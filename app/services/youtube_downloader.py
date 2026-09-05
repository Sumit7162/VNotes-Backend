import httpx
import time
from pathlib import Path
from typing import NamedTuple, Optional
from urllib.parse import urlparse, parse_qs
# pyrefly: ignore [missing-import]
from youtube_transcript_api import YouTubeTranscriptApi
# pyrefly: ignore [missing-import]
from youtube_transcript_api.formatters import TextFormatter

from app.utils.logger import get_logger

logger = get_logger(__name__)


class DownloadError(Exception):
    """Raised when video fetch fails."""
    pass


class DownloadedAudio(NamedTuple):
    """Where the audio landed, plus the runtime the download reported."""

    path: str
    duration_seconds: Optional[int] = None


def extract_video_id(url: str) -> str:
    """Extract YouTube video ID from URL."""
    url = url.strip()
    parsed = urlparse(url)
    if parsed.hostname in ('youtu.be', 'www.youtu.be'):
        return parsed.path[1:]
    if parsed.hostname in ('youtube.com', 'www.youtube.com'):
        if parsed.path == '/watch':
            qs = parse_qs(parsed.query)
            if 'v' in qs:
                return qs['v'][0]
        if parsed.path.startswith('/embed/'):
            return parsed.path.split('/')[2]
        if parsed.path.startswith('/v/'):
            return parsed.path.split('/')[2]
    
    if "v=" in url:
        return url.split("v=")[1].split("&")[0]
    elif "youtu.be/" in url:
        return url.split("youtu.be/")[1].split("?")[0]
        
    raise ValueError("Invalid YouTube URL")


def normalize_youtube_url(url: str) -> str:
    """Return a clean watch URL without sharing/tracking query params."""
    video_id = extract_video_id(url)
    return f"https://www.youtube.com/watch?v={video_id}"


