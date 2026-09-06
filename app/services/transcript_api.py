"""Client for the YouTubeTranscripts.co REST API.

This is the only way the backend obtains a transcript. The API fetches a
video's native captions where it has them and falls back to Whisper where it
does not, so nothing here ever downloads media: no yt-dlp, no ffmpeg, no
residential proxy to get past YouTube's datacenter-IP blocking.
"""

import time
from typing import NamedTuple, Optional

import httpx

from app.core.config import get_settings
from app.utils.logger import get_logger

logger = get_logger(__name__)


class TranscriptAPIError(Exception):
    """Raised when a transcript cannot be obtained."""
    pass


class Transcript(NamedTuple):
    """A fetched transcript plus the metadata the API reported alongside it."""

    text: str
    video_id: Optional[str] = None
    title: Optional[str] = None
    channel: Optional[str] = None
    duration_seconds: Optional[int] = None
    language: Optional[str] = None
    cached: bool = False


# Terminal states of a transcript request; anything else means keep polling.
_DONE = "completed"
_FAILED = "failed"


class TranscriptAPIService:
    def fetch(self, url: str) -> Transcript:
        """Fetch the transcript for a YouTube URL, blocking until it is ready.

        The API is asynchronous: the POST enqueues a request and returns an id,
        which is then polled. A cached transcript can come back completed on the
        first response, so the polling loop checks before it sleeps.
        """
        settings = get_settings()
        if not settings.transcript_api_key:
            raise TranscriptAPIError(
                "TRANSCRIPT_API_KEY is not set. Create a key at "
                "https://youtubetranscripts.co/dashboard/api-keys and add it to your .env."
            )

        base_url = settings.transcript_api_base_url.rstrip("/")
        headers = {
            "Authorization": f"Bearer {settings.transcript_api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "url": url,
            "format": "text",
            "language": settings.transcript_language or "auto",
            "translate_to": settings.transcript_translate_to or "none",
            "native_only": settings.transcript_native_only,
        }

        started = time.monotonic()
        try:
            with httpx.Client(timeout=60.0) as client:
                response = client.post(
                    f"{base_url}/v1/transcript", json=payload, headers=headers
                )
                self._raise_for_status(response, "enqueue")
                job = response.json()

                request_id = job.get("id")
                deadline = started + settings.transcript_poll_timeout_seconds

                while True:
                    status = (job.get("status") or "").lower()

                    if status == _DONE:
                        break
                    if status == _FAILED:
                        raise TranscriptAPIError(
                            f"Transcript API failed for {url}: "
                            f"{job.get('error_message') or job.get('error_code') or 'unknown error'}"
                        )
                    if not request_id:
                        raise TranscriptAPIError(
                            f"Transcript API returned no request id for {url}: {job}"
                        )
                    if time.monotonic() >= deadline:
                        raise TranscriptAPIError(
                            f"Transcript API timed out after "
                            f"{settings.transcript_poll_timeout_seconds}s waiting for {url} "
                            f"(last status: {status or 'unknown'})"
                        )

                    time.sleep(settings.transcript_poll_interval_seconds)
                    poll = client.get(
                        f"{base_url}/v1/transcript/{request_id}", headers=headers
                    )
                    self._raise_for_status(poll, "poll")
                    job = poll.json()
        except TranscriptAPIError:
            raise
        except Exception as e:
            logger.error("transcript_api_request_failed", url=url, error=str(e))
            raise TranscriptAPIError(f"Transcript API request failed: {e}")

        result = job.get("result") or {}
        text = (result.get("transcript") or "").strip()
        if not text:
            # `format: text` is expected to fill `transcript`, but a segmented
            # result is just as usable, so rebuild the text rather than failing.
            text = "\n".join(
                (segment.get("text") or "").strip()
                for segment in (result.get("segments") or [])
            ).strip()
        if not text:
            raise TranscriptAPIError(f"Transcript API returned an empty transcript for {url}")

        transcript = Transcript(
            text=text,
            video_id=job.get("video_id"),
            title=job.get("title"),
            channel=job.get("channel"),
            duration_seconds=job.get("duration_seconds"),
            language=result.get("translated_to") or result.get("language"),
            cached=bool(result.get("cached")),
        )

        logger.info(
            "transcript_fetched",
            url=url,
            video_id=transcript.video_id,
            language=transcript.language,
            chars=len(text),
            cached=transcript.cached,
            elapsed_seconds=round(time.monotonic() - started, 1),
            credits_used=result.get("credits_used"),
            credits_remaining=result.get("credits_remaining"),
        )
        return transcript

    @staticmethod
    def _raise_for_status(response: httpx.Response, stage: str) -> None:
        """Turn an error response into a TranscriptAPIError carrying its body.

        The API explains itself in the body (bad key, out of credits, no
        captions), so that detail is worth surfacing to the user instead of a
        bare status code.
        """
        if response.status_code < 400:
            return

        detail = response.text[:500]
        logger.error(
            "transcript_api_rejected",
            stage=stage,
            status=response.status_code,
            detail=detail,
        )
        if response.status_code in (401, 403):
            raise TranscriptAPIError(
                f"Transcript API rejected the API key ({response.status_code}): {detail}"
            )
        if response.status_code == 429:
            raise TranscriptAPIError(
                f"Transcript API rate limit or credit limit reached: {detail}"
            )
        raise TranscriptAPIError(
            f"Transcript API returned {response.status_code}: {detail}"
        )
