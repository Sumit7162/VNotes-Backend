"""Preparation of transcripts the user supplies directly.

The YouTube path gets a clean block of text back from the transcript API. A
transcript the user uploads or pastes arrives in whatever shape their source
produced it, most often a caption file: SubRip (.srt) and WebVTT (.vtt) both
interleave the spoken words with cue numbers, timestamps and markup that would
otherwise be fed to the model as if they were content.

Everything here is format detection by inspection rather than by file
extension, because the frontend reads the file in the browser and sends only
its text - and because a file named .txt is quite often a caption dump anyway.
"""

import re
from typing import NamedTuple

from app.utils.logger import get_logger

logger = get_logger(__name__)

# Below this there is not enough material for the model to write notes from;
# it is almost always a mis-paste. The upper bound is a guard against a user
# pasting a whole book: notes generation slices the text into chunks and the
# chunk cap would silently drop the tail, so refuse clearly instead.
MIN_TRANSCRIPT_CHARS = 200
MAX_TRANSCRIPT_CHARS = 600_000

# Average speaking rate used to estimate how long the recording behind a
# transcript ran. A transcript carries no runtime of its own, but the free
# plan's limits are expressed in minutes, so one has to be inferred.
WORDS_PER_MINUTE = 150

# "00:00:01,000 --> 00:00:04,000", with or without the hour field, and with
# either the SRT comma or the VTT dot before the milliseconds. Trailing cue
# settings ("align:start position:0%") are part of the same line in VTT.
_CUE_TIMING = re.compile(
    r"^\s*(?:\d{1,2}:)?\d{1,2}:\d{2}[.,]\d{1,3}\s*-->\s*(?:\d{1,2}:)?\d{1,2}:\d{2}[.,]\d{1,3}.*$"
)
# A bare "00:01:23" or "[00:01:23]" opening a line: the shape most plain-text
# transcript exports use instead of real cues.
_LEADING_STAMP = re.compile(r"^\s*[\[(]?\s*(?:\d{1,2}:)?\d{1,2}:\d{2}(?:[.,]\d{1,3})?\s*[\])]?\s*[-–:]?\s*")
_CUE_INDEX = re.compile(r"^\s*\d{1,6}\s*$")
_VTT_HEADER = re.compile(r"^\s*(WEBVTT|Kind:|Language:|NOTE\b|STYLE\b|REGION\b)", re.IGNORECASE)
# Inline caption markup: karaoke timestamps (<00:00:02.000>), class and voice
# spans (<c.colorE5E5E5>, <v Speaker>) and the basic style tags.
_INLINE_TAG = re.compile(
    r"</?c[^>]*>|</?v[^>]*>|</?(?:i|b|u|ruby|rt)>|<\d{1,2}:\d{2}:\d{2}[.,]\d{1,3}>",
    re.IGNORECASE,
)


class PreparedTranscript(NamedTuple):
    """A cleaned transcript and what could be worked out about it."""

    text: str
    word_count: int
    estimated_duration_seconds: int
    was_caption_file: bool


class TranscriptInputError(Exception):
    """Raised when a supplied transcript cannot be used."""
    pass


def clean_transcript(raw: str) -> tuple[str, bool]:
    """Strip caption scaffolding from ``raw`` and return the spoken text.

    Returns the cleaned text along with whether the input looked like a caption
    file, which is only used for logging. Plain prose passes through with its
    paragraph breaks intact.
    """
    lines = raw.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    kept: list[str] = []
    was_caption_file = False
    previous = None

    for index, line in enumerate(lines):
        if _CUE_TIMING.match(line):
            was_caption_file = True
            continue
        if _VTT_HEADER.match(line):
            was_caption_file = True
            continue
        # A digits-only line is an SRT cue number only when a timing line
        # follows it; on its own it could be a numbered point in real notes.
        if _CUE_INDEX.match(line) and _next_line_is_timing(lines, index):
            was_caption_file = True
            continue

        text = _INLINE_TAG.sub("", line)
        text = _LEADING_STAMP.sub("", text)
        text = text.strip()

        if not text:
            # Collapse the blank line that separates every cue, but keep one
            # blank line so genuine paragraph breaks in prose survive.
            if kept and kept[-1] != "":
                kept.append("")
            continue

        # Rolling captions repeat the previous cue's last line as the new
        # cue's first line; without this the notes see everything twice.
        if text == previous:
            continue

        kept.append(text)
        previous = text

    # A caption file has no paragraphs: every cue is followed by a blank line
    # and cues routinely break mid-sentence, so honouring those breaks would
    # scatter one sentence across several "paragraphs". Join the whole thing
    # into flowing text instead. Plain prose keeps the breaks it came with.
    if was_caption_file:
        cleaned = " ".join(line for line in kept if line).strip()
    else:
        cleaned = "\n".join(kept).strip()

    return cleaned, was_caption_file


def _next_line_is_timing(lines: list[str], index: int) -> bool:
    """Whether the next non-empty line after ``index`` is a cue timing line."""
    for line in lines[index + 1 : index + 4]:
        if not line.strip():
            continue
        return bool(_CUE_TIMING.match(line))
    return False


def estimate_duration_seconds(text: str) -> int:
    """Estimate the runtime of the recording this transcript came from."""
    words = len(text.split())
    return round(words / WORDS_PER_MINUTE * 60)


def prepare(raw: str) -> PreparedTranscript:
    """Clean and validate a user-supplied transcript.

    Raises ``TranscriptInputError`` with a message meant for the user when the
    text is empty, too short to be worth summarising, or too long to process.
    """
    if not raw or not raw.strip():
        raise TranscriptInputError("The transcript is empty.")

    if len(raw) > MAX_TRANSCRIPT_CHARS:
        raise TranscriptInputError(
            f"The transcript is {len(raw):,} characters, which is over the "
            f"{MAX_TRANSCRIPT_CHARS:,} character limit. Split it into parts and "
            "upload them separately."
        )

    cleaned, was_caption_file = clean_transcript(raw)

    if len(cleaned) < MIN_TRANSCRIPT_CHARS:
        raise TranscriptInputError(
            f"The transcript is too short to make notes from - it needs at least "
            f"{MIN_TRANSCRIPT_CHARS} characters of text, and this one has {len(cleaned)}."
        )

    word_count = len(cleaned.split())
    return PreparedTranscript(
        text=cleaned,
        word_count=word_count,
        estimated_duration_seconds=estimate_duration_seconds(cleaned),
        was_caption_file=was_caption_file,
    )
