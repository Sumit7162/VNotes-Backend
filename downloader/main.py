import os
import tempfile
import uuid
import logging

from urllib.parse import urlparse, parse_qs

from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.responses import FileResponse
from pydantic import BaseModel
import yt_dlp
from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api.formatters import TextFormatter
from youtube_transcript_api.proxies import GenericProxyConfig

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="YouTube Downloader Service")

# Downloads land in the platform temp dir so the service also runs outside the
# Linux container (a hard-coded "/tmp" is not a real temp dir on Windows).
TMP_DIR = os.getenv("DOWNLOADER_TMP_DIR", tempfile.gettempdir())

# Speech-only audio, sized for a transcription API rather than for listening.
# Mono 16kHz 64kbps mp3 is what Whisper wants anyway, and it is roughly 20x
# smaller than the PCM wav this service used to hand back.
AUDIO_CODEC = "mp3"
AUDIO_BITRATE_KBPS = "64"
AUDIO_SAMPLE_RATE = 16000
AUDIO_MEDIA_TYPE = "audio/mpeg"

# YouTube answers datacenter IP ranges with "Sign in to confirm you're not a
# bot", so a deployed downloader has to egress through a residential proxy.
# Accepts any scheme yt-dlp understands, including socks5://. Empty = direct,
# which is fine on a developer machine but will be blocked on a cloud host.
PROXY_URL = os.getenv("PROXY_URL", "").strip()

# YouTube's player gates audio formats behind a JavaScript challenge, so yt-dlp
# needs a JS runtime to see them at all. Deno is the one that travels with the
# image (installed by the yt-dlp[deno] extra); node is listed too so a developer
# machine that already has it keeps working. yt-dlp picks the highest-priority
# runtime that is actually present, so enabling one that is missing is harmless.
JS_RUNTIMES = {"deno": {}, "node": {}}

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)


class DownloadRequest(BaseModel):
    url: str


