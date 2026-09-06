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

# Expose local default port. Cloud Run sets PORT=8080 at runtime.
EXPOSE 8000

# Run with uvicorn
CMD ["sh", "-c", "alembic upgrade head || echo 'Alembic migration skipped; continuing startup'; exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
