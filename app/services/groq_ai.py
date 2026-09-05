import re
import time
from typing import Optional

from groq import Groq, APIError, RateLimitError, APITimeoutError

from app.core.config import get_settings
from app.utils.logger import get_logger

logger = get_logger(__name__)

MAX_RETRIES = 3
RETRY_DELAY_SECONDS = 2
REQUEST_TIMEOUT = 120
# Groq's free tier resets its tokens-per-minute budget on a ~60s window, so a
# fixed two-second backoff always retries into the same wall. Wait as long as
# the API asks, up to this ceiling.
MAX_RETRY_AFTER_SECONDS = 75

_RETRY_HINT = re.compile(r"try again in ([\d.]+)\s*s", re.IGNORECASE)
_DURATION = re.compile(r"^\s*([\d.]+)\s*(ms|s|m)?\s*$", re.IGNORECASE)


def _parse_duration(value: str) -> Optional[float]:
    match = _DURATION.match(value)
    if not match:
        return None
    amount = float(match.group(1))
    unit = (match.group(2) or "s").lower()
    if unit == "ms":
        return amount / 1000
    if unit == "m":
        return amount * 60
    return amount


def retry_after_seconds(error: Exception) -> Optional[float]:
    """How long the API asked us to wait, from headers or the error message."""
    response = getattr(error, "response", None)
    headers = getattr(response, "headers", None)
    if headers:
        for name in ("retry-after", "x-ratelimit-reset-tokens", "x-ratelimit-reset-requests"):
            raw = headers.get(name)
            if raw and (seconds := _parse_duration(str(raw))) is not None:
                return min(seconds, MAX_RETRY_AFTER_SECONDS)

    hint = _RETRY_HINT.search(str(error))
    if hint:
        return min(float(hint.group(1)), MAX_RETRY_AFTER_SECONDS)
    return None

# The exact refusal wording the note prompt asks for when a video is not
# educational; used to short-circuit multi-segment generation.
NOT_EDUCATIONAL_MARKER = "does not appear to contain educational content"

_HEADING = re.compile(r"^(#{1,5})(\s)", re.MULTILINE)
_FENCE = re.compile(r"^(```|~~~)", re.MULTILINE)


def demote_headings(markdown: str) -> str:
    """Push every heading down one level so parts nest under a document title.

    Headings inside fenced code blocks are left alone - a leading '#' there is
    a comment, not a heading.
    """
    out: list[str] = []
    in_fence = False
    for line in markdown.split("\n"):
        if _FENCE.match(line):
            in_fence = not in_fence
            out.append(line)
            continue
        out.append(line if in_fence else _HEADING.sub(r"#\1\2", line, count=1))
    return "\n".join(out)


