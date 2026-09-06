"""Cheap YouTube metadata lookups: title, runtime, thumbnail.

Only metadata lives here. Transcripts come from the YouTubeTranscripts.co API
(see ``transcript_api``), and nothing in this codebase downloads media.

The runtime is fetched separately from the transcript because the free-plan
duration cap has to be enforced *before* a video is accepted, and a transcript
request costs credits.
"""

import re
from typing import Optional
from urllib.parse import urlparse, parse_qs

import httpx

from app.core.config import get_settings
from app.utils.logger import get_logger

logger = get_logger(__name__)


def extract_video_id(url: str) -> str:
    """Extract a YouTube video ID from a URL."""
    url = url.strip()
    parsed = urlparse(url)
    if parsed.hostname in ("youtu.be", "www.youtu.be"):
        return parsed.path[1:]
    if parsed.hostname in ("youtube.com", "www.youtube.com", "m.youtube.com"):
        if parsed.path == "/watch":
            qs = parse_qs(parsed.query)
            if "v" in qs:
                return qs["v"][0]
        if parsed.path.startswith("/embed/"):
            return parsed.path.split("/")[2]
        if parsed.path.startswith("/v/"):
            return parsed.path.split("/")[2]
        if parsed.path.startswith("/shorts/"):
            return parsed.path.split("/")[2]

    if "v=" in url:
        return url.split("v=")[1].split("&")[0]
    if "youtu.be/" in url:
        return url.split("youtu.be/")[1].split("?")[0]

    raise ValueError("Invalid YouTube URL")


def normalize_youtube_url(url: str) -> str:
    """Return a clean watch URL without sharing/tracking query params."""
    return f"https://www.youtube.com/watch?v={extract_video_id(url)}"


# The Data API reports runtimes as ISO 8601 durations ("PT15M39S"). Live streams
# and premieres can come back as a bare "P0D", so the time part is optional.
_ISO8601_DURATION = re.compile(
    r"^P(?:(?P<days>\d+)D)?"
    r"(?:T(?:(?P<hours>\d+)H)?(?:(?P<minutes>\d+)M)?(?:(?P<seconds>\d+)S)?)?$"
)


def parse_iso8601_duration(value: Optional[str]) -> Optional[int]:
    """Seconds from an ISO 8601 duration, or None if it is absent or malformed.

    Returns None rather than 0 for a zero-length duration: a live stream reports
    "P0D", and treating that as a genuinely zero-second video would let it slip
    past the free plan's duration cap.
    """
    match = _ISO8601_DURATION.match(value or "")
    if not match:
        return None
    parts = {key: int(raw or 0) for key, raw in match.groupdict().items()}
    seconds = (
        parts["days"] * 86400
        + parts["hours"] * 3600
        + parts["minutes"] * 60
        + parts["seconds"]
    )
    return seconds or None


class YouTubeMetadataService:
    def get_video_info(self, url: str) -> dict:
        """Fetch video metadata, cheapest and most reliable source first.

        1. The YouTube Data API, when a key is configured. It is Google's own
           API, so it answers a cloud host as happily as anywhere else, it
           reports the runtime directly, and one lookup costs a single unit of
           a 10,000/day free quota.
        2. oEmbed, for the title and thumbnail only - it does not report a
           runtime.

        ``duration`` stays None when neither source can say. The transcript API
        reports the runtime too, so a video accepted with an unknown duration
        still gets one recorded once its transcript comes back.
        """
        info = {"title": None, "duration": None, "thumbnail": None}

        from_api = self._fetch_via_data_api(url)
        if from_api:
            info.update(from_api)

        if not info["title"] or not info["thumbnail"]:
            oembed = self._fetch_oembed(url)
            if oembed:
                info["title"] = info["title"] or oembed.get("title")
                info["thumbnail"] = info["thumbnail"] or oembed.get("thumbnail")

        info["title"] = info["title"] or "YouTube Video"
        return info

    @staticmethod
    def _fetch_via_data_api(url: str) -> Optional[dict]:
        """Metadata from the YouTube Data API, or None when it cannot answer."""
        settings = get_settings()
        if not settings.youtube_api_key:
            return None

        try:
            video_id = extract_video_id(url)
        except ValueError as e:
            logger.warning("data_api_bad_url", url=url, error=str(e))
            return None

        try:
            response = httpx.get(
                "https://www.googleapis.com/youtube/v3/videos",
                params={
                    "part": "snippet,contentDetails",
                    "id": video_id,
                    "key": settings.youtube_api_key,
                },
                timeout=10.0,
            )
            if response.status_code != 200:
                logger.warning(
                    "data_api_rejected",
                    video_id=video_id,
                    status=response.status_code,
                    detail=response.text[:300],
                )
                return None

            items = response.json().get("items") or []
            if not items:
                # A private, deleted, or region-blocked video returns no items.
                logger.info("data_api_no_such_video", video_id=video_id)
                return None

            snippet = items[0].get("snippet") or {}
            details = items[0].get("contentDetails") or {}
            thumbnails = snippet.get("thumbnails") or {}
            best = (
                thumbnails.get("maxres")
                or thumbnails.get("standard")
                or thumbnails.get("high")
                or {}
            )

            duration = parse_iso8601_duration(details.get("duration"))
            logger.info("data_api_metadata", video_id=video_id, duration=duration)
            return {
                "title": snippet.get("title"),
                "duration": duration,
                "thumbnail": best.get("url"),
            }
        except Exception as e:
            logger.warning("data_api_failed", url=url, error=str(e))
            return None

    @staticmethod
    def _fetch_oembed(url: str) -> Optional[dict]:
        """Title and thumbnail from YouTube's oEmbed API; None if unavailable."""
        try:
            response = httpx.get(
                "https://www.youtube.com/oembed",
                params={"url": url, "format": "json"},
                timeout=10.0,
            )
            if response.status_code != 200:
                return None
            data = response.json()
            return {
                "title": data.get("title"),
                "thumbnail": data.get("thumbnail_url"),
            }
        except Exception as e:
            logger.warning("oembed_fetch_failed", url=url, error=str(e))
            return None
