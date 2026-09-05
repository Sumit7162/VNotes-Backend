import uuid
from pathlib import Path

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.video import VideoStatus
from app.repositories.note import NoteRepository
from app.repositories.usage import UsageRepository
from app.repositories.video import VideoRepository
from app.services.audio_extraction import AudioExtractionService
from app.services.groq_ai import GroqAIService
from app.services.transcription import TranscriptionService
from app.services.youtube_downloader import YouTubeDownloaderService
from app.utils.logger import get_logger

logger = get_logger(__name__)
settings = get_settings()


class VideoProcessingError(Exception):
    """Raised when the video processing pipeline fails."""
    pass


class VideoProcessingService:
    def __init__(self, db: Session):
        self.db = db
        self.video_repo = VideoRepository(db)
        self.note_repo = NoteRepository(db)
        self.usage_repo = UsageRepository(db)
        self.downloader = YouTubeDownloaderService()
        self.audio_extractor = AudioExtractionService()
        self.transcriber = TranscriptionService()
        self.note_generator = GroqAIService()

    def process_video(self, video_id: uuid.UUID, user_id: uuid.UUID) -> None:
        """Run the full video processing pipeline."""
        video = self.video_repo.get_by_id(video_id)
        if not video:
            raise VideoProcessingError(f"Video {video_id} not found")

        try:
            # Step 1: Get info
            self.video_repo.update_status(video, VideoStatus.DOWNLOADING, clear_error=True)
            logger.info("processing_step", video_id=str(video_id), step="fetching_info")
            info = self.downloader.get_video_info(video.youtube_url)
            duration_seconds = info.get("duration")

            self.video_repo.update_status(
                video,
                VideoStatus.TRANSCRIBING,
                title=info["title"],
                duration_seconds=duration_seconds,
                file_path=None,
            )

            # Step 2: Transcribe (fetch captions directly)
            logger.info("processing_step", video_id=str(video_id), step="fetching_transcript")
            
            try:
                transcript = self.downloader.get_transcript(video.youtube_url)
            except Exception as e:
                logger.warning("transcript_fetch_failed_falling_back_to_audio", video_id=str(video_id), error=str(e))
                self.video_repo.update_status(video, VideoStatus.DOWNLOADING)
                
                # Download audio
                upload_dir = Path(settings.upload_dir)
                upload_dir.mkdir(parents=True, exist_ok=True)
                audio_path = str(upload_dir / f"{video_id}")
                
                logger.info("processing_step", video_id=str(video_id), step="downloading_audio")
                download = self.downloader.download_audio(video.youtube_url, audio_path)

                # The downloader probes the video with yt-dlp, so it is the more
                # reliable source of a runtime than the metadata lookup above.
                if download.duration_seconds:
                    duration_seconds = download.duration_seconds

                # Transcribe using Whisper API
                self.video_repo.update_status(
                    video,
                    VideoStatus.TRANSCRIBING,
                    duration_seconds=duration_seconds,
                    audio_path=download.path,
                )
                logger.info("processing_step", video_id=str(video_id), step="whisper_transcription")
                transcript = self.transcriber.transcribe(download.path)

            # Save transcript to file
            transcript_dir = Path(settings.transcript_dir)
            transcript_dir.mkdir(parents=True, exist_ok=True)
            transcript_path = transcript_dir / f"{video_id}.txt"
            transcript_path.write_text(transcript, encoding="utf-8")

            # Step 4: Generate notes
            self.video_repo.update_status(video, VideoStatus.GENERATING_NOTES)
            logger.info("processing_step", video_id=str(video_id), step="generating_notes")

            # The full transcript is passed through: generate_notes slices it
            # internally so a long video gets notes for its whole runtime.
            notes = self.note_generator.generate_notes(transcript, info["title"])

            # Save notes to file
            notes_dir = Path(settings.notes_dir)
            notes_dir.mkdir(parents=True, exist_ok=True)
            notes_path = notes_dir / f"{video_id}.md"
            notes_path.write_text(notes, encoding="utf-8")

            # Save notes to database
            self.note_repo.create(video_id=video_id, markdown_content=notes, model_used=settings.groq_model)

            duration_minutes = (duration_seconds or 0) // 60
            self.usage_repo.increment_usage(user_id, duration_minutes)
            logger.info(
                "usage_recorded",
                video_id=str(video_id),
                user_id=str(user_id),
                minutes=duration_minutes,
            )

            # Step 5: Mark complete
            self.video_repo.update_status(video, VideoStatus.COMPLETED, clear_error=True)
            logger.info("processing_complete", video_id=str(video_id))

        except Exception as e:
            logger.error("processing_failed", video_id=str(video_id), error=str(e))
            self.video_repo.update_status(video, VideoStatus.FAILED, error_message=str(e))
