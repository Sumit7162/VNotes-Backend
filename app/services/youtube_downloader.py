import httpx
import re
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


def build_proxy_config():
    """Proxy configuration for youtube-transcript-api, or None if unconfigured.

    Webshare credentials win over a plain proxy URL because the library's
    Webshare integration rotates exit nodes and retries when YouTube blocks one,
    which a static proxy URL cannot do.
    """
    from app.core.config import get_settings

    settings = get_settings()

    if settings.webshare_proxy_username and settings.webshare_proxy_password:
        from youtube_transcript_api.proxies import WebshareProxyConfig

        return WebshareProxyConfig(
            proxy_username=settings.webshare_proxy_username,
            proxy_password=settings.webshare_proxy_password,
        )

    if settings.proxy_url:
        from youtube_transcript_api.proxies import GenericProxyConfig

        return GenericProxyConfig(
            http_url=settings.proxy_url,
            https_url=settings.proxy_url,
        )

    return None


def outbound_proxy() -> Optional[str]:
    """Proxy URL for direct httpx calls to YouTube, or None.

    Webshare's rotating endpoint works as an ordinary authenticated HTTP proxy,
    so it serves the plain httpx calls too.
    """
    from app.core.config import get_settings

    settings = get_settings()

    if settings.webshare_proxy_username and settings.webshare_proxy_password:
        return (
            f"http://{settings.webshare_proxy_username}:"
            f"{settings.webshare_proxy_password}@p.webshare.io:80"
        )
    return settings.proxy_url or None


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


def normalize_youtube_url(url: str) -> str:
    """Return a clean watch URL without sharing/tracking query params."""
    video_id = extract_video_id(url)
    return f"https://www.youtube.com/watch?v={video_id}"


class YouTubeDownloaderService:
    def get_video_info(self, url: str) -> dict:
        """Fetch video metadata, cheapest and most reliable source first.

        The runtime matters - it is what the free-plan duration cap and the
        usage counters are measured in - so it is worth some care to obtain.

        1. The YouTube Data API, when a key is configured. It is Google's own
           API, so it answers a cloud host as happily as anywhere else, it
           reports the runtime directly, and one lookup costs a single unit of
           a 10,000/day free quota.
        2. The downloader's yt-dlp probe. Accurate, but it is subject to the
           datacenter-IP blocking that the Data API sidesteps.
        3. oEmbed, for the title only - it does not report a runtime.

        ``duration`` stays None when no source can say, and the UI hides an
        unknown duration rather than showing a made-up one.
        """
        info = {"title": None, "duration": None, "thumbnail": None}

        from_api = self._fetch_via_data_api(url)
        if from_api:
            info.update(from_api)

        # Only probe when the Data API could not answer: the probe is the slow
        # path and the one YouTube blocks.
        if info["duration"] is None or info["title"] is None:
            probed = self._probe_via_downloader_service(url)
            if probed:
                info["title"] = info["title"] or probed.get("title")
                info["duration"] = info["duration"] or probed.get("duration")
                info["thumbnail"] = info["thumbnail"] or probed.get("thumbnail")

        if not info["title"] or not info["thumbnail"]:
            oembed = self._fetch_oembed(url)
            if oembed:
                info["title"] = info["title"] or oembed.get("title")
                info["thumbnail"] = info["thumbnail"] or oembed.get("thumbnail")

        info["title"] = info["title"] or "YouTube Video"
        return info

    @staticmethod
    def _fetch_via_data_api(url: str) -> Optional[dict]:
        """Metadata from the YouTube Data API, or None when it cannot answer.

        Not routed through the proxy: this is Google's own API rather than the
        scraped endpoints, so it is not IP-blocked, and sending it through a
        metered residential proxy would only waste bandwidth.
        """
        from app.core.config import get_settings

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
            logger.warning("data_api_failed", video_id=video_id, error=str(e))
            return None

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
            # oEmbed is a YouTube endpoint, so it goes out through the proxy too.
            response = httpx.get(oembed_url, timeout=10.0, proxy=outbound_proxy())
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
        """Fetch YouTube captions, preferring the downloader service.

        YouTube serves auto-generated caption tracks only to non-datacenter IPs,
        so a cloud-hosted backend cannot reliably fetch them itself. The
        downloader is the component meant to face YouTube, so ask it first and
        only fall back to fetching directly when it cannot answer.
        """
        via_service = self._transcript_via_downloader_service(url)
        if via_service is not None:
            return via_service
        return self._transcript_direct(url)

    def _transcript_via_downloader_service(self, url: str) -> Optional[str]:
        """Captions from the downloader service, or None if it could not supply them."""
        from app.core.config import get_settings

        downloader_url = get_settings().downloader_url.rstrip("/")
        try:
            response = httpx.post(
                f"{downloader_url}/transcript", json={"url": url}, timeout=60.0
            )
            if response.status_code >= 400:
                logger.info(
                    "transcript_service_no_captions",
                    url=url,
                    status=response.status_code,
                    detail=response.text[:300],
                )
                return None
            data = response.json()
            transcript = (data.get("transcript") or "").strip()
            if not transcript:
                return None
            logger.info(
                "transcript_fetched_via_service",
                url=url,
                language=data.get("language"),
                generated=data.get("is_generated"),
                chars=len(transcript),
            )
            return transcript
        except Exception as e:
            logger.warning("transcript_service_unreachable", url=url, error=str(e))
            return None

    def _transcript_direct(self, url: str) -> str:
        """Fetch captions from this process. Blocked on datacenter IPs."""
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

            # proxy_config and http_client compose: the proxies are applied onto
            # the session passed in, so cookies and a proxy can be used together.
            proxy_config = build_proxy_config()
            if proxy_config is None:
                logger.warning("transcript_fetch_without_proxy", video_id=video_id)
            fetcher = YouTubeTranscriptApi(
                proxy_config=proxy_config, http_client=http_client
            )
            transcript_list = fetcher.list(video_id)

            # Prefer an English track, else take whatever the video has.
            chosen = None
            for find in (
                transcript_list.find_manually_created_transcript,
                transcript_list.find_generated_transcript,
            ):
                try:
                    chosen = find(["en"])
                    break
                except Exception:
                    continue
            if chosen is None:
                chosen = list(transcript_list)[0]

            language = chosen.language_code
            # Translate only when YouTube offers a translation. Auto-generated
            # tracks often report is_translatable=False, and calling translate()
            # on one raises - which used to discard usable captions and send the
            # video down the far more expensive download-and-transcribe path.
            if language != "en" and chosen.is_translatable:
                chosen = chosen.translate("en")
                language = "en"

            transcript = TextFormatter().format_transcript(chosen.fetch())

            logger.info("transcript_fetched", video_id=video_id, language=language)
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