def split_transcript(transcript: str, max_chars: int, max_chunks: int) -> list[str]:
    """Split a transcript into ordered slices of at most ``max_chars``.

    Splits on line boundaries so a sentence is not cut in half. When the
    transcript would need more than ``max_chunks`` slices the budget is widened
    instead, capping the number of LLM calls a single video can trigger.
    """
    text = transcript.strip()
    if len(text) <= max_chars:
        return [text]

    budget = max_chars
    if max_chunks > 0:
        needed = -(-len(text) // max_chars)  # ceil
        if needed > max_chunks:
            budget = -(-len(text) // max_chunks)

    def pack(limit: int) -> list[str]:
        units: list[str] = []
        for line in text.splitlines(keepends=True):
            # A caption line is normally short, but guard against one huge blob.
            while len(line) > limit:
                units.append(line[:limit])
                line = line[limit:]
            if line:
                units.append(line)

        chunks: list[str] = []
        current: list[str] = []
        size = 0
        for unit in units:
            if size + len(unit) > limit and current:
                chunks.append("".join(current).strip())
                current, size = [], 0
            current.append(unit)
            size += len(unit)
        if current:
            chunks.append("".join(current).strip())
        return [chunk for chunk in chunks if chunk]

    result = pack(budget)
    # Packing on line boundaries leaves slack, so the first estimate can still
    # overshoot the cap. Widen until it fits.
    while max_chunks > 0 and len(result) > max_chunks:
        budget = int(budget * 1.15) + 1
        result = pack(budget)

    return result


class GroqAIError(Exception):
    """Raised when a Groq API call fails."""
    pass


class GroqAIRateLimitError(GroqAIError):
    """Raised when Groq API rate limit is hit."""
    pass


class GroqAITimeoutError(GroqAIError):
    """Raised when the Groq API request times out."""
    pass


class GroqAIService:
    """Service for AI note generation using Groq API."""

    def __init__(self) -> None:
        settings = get_settings()
        self.api_key = settings.groq_api_key
        self.model = settings.groq_model
        self.chunk_chars = settings.notes_chunk_chars
        self.max_chunks = settings.notes_max_chunks
        self.merge_chars = settings.notes_merge_chars
        self._client: Optional[Groq] = None

    def _get_client(self) -> Groq:
        """Lazy-initialize the Groq client."""
        if self._client is not None:
            return self._client

        if not self.api_key:
            raise GroqAIError(
                "GROQ_API_KEY is not set. Please configure it in your .env file."
            )

        self._client = Groq(
            api_key=self.api_key,
            timeout=REQUEST_TIMEOUT,
            max_retries=0,  # We handle retries ourselves
        )
        return self._client

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def generate_notes(self, transcript: str, title: str = "Video") -> str:
        """Generate structured markdown notes covering the *whole* transcript.

        Short transcripts go through in a single call. Longer ones are split
        into ordered segments, each turned into notes, and then merged - so a
        long video produces notes for its full runtime instead of only the
        opening minutes.
        """
        segments = split_transcript(transcript, self.chunk_chars, self.max_chunks)

        if len(segments) == 1:
            logger.info("generating_notes", title=title, segments=1)
            return self._generate_segment_notes(segments[0], title, 1, 1)

        logger.info(
            "generating_notes",
            title=title,
            segments=len(segments),
            transcript_chars=len(transcript),
        )

        parts: list[str] = []
        for index, segment in enumerate(segments, start=1):
            notes = self._generate_segment_notes(segment, title, index, len(segments))
            # A refusal on the opening segment means the whole video was judged
            # non-educational; there is nothing to merge.
            if index == 1 and NOT_EDUCATIONAL_MARKER in notes:
                return notes
            parts.append(notes)
            logger.info("segment_notes_generated", title=title, segment=index, of=len(segments))

        return self._merge_segment_notes(parts, title)

    def _generate_segment_notes(
        self, transcript: str, title: str, index: int, total: int
    ) -> str:
        """Produce notes for one slice of the transcript."""
        if total > 1:
            scope = f"""
This transcript is PART {index} of {total} of one long video. Write notes for
THIS part only - do not speculate about the other parts and do not add an
introduction or conclusion for the video as a whole. Cover this part in full;
do not stop early or summarise it away.
"""
            heading = f"## Part {index} of {total}"
            h2 = "###"
        else:
            scope = ""
            heading = f"# {title} - Study Notes"
            h2 = "##"

        prompt = f"""You are an expert educational AI tutor.
{scope}

FIRST, analyze the video transcript and classify it. If the video is NOT educational (e.g., entertainment, music, vlogs, random chatter), you MUST refuse to process it by returning ONLY this exact message:
"This video does not appear to contain educational content. I can only generate notes for coding, math, or theoretical videos."

If the video IS educational, create complete, high-quality study notes based on this video transcript.
Mix the content from the video transcript WITH your own expert knowledge and useful insights to provide a richer, more educational experience.

Video Title: {title}

Transcript:
{transcript}

IMPORTANT DOMAIN-SPECIFIC RULES:
- For Coding/Programming Videos: You MUST include code-related content. If the video's code has errors or is incomplete, provide the corrected and complete code.
- For Math Videos: You MUST provide step-by-step mathematical solutions and explanations. Use proper LaTeX notation ($...$ for inline, $$...$$ for block). Convert spoken math into formulas.
- For Theory Videos: Provide deep theoretical explanations, good theoretical context, and clarify any abstract concepts with strong examples.

Return clean markdown with exactly these sections:

{heading}

{h2} Key Points
List the most important takeaways and core concepts from the video. Bold the core concepts.

{h2} Detailed Explanations & Solutions
Depending on the video type:
- If Coding: Provide the corrected, well-documented code examples discussed.
- If Math: Provide the step-by-step mathematical solutions. **CRITICAL: You MUST use block LaTeX notation ($$ equation $$) for all equations so they appear clearly on their own separate lines. Do NOT put multiple equations on a single line using inline math.**
- If Theory: Provide a deep-dive into the theoretical concepts and frameworks discussed."""

        markdown = self._call_llm(prompt, temperature=0.3)

        logger.info("segment_notes_ready", title=title, segment=index, of=total, model=self.model)
        return markdown

    def _merge_segment_notes(self, parts: list[str], title: str) -> str:
        """Fold per-segment notes into one document covering the whole video."""
        combined = "\n\n".join(
            f"### Notes from part {index} of {len(parts)}\n\n{part}"
            for index, part in enumerate(parts, start=1)
        )

        # Consolidating with the LLM gives the nicest result, but only when the
        # per-part notes still fit comfortably in one request.
        if len(combined) <= self.merge_chars:
            prompt = f"""You are an expert editor assembling one set of study notes for a full-length video.

Below are notes written independently for consecutive parts of the SAME video, in order.
Merge them into ONE coherent document.

Rules:
- Keep every distinct fact, example, formula and code block. Do NOT drop content to save space.
- Remove duplicated points, and put related material together.
- Preserve the original order of the video's material.
- Keep all LaTeX exactly as written ($...$ inline, $$...$$ display), and keep code fences intact.

Video Title: {title}

{combined}

Return clean markdown with exactly these sections:

# {title} - Study Notes

## Key Points
The most important takeaways across the entire video. Bold the core concepts.

## Detailed Explanations & Solutions
The full detailed material from every part, in video order."""

            try:
                merged = self._call_llm(prompt, temperature=0.2)
                logger.info("segment_notes_merged", title=title, parts=len(parts), mode="llm")
                return merged
            except GroqAIError as e:
                # A failed merge must not throw away notes we already paid for.
                logger.warning("segment_merge_failed_using_concatenation", title=title, error=str(e))

        logger.info("segment_notes_merged", title=title, parts=len(parts), mode="concatenated")
        return self._concatenate_segment_notes(parts, title)

    @staticmethod
    def _concatenate_segment_notes(parts: list[str], title: str) -> str:
        """Fallback merge: stitch the parts together without another LLM call."""
        sections = [f"# {title} - Study Notes", ""]
        for index, part in enumerate(parts, start=1):
            sections.append(f"## Part {index} of {len(parts)}")
            sections.append("")
            sections.append(demote_headings(part))
            sections.append("")
        return "\n".join(sections).strip() + "\n"

    def summarize_transcript(self, transcript: str, title: str = "Video") -> str:
        """Return only the transcript summary."""
        return self._summarize_transcript(transcript, title)

    def extract_chapters(self, transcript: str) -> str:
        """Return only the chapter/topic breakdown."""
        return self._extract_chapters(transcript)

    def extract_key_points(self, transcript: str, title: str = "Video") -> str:
        """Return only the key points."""
        return self._extract_key_points(transcript, title)

    def extract_action_items(self, transcript: str, title: str = "Video") -> str:
        """Return only the action items."""
        return self._extract_action_items(transcript, title)

    # ------------------------------------------------------------------
    # Private sub-tasks
    # ------------------------------------------------------------------

    def _summarize_transcript(self, transcript: str, title: str) -> str:
        prompt = f"""You are an expert summarizer. Provide a concise 2-3 paragraph summary of the following video.

Video Title: {title}

Transcript:
{transcript}

Write a clear, informative summary that captures the main thesis, key arguments, and conclusions."""
        return self._call_llm(prompt, temperature=0.3)

    def _extract_chapters(self, transcript: str) -> str:
        prompt = f"""Analyze the following transcript and break it into logical chapters/topics.

For each chapter provide:
- **Chapter title** (descriptive name)
- **Timestamp range** if mentioned, otherwise approximate based on content flow
- **Key topics covered** (2-3 bullet points)

Transcript:
{transcript}

Format as a clean markdown list with ### for chapter titles."""
        return self._call_llm(prompt, temperature=0.2)

    def _extract_key_points(self, transcript: str, title: str) -> str:
        prompt = f"""Extract the most important key points and takeaways from the following video transcript.

Video Title: {title}

Transcript:
{transcript}

Requirements:
- List 5-10 key points
- Each point should be a concise, standalone statement
- Bold the most critical term or concept in each point
- Cover the full scope of the video

Format as a bullet-point list with **bold** for emphasis."""
        return self._call_llm(prompt, temperature=0.3)

    def _extract_action_items(self, transcript: str, title: str) -> str:
        prompt = f"""Based on the following video transcript, identify actionable items, next steps, or concrete takeaways the viewer should act on.

Video Title: {title}

Transcript:
{transcript}

Include:
- **Action item** — what to do
- **Why it matters** — brief justification from the video

If the video does not contain explicit action items, extrapolate reasonable follow-up actions based on the content."""

        return self._call_llm(prompt, temperature=0.4)

    # ------------------------------------------------------------------
    # Assembly
    # ------------------------------------------------------------------

    def _assemble_markdown(
        self,
        title: str,
        summary: str,
        chapters: str,
        key_points: str,
        action_items: str,
    ) -> str:
        sections = [
            f"# {title} — AI Study Notes",
            "",
            "",
            "",
            "---",
            "",
            "## Summary",
            "",
            summary,
            "",
            "---",
            "",
            "## Key Points",
            "",
            key_points,
            "",
            "---",
            "",
            "## Chapter Breakdown",
            "",
            chapters,
            "",
            "---",
            "",
            "## Action Items",
            "",
            action_items,
            "",
            "---",
            "",
            "",
            "",
        ]
        return "\n".join(sections)

    # ------------------------------------------------------------------
    # LLM Call
    # ------------------------------------------------------------------

    def _call_llm(self, prompt: str, temperature: float = 0.3) -> str:
        """Make a chat completion call to the Groq API.

        Implements retry logic with exponential back-off for transient failures.
        """
        last_exception: Optional[Exception] = None

        for attempt in range(1, MAX_RETRIES + 1):
            try:
                response = self._get_client().chat.completions.create(
                    model=self.model,
                    messages=[
                        {
                            "role": "system",
                            "content": (
                                "You are a helpful, precise assistant that produces high-quality structured notes from video transcripts. "
                                "You are especially skilled at handling mathematical, scientific, and technical content. "
                                "When the transcript contains mathematical concepts, always convert spoken math into proper LaTeX notation "
                                "using $...$ for inline math and $$...$$ for display equations. "
                                "Ensure all formulas, theorems, and equations are accurately represented."
                            ),
                        },
                        {"role": "user", "content": prompt},
                    ],
                    temperature=temperature,
                    max_tokens=4000,
                )
                content = response.choices[0].message.content
                if content is None:
                    raise GroqAIError("LLM returned empty response")
                return content.strip()

            except RateLimitError as e:
                last_exception = GroqAIRateLimitError(
                    f"Groq rate limit exceeded (attempt {attempt}/{MAX_RETRIES}): {e}"
                )
                cooldown = retry_after_seconds(e)
                logger.warning(
                    "rate_limit_hit",
                    attempt=attempt,
                    max_retries=MAX_RETRIES,
                    retry_after=cooldown,
                    error=str(e),
                )
                if attempt < MAX_RETRIES:
                    self._wait(attempt, cooldown)

            except APITimeoutError as e:
                last_exception = GroqAITimeoutError(
                    f"Groq request timed out (attempt {attempt}/{MAX_RETRIES}): {e}"
                )
                logger.warning(
                    "request_timeout",
                    attempt=attempt,
                    max_retries=MAX_RETRIES,
                    error=str(e),
                )
                if attempt < MAX_RETRIES:
                    self._wait(attempt)

            except APIError as e:
                last_exception = GroqAIError(
                    f"Groq API error (attempt {attempt}/{MAX_RETRIES}): {e}"
                )
                logger.error(
                    "api_error",
                    attempt=attempt,
                    max_retries=MAX_RETRIES,
                    error=str(e),
                    status_code=getattr(e, "status_code", None),
                )
                if attempt < MAX_RETRIES and getattr(e, "status_code", 0) >= 500:
                    self._wait(attempt)
                else:
                    raise last_exception

            except Exception as e:
                last_exception = GroqAIError(
                    f"Unexpected Groq error (attempt {attempt}/{MAX_RETRIES}): {e}"
                )
                logger.error(
                    "unexpected_error",
                    attempt=attempt,
                    error=str(e),
                )
                if attempt < MAX_RETRIES:
                    self._wait(attempt)
                else:
                    raise last_exception

        raise last_exception  # type: ignore[misc]

    @staticmethod
    def _wait(attempt: int, retry_after: Optional[float] = None) -> None:
        # Respect the server's own cooldown when it gives one; otherwise back
        # off linearly.
        delay = retry_after if retry_after is not None else RETRY_DELAY_SECONDS * attempt
        time.sleep(max(delay, 0) + 1)
