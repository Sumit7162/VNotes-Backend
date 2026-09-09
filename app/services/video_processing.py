import uuid
from pathlib import Path

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.video import VideoStatus
from app.repositories.note import NoteRepository
from app.repositories.usage import UsageRepository
from app.repositories.video import VideoRepository
from app.services.groq_ai import GroqAIService
from app.services.transcript_api import TranscriptAPIService
from app.services.youtube_metadata import YouTubeMetadataService
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
        self.metadata = YouTubeMetadataService()
        self.transcripts = TranscriptAPIService()
        self.note_generator = GroqAIService()

    def process_video(self, video_id: uuid.UUID, user_id: uuid.UUID) -> None:
        """Run the full video processing pipeline.

        Two steps: fetch the transcript from the transcript API, then turn it
        into notes. There is no download or local transcription stage - the API
        handles native captions and its own Whisper fallback.
        """
        video = self.video_repo.get_by_id(video_id)
        if not video:
            raise VideoProcessingError(f"Video {video_id} not found")

        try:
            # Step 1: Metadata, so the record has a title even if the rest fails.
            logger.info("processing_step", video_id=str(video_id), step="fetching_info")
            info = self.metadata.get_video_info(video.youtube_url)
            duration_seconds = info.get("duration")

            self.video_repo.update_status(
                video,
                VideoStatus.TRANSCRIBING,
                title=info["title"],
                duration_seconds=duration_seconds,
                clear_error=True,
            )

            # Step 2: Transcript
            logger.info("processing_step", video_id=str(video_id), step="fetching_transcript")
            transcript_result = self.transcripts.fetch(video.youtube_url)
            transcript = transcript_result.text
            title = info["title"] or transcript_result.title or "YouTube Video"

            # The transcript API reports the runtime it actually processed, so it
            # fills in a duration the metadata lookup could not supply - which is
            # what the usage counter below is measured in.
            if duration_seconds is None and transcript_result.duration_seconds:
                duration_seconds = transcript_result.duration_seconds
                self.video_repo.update_status(
                    video,
                    VideoStatus.TRANSCRIBING,
                    title=title,
                    duration_seconds=duration_seconds,
                )

            # Step 3 onwards is shared with the uploaded-transcript pipeline.
            self._notes_from_transcript(video, user_id, transcript, title, duration_seconds)

        except Exception as e:
            logger.error("processing_failed", video_id=str(video_id), error=str(e))
            # A failed run must not leave media behind either.
            self._discard_media(video)
            self.video_repo.update_status(video, VideoStatus.FAILED, error_message=str(e))

    def process_transcript(
        self,
        video_id: uuid.UUID,
        user_id: uuid.UUID,
        transcript: str,
        duration_seconds: int,
    ) -> None:
        """Turn a transcript the user supplied directly into notes.

        The same pipeline as ``process_video`` minus its first two steps: there
        is no URL to look up and no transcript to fetch, because the caller
        already cleaned and validated the text. ``duration_seconds`` is the
        runtime estimated from the transcript's length, which is what the usage
        counter is measured in.
        """
        video = self.video_repo.get_by_id(video_id)
        if not video:
            raise VideoProcessingError(f"Video {video_id} not found")

        try:
            title = video.title or "Uploaded Transcript"
            self._notes_from_transcript(video, user_id, transcript, title, duration_seconds)
        except Exception as e:
            logger.error("transcript_processing_failed", video_id=str(video_id), error=str(e))
            self.video_repo.update_status(video, VideoStatus.FAILED, error_message=str(e))

    def _notes_from_transcript(
        self,
        video,
        user_id: uuid.UUID,
        transcript: str,
        title: str,
        duration_seconds: int | None,
    ) -> None:
        """Store the transcript, generate notes from it and record the usage.

        Shared by both entry points, which differ only in how they come by the
        transcript. Exceptions propagate so each caller marks its own record
        failed with the cleanup that entry point needs.
        """
        video_id = video.id

        transcript_dir = Path(settings.transcript_dir)
        transcript_dir.mkdir(parents=True, exist_ok=True)
        (transcript_dir / f"{video_id}.txt").write_text(transcript, encoding="utf-8")

        self.video_repo.update_status(video, VideoStatus.GENERATING_NOTES, clear_error=True)
        logger.info("processing_step", video_id=str(video_id), step="generating_notes")

        # The full transcript is passed through: generate_notes slices it
        # internally so a long video gets notes for its whole runtime.
        notes = self.note_generator.generate_notes(transcript, title)

        notes_dir = Path(settings.notes_dir)
        notes_dir.mkdir(parents=True, exist_ok=True)
        (notes_dir / f"{video_id}.md").write_text(notes, encoding="utf-8")

        self.note_repo.create(
            video_id=video_id, markdown_content=notes, model_used=settings.groq_model
        )

        duration_minutes = (duration_seconds or 0) // 60
        self.usage_repo.increment_usage(user_id, duration_minutes)
        logger.info(
            "usage_recorded",
            video_id=str(video_id),
            user_id=str(user_id),
            minutes=duration_minutes,
        )

        # Discard any media, then mark complete.
        self._discard_media(video)
        self.video_repo.update_status(video, VideoStatus.COMPLETED, clear_error=True)
        logger.info("processing_complete", video_id=str(video_id))

    def _discard_media(self, video) -> None:
        """Delete any video/audio file this record points at, and forget the paths.

        The current pipeline never downloads media, so for anything processed
        since the move to the transcript API this is a no-op. It exists for
        records created by the old download-and-transcribe pipeline, whose rows
        still carry file_path and audio_path: reprocessing one now clears the
        files it left on disk instead of stranding them there.

        Cleanup failure is logged and swallowed - the notes are already saved,
        and a leftover file is not worth failing a finished video over.
        """
        paths = [path for path in (video.file_path, video.audio_path) if path]
        if not paths:
            return

        for path in paths:
            try:
                file = Path(path)
                if file.exists():
                    size_mb = round(file.stat().st_size / (1024 * 1024), 1)
                    file.unlink()
                    logger.info("media_discarded", path=path, size_mb=size_mb)
            except OSError as e:
                logger.warning("media_discard_failed", path=path, error=str(e))

        self.video_repo.update_status(video, video.status, clear_media_paths=True)
