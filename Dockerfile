FROM python:3.12-slim

WORKDIR /app

# Install Python dependencies.
# No system packages are needed: transcripts come from the YouTubeTranscripts.co
# API, so there is no audio to download or re-encode and therefore no ffmpeg.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Create storage directories
RUN mkdir -p transcripts notes

# Hugging Face Spaces runs the container as a non-root user with UID 1000, so
# /app and the two runtime storage directories have to belong to that user or
# startup fails the first time it writes a transcript. Named volumes mounted
# over these paths inherit the ownership from the image, so docker compose
# keeps working too.
RUN useradd -m -u 1000 user && chown -R user:user /app
USER user

# Hugging Face Spaces routes traffic to 7860 (see app_port in README.md).
# Cloud Run and docker compose override PORT.
ENV PORT=7860
EXPOSE 7860

# Run with uvicorn
CMD ["sh", "-c", "alembic upgrade head || echo 'Alembic migration skipped; continuing startup'; exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-7860}"]
