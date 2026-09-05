import uuid
from pathlib import Path
import subprocess

from app.core.config import get_settings
from app.utils.logger import get_logger

logger = get_logger(__name__)
settings = get_settings()


class AudioExtractionError(Exception):
    """Raised when audio extraction fails."""
    pass


class AudioExtractionService:
    def __init__(self):
        self.upload_dir = Path(settings.upload_dir)
        self.upload_dir.mkdir(parents=True, exist_ok=True)

    def extract_audio(self, video_path: str, video_id: uuid.UUID) -> str:
        """Extract audio from a video file and return the audio file path."""
        output_path = str(self.upload_dir / f"{video_id}.wav")

        try:
            cmd = [
                "ffmpeg",
                "-i", video_path,
                "-vn",
                "-acodec", "pcm_s16le",
                "-ar", "16000",
                "-ac", "1",
                "-y",
                output_path,
            ]

            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=300,
            )

            if result.returncode != 0:
                logger.error("audio_extraction_failed", error=result.stderr)
                raise AudioExtractionError(f"ffmpeg failed: {result.stderr}")

            logger.info("audio_extracted", video_id=str(video_id))
            return output_path
        except subprocess.TimeoutExpired:
            raise AudioExtractionError("Audio extraction timed out")
        except FileNotFoundError:
            raise AudioExtractionError("ffmpeg not found. Please install ffmpeg.")