class YouTubeDownloaderService:
    def get_video_info(self, url: str) -> dict:
        """Fetch video metadata: runtime from the downloader service, title from oEmbed.

        The runtime matters - it is what the free-plan duration cap and the
        usage counters are measured in - and YouTube's oEmbed API does not
        report it, so the downloader service is asked to probe the video with
        yt-dlp. oEmbed still fills in the title because it answers in
        milliseconds and does not trip YouTube's bot checks. ``duration`` stays
        None when neither source can say, and the UI hides an unknown duration
        rather than showing a made-up one.
        """
        info = {"title": None, "duration": None, "thumbnail": None}

        probed = self._probe_via_downloader_service(url)
        if probed:
            info.update(
                {
                    "title": probed.get("title") or info["title"],
                    "duration": probed.get("duration"),
                    "thumbnail": probed.get("thumbnail") or info["thumbnail"],
                }
            )

        oembed = self._fetch_oembed(url)
        if oembed:
            info["title"] = oembed.get("title") or info["title"]
            info["thumbnail"] = info["thumbnail"] or oembed.get("thumbnail")

        info["title"] = info["title"] or "YouTube Video"
        return info

    def _probe_via_downloader_service(self, url: str) -> Optional[dict]:
        """Ask the downloader service for yt-dlp metadata; None if it cannot say."""
        from app.core.config import get_settings

        downloader_url = get_settings().downloader_url.rstrip("/")
        try:
            response = httpx.post(
                f"{downloader_url}/info", json={"url": url}, timeout=45.0
            )
            if response.status_code >= 400:
                logger.warning(
                    "video_info_probe_rejected",
                    url=url,
                    status=response.status_code,
                    detail=response.text[:300],
                )
                return None
            return response.json()
        except Exception as e:
            logger.warning("video_info_probe_failed", url=url, error=str(e))
            return None

    @staticmethod
    def _fetch_oembed(url: str) -> Optional[dict]:
        """Title and thumbnail from YouTube's oEmbed API; None if unavailable."""
        try:
            oembed_url = f"https://www.youtube.com/oembed?url={url}&format=json"
            response = httpx.get(oembed_url, timeout=10.0)
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

    def get_transcript(self, url: str) -> str:
        """Fetch YouTube auto-generated captions directly."""
        video_id = extract_video_id(url)
        try:
            # Check if a cookies.txt file exists to bypass YouTube's bot detection
            import os
            cookies_file = "cookies.txt"
            cookies_path = cookies_file if os.path.exists(cookies_file) else None
            
            http_client = None
            if cookies_path:
                import http.cookiejar
                import requests
                try:
                    cookie_jar = http.cookiejar.MozillaCookieJar(cookies_path)
                    cookie_jar.load(ignore_discard=True, ignore_expires=True)
                    http_client = requests.Session()
                    http_client.cookies.update(cookie_jar)
                except Exception as e:
                    logger.warning("failed_to_load_cookies", error=str(e))
                    http_client = None

            # New version (e.g. 1.2.4) uses http_client parameter for cookies
            fetcher = YouTubeTranscriptApi(http_client=http_client) if http_client else YouTubeTranscriptApi()
            transcript_list = fetcher.list(video_id)
            
            # Fetch the first available transcript (regardless of language)
            first_transcript = list(transcript_list)[0]
            
            # If it's not English, translate it to English
            if first_transcript.language_code != 'en':
                first_transcript = first_transcript.translate('en')
                
            raw_transcript = first_transcript.fetch()
            
            formatter = TextFormatter()
            transcript = formatter.format_transcript(raw_transcript)
            
            logger.info("transcript_fetched", video_id=video_id)
            return transcript
        except Exception as e:
            logger.error("transcript_fetch_failed", url=url, error=str(e))
            raise DownloadError(f"Failed to fetch transcript: {e}")

    def download_audio(self, url: str, output_path: str) -> DownloadedAudio:
        """Download audio from YouTube via Video Download API or downloader service.

        Both paths return a compressed mp3 rather than PCM wav: the audio only
        ever feeds a transcription API, and the wav of a 15-minute video is
        ~150MB to move where the mp3 is ~7MB.
        """
        from app.core.config import get_settings

        settings = get_settings()

        actual_path = str(Path(output_path).with_suffix(".mp3"))

        if settings.video_download_api_key:
            return DownloadedAudio(
                self._download_audio_via_video_download_api(url, actual_path, settings)
            )

        return self._download_audio_via_downloader_service(url, actual_path, settings)

    def _download_audio_via_downloader_service(
        self, url: str, actual_path: str, settings
    ) -> DownloadedAudio:
        downloader_url = settings.downloader_url.rstrip("/")

        try:
            duration_seconds = None
            with httpx.Client(timeout=300.0) as client:
                with client.stream("POST", f"{downloader_url}/download", json={"url": url}) as response:
                    if response.status_code >= 400:
                        # The body has to be pulled off a streaming response before
                        # it can be read.
                        detail = response.read().decode("utf-8", "replace")[:500]
                        logger.error(
                            "audio_download_rejected",
                            url=url,
                            status=response.status_code,
                            detail=detail,
                        )
                        raise DownloadError(
                            f"Downloader service returned {response.status_code}: {detail}"
                        )
                    raw_duration = response.headers.get("X-Video-Duration")
                    if raw_duration and raw_duration.isdigit():
                        duration_seconds = int(raw_duration)
                    with open(actual_path, "wb") as f:
                        for chunk in response.iter_bytes():
                            f.write(chunk)

            size_mb = Path(actual_path).stat().st_size / (1024 * 1024)
            logger.info(
                "audio_downloaded",
                path=actual_path,
                size_mb=round(size_mb, 1),
                duration_seconds=duration_seconds,
            )
            return DownloadedAudio(actual_path, duration_seconds)
        except DownloadError:
            raise
        except httpx.ConnectError as e:
            # An unreachable host is almost always a misconfigured DOWNLOADER_URL
            # (e.g. the docker-compose hostname used outside compose), so say so
            # instead of surfacing a bare getaddrinfo errno.
            logger.error(
                "audio_download_unreachable",
                url=url,
                downloader_url=downloader_url,
                error=str(e),
            )
            raise DownloadError(
                f"Downloader service at {downloader_url} is unreachable. "
                f"Start it (uvicorn main:app --port 8001 in ./downloader) or point "
                f"DOWNLOADER_URL at a running instance. Underlying error: {e}"
            )
        except Exception as e:
            logger.error("audio_download_failed", url=url, error=str(e))
            raise DownloadError(f"Failed to download audio via downloader service: {e}")

    def _download_audio_via_video_download_api(self, url: str, actual_path: str, settings) -> str:
        base_url = settings.video_download_api_base_url.rstrip("/")
        start_url = f"{base_url}/ajax/download.php"
        progress_url = f"{base_url}/ajax/progress.php"

        try:
            with httpx.Client(timeout=60.0, follow_redirects=True) as client:
                clean_url = normalize_youtube_url(url)
                start_response = client.get(
                    start_url,
                    params={
                        "url": clean_url,
                        "format": settings.video_download_format,
                        "apikey": settings.video_download_api_key,
                        "api": settings.video_download_api_key,
                        "add_info": "1",
                        "allow_extended_duration": "1",
                        "no_merge": "0",
                    },
                )
                if start_response.status_code >= 400:
                    raise DownloadError(
                        f"Video Download API returned {start_response.status_code}: "
                        f"{start_response.text[:500]}"
                    )
                start_data = start_response.json()

                download_url = start_data.get("download_url")
                download_id = start_data.get("id")
                if not download_url and not download_id:
                    raise DownloadError(f"Video Download API did not return a download id: {start_data}")

                deadline = time.monotonic() + settings.video_download_poll_timeout_seconds
                while not download_url and time.monotonic() < deadline:
                    time.sleep(settings.video_download_poll_interval_seconds)
                    progress_response = client.get(progress_url, params={"id": download_id})
                    progress_response.raise_for_status()
                    progress_data = progress_response.json()

                    if progress_data.get("success") in (0, False):
                        message = (
                            progress_data.get("error")
                            or progress_data.get("message")
                            or progress_data.get("text")
                            or "download job failed"
                        )
                        raise DownloadError(f"Video Download API failed: {message}")

                    progress = int(progress_data.get("progress") or 0)
                    download_url = progress_data.get("download_url")
                    logger.info("video_download_api_progress", progress=progress, ready=bool(download_url))

                if not download_url:
                    raise DownloadError("Video Download API timed out waiting for download URL")

                with client.stream("GET", download_url, timeout=300.0) as response:
                    response.raise_for_status()
                    with open(actual_path, "wb") as f:
                        for chunk in response.iter_bytes():
                            f.write(chunk)

            return actual_path
        except DownloadError:
            raise
        except Exception as e:
            logger.error("video_download_api_failed", url=url, error=str(e))
            raise DownloadError(f"Failed to download audio via Video Download API: {e}")
