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
    
    # Custom JWT Authentication
    jwt_secret_key: str = "super_secret_key_change_me_in_production"
    jwt_algorithm: str = "HS256"
    jwt_access_token_expire_minutes: int = 1440 # 24 hours

    # Groq API (used for transcription)
    groq_api_key: str = ""
    groq_model: str = "openai/gpt-oss-120b"

    # NVIDIA API (used for note generation)
    nvidia_api_key: str = ""
    nvidia_base_url: str = "https://integrate.api.nvidia.com/v1"
    nvidia_model: str = "meta/llama-3.1-70b-instruct"

    # Storage paths
    upload_dir: str = "./uploads"
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

    # Proxy for the requests that touch YouTube.
    #
    # YouTube answers datacenter IP ranges (GCP, AWS, ...) with "Sign in to
    # confirm you're not a bot", so any deployed instance has to egress through
    # a residential proxy. Set either a full proxy URL, or Webshare credentials
    # - youtube-transcript-api has a dedicated Webshare integration that rotates
    # exit nodes and retries when one of them is blocked.
    #
    # This only applies to outbound YouTube traffic. Calls to the downloader
    # service are internal and are never proxied.
    proxy_url: str = ""
    webshare_proxy_username: str = ""
    webshare_proxy_password: str = ""

    # RapidAPI
    rapidapi_key: str = ""
    # Video Download API
    video_download_api_key: str = ""
    video_download_api_base_url: str = "https://p.savenow.to"
    video_download_format: str = "mp3"
    video_download_poll_interval_seconds: int = 3
    video_download_poll_timeout_seconds: int = 300
    # Downloader Service
    # Defaults to the local service; docker-compose overrides this with the
    # "downloader" service hostname.
    downloader_url: str = "http://localhost:8001"

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
