import json
from functools import lru_cache
from typing import List, Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Database
    database_url: str = "postgresql://postgres:postgres@localhost:5432/videonotes"

    # Google Authentication
    google_client_id: str = ""

    # YouTube Data API v3 key.
    #
    # The preferred source of a video's runtime and title: it is Google's own
    # API, so unlike the scraped endpoints it answers a cloud host normally, and
    # a lookup costs one unit of a 10,000/day free quota. Without it a video's
    # runtime is unknown until the transcript API reports it, which means the
    # free plan's duration cap cannot be enforced before the video is accepted.
    youtube_api_key: str = ""
    
    # Custom JWT Authentication
    jwt_secret_key: str = "super_secret_key_change_me_in_production"
    jwt_algorithm: str = "HS256"
    jwt_access_token_expire_minutes: int = 1440 # 24 hours

    # Groq API (note generation)
    groq_api_key: str = ""
    groq_model: str = "openai/gpt-oss-120b"

    # NVIDIA API (used for note generation)
    nvidia_api_key: str = ""
    nvidia_base_url: str = "https://integrate.api.nvidia.com/v1"
    nvidia_model: str = "meta/llama-3.1-70b-instruct"

    # Storage paths
    transcript_dir: str = "./transcripts"
    notes_dir: str = "./notes"

    # Note generation
    # A long transcript is processed in slices of this many characters so the
    # notes cover the whole video instead of only the part that fit in one
    # request. The default keeps a slice plus its 4k-token reply inside Groq's
    # free-tier 8k tokens-per-minute window; raise it on a paid tier.
    # notes_max_chunks caps how many LLM calls one video can trigger.
    notes_chunk_chars: int = 12000
    notes_max_chunks: int = 12
    # Above this combined size the per-part notes are stitched together locally
    # rather than sent back to the model for a single consolidation pass.
    notes_merge_chars: int = 14000

    # Free Plan Limits
    free_daily_videos: int = 2
    free_daily_short_videos: int = 10
    free_max_duration_minutes: int = 30

    # CORS
    cors_origins_env: str = Field(
        default='["http://localhost:5173","https://v-notes-five.vercel.app"]',
        validation_alias="CORS_ORIGINS",
    )
    cors_origin_regex: Optional[str] = r"http://(localhost|127\.0\.0\.1):\d+"

    # Logging
    log_level: str = "INFO"

    # YouTube Transcripts API - https://youtubetranscripts.co
    #
    # The one and only source of transcripts. It serves a video's native
    # captions where they exist and falls back to Whisper where they do not, so
    # this backend never downloads audio and never has to get past YouTube's
    # "Sign in to confirm you're not a bot" block on datacenter IPs. Create a
    # key at https://youtubetranscripts.co/dashboard/api-keys.
    transcript_api_key: str = ""
    transcript_api_base_url: str = "https://api.youtubetranscripts.co"
    # Caption language to request: an ISO-639 code ("en", "hi") or "auto" to
    # take whatever track the video ships with.
    transcript_language: str = "auto"
    # Language to translate the transcript into, or "none" to keep it as-is.
    transcript_translate_to: str = "none"
    # Skip the API's Whisper fallback. Native captions cost 1 credit per video
    # while the Whisper fallback costs 1 credit per minute, so turning this on
    # caps the spend - at the price of failing on videos that have no captions.
    transcript_native_only: bool = False
    # The API is asynchronous, so a request is enqueued and then polled. The
    # timeout has to cover a Whisper fallback on a long video, not just a
    # caption lookup.
    transcript_poll_interval_seconds: float = 2.0
    transcript_poll_timeout_seconds: int = 600

    @property
    def cors_origins(self) -> List[str]:
        raw = self.cors_origins_env.strip()
        if not raw:
            return []

        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return [origin.strip() for origin in raw.split(",") if origin.strip()]

        if isinstance(parsed, list):
            return [str(origin).strip() for origin in parsed if str(origin).strip()]
        if isinstance(parsed, str) and parsed.strip():
            return [parsed.strip()]
        return []


@lru_cache
def get_settings() -> Settings:
    return Settings()
