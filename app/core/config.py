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

    # Password sign-in
    #
    # A user who signs up with an email address and a password cannot use the
    # app until the address is proved to be theirs, so signup only creates the
    # account - the verification link is what turns it on. The link carries a
    # signed, short-lived JWT rather than a row in a table, which keeps the
    # whole flow stateless and free of a cleanup job.
    email_verification_expire_hours: int = 24
    password_reset_expire_minutes: int = 60
    password_min_length: int = 8
    # Look up the address's domain for MX records before accepting a signup.
    # This rejects typos like "gmial.com" at the door instead of sending a
    # verification email into a black hole. Turn it off if outbound DNS is
    # blocked wherever this runs; syntax checking still applies.
    email_check_deliverability: bool = True

    # Brevo (https://app.brevo.com) - transactional email.
    #
    # Used to deliver verification and password-reset links. The free plan
    # allows 300 emails a day, which is well clear of what signups need. The
    # key is created under Brevo -> SMTP & API -> API Keys, and the sender
    # address MUST be one Brevo has verified (Senders, Domains & Dedicated IPs
    # -> Senders), otherwise every send is rejected.
    brevo_api_key: str = ""
    brevo_api_url: str = "https://api.brevo.com/v3/smtp/email"
    brevo_sender_email: str = ""
    brevo_sender_name: str = "V-Notes AI"
    # Set this to the deployed site, e.g. https://v-notes-five.vercel.app. It is
    # the base of the link in the email, so a wrong value sends every user to
    # the wrong place.
    frontend_url: str = "http://localhost:5173"

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
