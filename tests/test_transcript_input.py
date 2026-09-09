"""Tests for preparing transcripts the user supplies directly.

These cover the shapes a real upload arrives in - a SubRip file, a WebVTT file
with inline caption markup, and plain prose - because the cleaning is what
decides whether the model sees spoken words or a wall of timestamps.
"""

import pytest

from app.services import transcript_input

SRT = """1
00:00:00,120 --> 00:00:03,400
Welcome to the lecture on distributed

2
00:00:03,400 --> 00:00:06,900
systems. Today we cover consensus
and how replicas agree.

3
00:00:06,900 --> 00:00:09,000
and how replicas agree.
"""

VTT = """WEBVTT
Kind: captions
Language: en

00:00:01.000 --> 00:00:04.000 align:start position:0%
Hello<00:00:02.000><c> everyone</c> welcome

00:00:04.000 --> 00:00:07.000
<v Speaker>to the second part</v>
"""


def test_srt_cues_become_flowing_text():
    text, was_caption = transcript_input.clean_transcript(SRT)

    assert was_caption
    # Cue numbers and timings are gone, and a sentence split across two cues
    # is put back together rather than broken into paragraphs.
    assert "-->" not in text
    assert "Welcome to the lecture on distributed systems." in text
    # The rolling-caption repeat of the previous cue's line appears only once.
    assert text.count("and how replicas agree") == 1


def test_vtt_header_and_inline_markup_are_stripped():
    text, was_caption = transcript_input.clean_transcript(VTT)

    assert was_caption
    assert text == "Hello everyone welcome to the second part"


def test_plain_prose_keeps_its_structure():
    prose = (
        "Intro paragraph about the topic.\n"
        "\n"
        "1. First numbered point that should survive.\n"
        "2. Second one.\n"
    )
    text, was_caption = transcript_input.clean_transcript(prose)

    assert not was_caption
    # A digits-only line is only a cue number when a timing line follows it,
    # so numbered points in real notes are left alone.
    assert "1. First numbered point that should survive." in text
    assert "\n\n" in text


def test_leading_timestamps_in_plain_transcripts_are_dropped():
    text, _ = transcript_input.clean_transcript("[00:12:30] The speaker begins.")

    assert text == "The speaker begins."


def test_duration_is_estimated_from_word_count():
    prepared = transcript_input.prepare("word " * 1500)

    assert prepared.word_count == 1500
    # 1500 words at 150 words per minute is ten minutes.
    assert prepared.estimated_duration_seconds == 600


def test_short_transcript_is_rejected():
    with pytest.raises(transcript_input.TranscriptInputError, match="too short"):
        transcript_input.prepare("Not enough text here.")


def test_empty_transcript_is_rejected():
    with pytest.raises(transcript_input.TranscriptInputError, match="empty"):
        transcript_input.prepare("   \n  ")


def test_oversized_transcript_is_rejected():
    oversized = "x" * (transcript_input.MAX_TRANSCRIPT_CHARS + 1)

    with pytest.raises(transcript_input.TranscriptInputError, match="character limit"):
        transcript_input.prepare(oversized)


def test_caption_file_that_is_only_scaffolding_is_rejected():
    # A file whose cues are all empty cleans down to nothing, and should fail
    # the length check rather than reaching the model as an empty prompt.
    with pytest.raises(transcript_input.TranscriptInputError, match="too short"):
        transcript_input.prepare("WEBVTT\n\n00:00:01.000 --> 00:00:04.000\n\n")
