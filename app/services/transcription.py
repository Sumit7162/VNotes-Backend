import time
from pathlib import Path

from groq import Groq

from app.core.config import get_settings
from app.utils.logger import get_logger

logger = get_logger(__name__)
settings = get_settings()


class TranscriptionError(Exception):
    """Raised when transcription fails."""
    pass


class TranscriptionService:
    def __init__(self):
        self._client = None

    def _get_client(self) -> Groq:
        """Lazy-load the Groq client."""
        if self._client is None:
            if not settings.groq_api_key:
                raise TranscriptionError(
                    "GROQ_API_KEY is not set. Please configure it in your .env file."
                )
            self._client = Groq(
                api_key=settings.groq_api_key,
                timeout=60.0,
                max_retries=1,
            )
        return self._client

    def transcribe(self, audio_path: str) -> str:
        """Transcribe audio file using Groq's Whisper API."""
        try:
            start_time = time.time()
            logger.info("transcription_started", audio_path=audio_path)

            client = self._get_client()

            # Groq Whisper API accepts audio files directly.
            # Max file size is 25MB. The downloader already hands back a
            # speech-grade mp3 that stays well under that for anything up to
            # roughly an hour, so this re-encode is a safety net for longer
            # audio and for files that arrived from somewhere else.
            audio_file = Path(audio_path)
            file_size_mb = audio_file.stat().st_size / (1024 * 1024)

            if file_size_mb > 25:
                # Convert to compressed mp3 to fit within Groq's 25MB limit
                logger.info(
                    "compressing_audio",
                    original_size_mb=round(file_size_mb, 1),
                )
                compressed_path = self._compress_audio(audio_path)
                audio_file = Path(compressed_path)
                compressed_size = audio_file.stat().st_size / (1024 * 1024)
                logger.info(
                    "audio_compressed",
                    compressed_size_mb=round(compressed_size, 1),
                )

            with open(str(audio_file), "rb") as f:
                logger.info("sending_audio_to_groq_api", file_size=f"{audio_file.stat().st_size / (1024*1024):.1f}MB")
                transcription = client.audio.transcriptions.create(
                    file=(audio_file.name, f),
                    model="whisper-large-v3",  # or whisper-large-v3-turbo if available
                    language="hi",  # Hindi — change if needed
                    response_format="verbose_json",
                    prompt=(
                        "Mathematics lecture: integral, derivative, differentiation, "
                        "integration, equation, theorem, proof, matrix, vector, "
                        "polynomial, quadratic, logarithm, exponential, trigonometry, "
                        "sine, cosine, tangent, theta, sigma, delta, pi, infinity, "
                        "summation, limit, function, variable, coefficient, fraction, "
                        "numerator, denominator, square root, cube root, factorial, "
                        "permutation, combination, probability, statistics, mean, "
                        "median, variance, standard deviation, hypothesis, algebra, "
                        "calculus, geometry, linear algebra, differential equation"
                    ),
                )
                logger.info("groq_api_response_received")

            transcript = transcription.text.strip()
            elapsed = round(time.time() - start_time, 1)

            logger.info(
                "transcription_completed",
                audio_path=audio_path,
                elapsed_seconds=elapsed,
                transcript_length=len(transcript),
            )
            return transcript

        except TranscriptionError:
            raise
        except Exception as e:
            logger.error("transcription_failed", audio_path=audio_path, error=str(e))
            raise TranscriptionError(f"Transcription failed: {e}")

    @staticmethod
    def _compress_audio(audio_path: str) -> str:
        """Compress WAV to MP3 using ffmpeg to fit Groq's 25MB limit."""
        import subprocess

        output_path = audio_path.rsplit(".", 1)[0] + "_compressed.mp3"
        cmd = [
            "ffmpeg",
            "-i", audio_path,
            "-vn",
            "-acodec", "libmp3lame",
            "-ab", "64k",      # 64kbps is enough for speech
            "-ar", "16000",    # 16kHz sample rate
            "-ac", "1",        # mono
            "-y",
            output_path,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if result.returncode != 0:
            raise TranscriptionError(f"Audio compression failed: {result.stderr}")
        return output_path
