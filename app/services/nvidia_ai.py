import time
from typing import Optional

from openai import APIConnectionError, APIError, OpenAI, RateLimitError
from openai import APITimeoutError

from app.core.config import get_settings
from app.utils.logger import get_logger

logger = get_logger(__name__)

MAX_RETRIES = 3
RETRY_DELAY_SECONDS = 2
REQUEST_TIMEOUT = 120


class NvidiaAIError(Exception):
    """Raised when an NVIDIA NIM API call fails."""

    pass


class NvidiaAIRateLimitError(NvidiaAIError):
    """Raised when NVIDIA NIM API rate limit is hit."""

    pass


class NvidiaAITimeoutError(NvidiaAIError):
    """Raised when the NVIDIA NIM API request times out."""

    pass


class NvidiaAIService:
    """Service for AI note generation using NVIDIA NIM API (OpenAI-compatible)."""

    def __init__(self) -> None:
        settings = get_settings()
        self.api_key = settings.nvidia_api_key
        self.base_url = settings.nvidia_base_url
        self.model = settings.nvidia_model
        self._client: Optional[OpenAI] = None

    def _get_client(self) -> OpenAI:
        """Lazy-initialize the OpenAI client. Defers API-key validation
        until the first actual API call."""
        if self._client is not None:
            return self._client

        if not self.api_key:
            raise NvidiaAIError(
                "NVIDIA_API_KEY is not set. Please configure it in your .env file."
            )

        import httpx
        self._client = OpenAI(
            api_key=self.api_key,
            base_url=self.base_url,
            timeout=REQUEST_TIMEOUT,
            max_retries=0,  # We handle retries ourselves
            http_client=httpx.Client(
                transport=httpx.HTTPTransport(local_address="0.0.0.0")
            )
        )
        return self._client

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def generate_notes(self, transcript: str, title: str = "Video") -> str:
        """Generate a complete set of structured markdown notes from a transcript."""
        logger.info("generating_notes", title=title)

        prompt = f"""You are an expert educational AI tutor. 

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

# {title} - Comprehensive Study Notes

## AI Topic Analysis & Context
Write a brief overview explaining the broader importance of the topic and provide extra context or background knowledge not explicitly mentioned in the video.

## Summary (Mixed Content)
Write 2-3 concise paragraphs covering the main idea and key arguments. Combine the transcript's points with your own expert understanding.

## Key Points & Expert Additions
List 8-12 important takeaways. For each point, include what the video said PLUS a brief added insight, example, or clarification from your own knowledge. Bold the core concept.

## Domain-Specific Content (Code / Math Solutions / Theory)
Depending on the video type:
- If Coding: Provide the corrected, well-documented code examples discussed.
- If Math: Provide the step-by-step mathematical solutions using LaTeX.
- If Theory: Provide a deep-dive into the theoretical concepts and frameworks discussed.

## Chapter Breakdown
Break the topic into logical chapters/topics. Use ### chapter headings and 2-3 bullets per chapter. 

## Action Items & Further Learning
List practical next steps, revision tasks, or recommended concepts to explore further."""

        markdown = self._call_llm(prompt, temperature=0.3)

        logger.info("notes_generated", title=title, model=self.model)
        return markdown

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
        """Make an OpenAI-compatible chat completion call to the NVIDIA NIM API.

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
                    raise NvidiaAIError("LLM returned empty response")
                return content.strip()

            except RateLimitError as e:
                last_exception = NvidiaAIRateLimitError(
                    f"NVIDIA NIM rate limit exceeded (attempt {attempt}/{MAX_RETRIES}): {e}"
                )
                logger.warning(
                    "rate_limit_hit",
                    attempt=attempt,
                    max_retries=MAX_RETRIES,
                    error=str(e),
                )
                if attempt < MAX_RETRIES:
                    self._wait(attempt)

            except APITimeoutError as e:
                last_exception = NvidiaAITimeoutError(
                    f"NVIDIA NIM request timed out (attempt {attempt}/{MAX_RETRIES}): {e}"
                )
                logger.warning(
                    "request_timeout",
                    attempt=attempt,
                    max_retries=MAX_RETRIES,
                    error=str(e),
                )
                if attempt < MAX_RETRIES:
                    self._wait(attempt)

            except APIConnectionError as e:
                last_exception = NvidiaAIError(
                    f"NVIDIA NIM connection error (attempt {attempt}/{MAX_RETRIES}): {e}"
                )
                logger.warning(
                    "connection_error",
                    attempt=attempt,
                    max_retries=MAX_RETRIES,
                    error=str(e),
                    base_url=self.base_url,
                )
                if attempt < MAX_RETRIES:
                    self._wait(attempt)
                else:
                    raise last_exception

            except APIError as e:
                last_exception = NvidiaAIError(
                    f"NVIDIA NIM API error (attempt {attempt}/{MAX_RETRIES}): {e}"
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
                last_exception = NvidiaAIError(
                    f"Unexpected NVIDIA NIM error (attempt {attempt}/{MAX_RETRIES}): {e}"
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
    def _wait(attempt: int) -> None:
        time.sleep(RETRY_DELAY_SECONDS * attempt)