def normalize_cookies_text(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n").lstrip("\ufeff")
    if text.startswith("Value 1\n"):
        text = text.removeprefix("Value 1\n").lstrip("\ufeff")
    if text.startswith("Value 1 "):
        text = text.removeprefix("Value 1 ").lstrip("\ufeff")
    return text.strip() + "\n"


def write_normalized_cookies_file(text: str) -> str:
    cookies_path = os.path.join(TMP_DIR, "youtube-cookies-normalized.txt")
    with open(cookies_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(normalize_cookies_text(text))
    return cookies_path


@app.get("/health")
def health_check():
    return {
        "status": "healthy",
        "service": "youtube-downloader",
        "cookies_loaded": bool(get_cookies_file()),
        # Whether an egress proxy is configured, without leaking its credentials.
        "proxy_configured": bool(PROXY_URL),
    }


def get_cookies_file() -> str | None:
    configured_path = os.getenv("YOUTUBE_COOKIES_FILE", "cookies.txt")
    if os.path.exists(configured_path):
        with open(configured_path, "r", encoding="utf-8-sig") as f:
            return write_normalized_cookies_file(f.read())

    cookies_text = os.getenv("YOUTUBE_COOKIES", "").strip()
    if not cookies_text:
        return None

    return write_normalized_cookies_file(cookies_text.replace("\\n", "\n"))


def remove_file(path: str):
    try:
        if os.path.exists(path):
            os.remove(path)
            logger.info(f"Removed temporary file {path}")
    except Exception as e:
        logger.error(f"Failed to remove file {path}: {e}")


def pick_audio_format_id(formats: list[dict]) -> str | None:
    audio_formats = [
        item
        for item in formats
        if item.get("format_id")
        and item.get("acodec") not in (None, "none")
        and item.get("url")
    ]
    if not audio_formats:
        return None

    audio_only = [item for item in audio_formats if item.get("vcodec") == "none"]
    candidates = audio_only or audio_formats

    def score(item: dict) -> tuple[int, float, float]:
        ext_score = {"m4a": 4, "mp4": 3, "webm": 2}.get(item.get("ext"), 1)
        abr = float(item.get("abr") or item.get("tbr") or 0)
        filesize = float(item.get("filesize") or item.get("filesize_approx") or 0)
        return (ext_score, abr, filesize)

    best = max(candidates, key=score)
    return str(best["format_id"])


def probe_video(url: str) -> dict:
    """Pull metadata for a video without downloading it."""
    opts = {
        "js_runtimes": JS_RUNTIMES,
        "quiet": True,
        "skip_download": True,
        "legacyserverconnect": True,
        "noplaylist": True,
        "socket_timeout": 30,
        "http_headers": {"User-Agent": USER_AGENT},
    }
    cookies_file = get_cookies_file()
    if cookies_file:
        opts["cookiefile"] = cookies_file
    if PROXY_URL:
        opts["proxy"] = PROXY_URL

    with yt_dlp.YoutubeDL(opts) as ydl:
        return ydl.extract_info(url, download=False) or {}


@app.post("/info")
def video_info(request: DownloadRequest):
    """Report a video's metadata, including the runtime YouTube's oEmbed API omits."""
    try:
        info = probe_video(request.url)
    except Exception as e:
        logger.error(f"Info lookup failed for {request.url}: {e}")
        raise HTTPException(status_code=502, detail=str(e))

    duration = info.get("duration")
    return {
        "title": info.get("title"),
        "duration": int(duration) if duration else None,
        "thumbnail": info.get("thumbnail"),
        "uploader": info.get("uploader"),
    }


def extract_video_id(url: str) -> str:
    """Pull the 11-character video id out of any common YouTube URL form."""
    url = url.strip()
    parsed = urlparse(url)
    if parsed.hostname in ("youtu.be", "www.youtu.be"):
        return parsed.path.lstrip("/").split("/")[0]
    if parsed.hostname in ("youtube.com", "www.youtube.com", "m.youtube.com"):
        if parsed.path == "/watch":
            ids = parse_qs(parsed.query).get("v")
            if ids:
                return ids[0]
        for prefix in ("/embed/", "/v/", "/shorts/", "/live/"):
            if parsed.path.startswith(prefix):
                return parsed.path[len(prefix):].split("/")[0]
    raise ValueError(f"Could not extract a YouTube video id from {url!r}")


def pick_transcript(transcript_list):
    """Choose the most useful caption track: English first, else whatever exists."""
    for find in (
        transcript_list.find_manually_created_transcript,
        transcript_list.find_generated_transcript,
    ):
        try:
            return find(["en"])
        except Exception:
            continue
    return list(transcript_list)[0]


@app.post("/transcript")
def fetch_transcript(request: DownloadRequest):
    """Fetch YouTube captions.

    This lives in the downloader rather than the backend because the downloader
    is the component deployed where YouTube will talk to it. YouTube serves
    auto-generated caption tracks only to non-datacenter IPs, so a backend
    running on a cloud host cannot fetch them itself.
    """
    try:
        video_id = extract_video_id(request.url)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    proxy_config = (
        GenericProxyConfig(http_url=PROXY_URL, https_url=PROXY_URL)
        if PROXY_URL
        else None
    )

    try:
        transcript_list = YouTubeTranscriptApi(proxy_config=proxy_config).list(video_id)
        chosen = pick_transcript(transcript_list)
        language, generated = chosen.language_code, chosen.is_generated

        # Translate to English only when YouTube actually offers a translation.
        # Auto-generated tracks frequently report is_translatable=False, and
        # calling translate() on one raises - which used to send the whole video
        # down the download-and-transcribe path even though usable captions
        # existed. Keeping the original language is far cheaper, and the note
        # generator handles a non-English transcript.
        if language != "en" and chosen.is_translatable:
            chosen = chosen.translate("en")
            language = "en"

        text = TextFormatter().format_transcript(chosen.fetch())
    except Exception as e:
        logger.warning(f"Transcript fetch failed for {request.url}: {e}")
        raise HTTPException(status_code=502, detail=str(e))

    if not text.strip():
        raise HTTPException(status_code=502, detail="Captions were empty")

    logger.info(
        "Transcript for %s: %s chars, language=%s, generated=%s",
        video_id, len(text), language, generated,
    )
    return {"transcript": text, "language": language, "is_generated": generated}


@app.post("/download")
def download_audio(request: DownloadRequest, background_tasks: BackgroundTasks):
    url = request.url
    file_id = str(uuid.uuid4())
    output_filename = os.path.join(TMP_DIR, f"{file_id}.%(ext)s")

    cookies_file = get_cookies_file()
    base_opts = {
        "outtmpl": output_filename,
        # YouTube's current player uses JS challenges for audio formats.
        # yt-dlp-ejs is installed with the downloader environment.
        "js_runtimes": JS_RUNTIMES,
        # Speech-grade mono mp3 rather than PCM wav: the caller only feeds this
        # to a transcription API, and a wav of a 15-minute video is ~150MB to
        # push over the wire where the mp3 is ~7MB.
        "postprocessors": [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": AUDIO_CODEC,
                "preferredquality": AUDIO_BITRATE_KBPS,
            }
        ],
        "postprocessor_args": {
            "extractaudio": ["-ac", "1", "-ar", str(AUDIO_SAMPLE_RATE)],
        },
        "quiet": False,
        "no_warnings": False,
        "legacyserverconnect": True,
        "noplaylist": True,
        "retries": 3,
        "fragment_retries": 3,
        "socket_timeout": 30,
        "http_headers": {"User-Agent": USER_AGENT},
    }
    if cookies_file:
        base_opts["cookiefile"] = cookies_file
    if PROXY_URL:
        base_opts["proxy"] = PROXY_URL
    else:
        logger.warning(
            "No PROXY_URL set; YouTube blocks datacenter IPs and this will "
            "likely fail with 'Sign in to confirm you're not a bot'"
        )

    selected_format = None
    duration = None
    try:
        probe_opts = {
            key: value
            for key, value in base_opts.items()
            if key not in {"outtmpl", "postprocessors", "postprocessor_args"}
        }
        probe_opts.update({"quiet": True, "skip_download": True})
        with yt_dlp.YoutubeDL(probe_opts) as ydl:
            info = ydl.extract_info(url, download=False)
        selected_format = pick_audio_format_id(info.get("formats") or [])
        duration = info.get("duration")
        logger.info("Selected format %s from available formats", selected_format)
    except Exception as e:
        logger.warning("Format probing failed, using fallback selectors: %s", e)

    attempts = [
        {
            "format": selected_format,
            "extractor_args": {"youtube": {"player_client": ["web", "mweb"]}},
        },
        {
            "format": "bestaudio[ext=m4a]/bestaudio/best[ext=mp4]/best",
            "extractor_args": {"youtube": {"player_client": ["web", "mweb"]}},
        },
        {
            "format": "best[acodec!=none]/best",
            "extractor_args": {"youtube": {"player_client": ["ios", "web_safari"]}},
        },
        {
            "format": "worstaudio/worst",
            "extractor_args": {"youtube": {"player_client": ["android", "web"]}},
        },
    ]
    attempts = [attempt for attempt in attempts if attempt["format"]]

    try:
        last_error = None
        for index, attempt_opts in enumerate(attempts, start=1):
            try:
                ydl_opts = {**base_opts, **attempt_opts}
                logger.info("Download attempt %s with format %s", index, ydl_opts["format"])
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    ydl.download([url])
                last_error = None
                break
            except Exception as e:
                last_error = e
                logger.warning("Download attempt %s failed: %s", index, e)

        if last_error:
            raise last_error
        
        actual_path = os.path.join(TMP_DIR, f"{file_id}.{AUDIO_CODEC}")
        if not os.path.exists(actual_path):
            raise FileNotFoundError(f"Downloaded file not found at {actual_path}")

        size_mb = os.path.getsize(actual_path) / (1024 * 1024)
        logger.info("Serving %s (%.1f MB, duration=%s)", actual_path, size_mb, duration)

        background_tasks.add_task(remove_file, actual_path)
        # The runtime rides along on the response so the caller does not need a
        # second round trip to /info just to record how long the video was.
        headers = {"X-Video-Duration": str(int(duration))} if duration else {}
        return FileResponse(
            actual_path,
            media_type=AUDIO_MEDIA_TYPE,
            filename=f"{file_id}.{AUDIO_CODEC}",
            headers=headers,
        )
    except Exception as e:
        logger.error(f"Download failed for {url}: {e}")
        raise HTTPException(status_code=500, detail=str(e))
