# VNotes — Backend

FastAPI service behind VNotes, an AI note-taker for YouTube videos. It takes a
video URL, gets a transcript, and turns it into structured markdown study notes.

The web client lives in a separate repository:
[VNotes-Frontend](https://github.com/Sumit7162/VNotes-Frontend).

## Tech stack

| Concern | Choice |
|---|---|
| API | FastAPI, Python 3.12 |
| Database | PostgreSQL 16, SQLAlchemy 2, Alembic |
| Validation | Pydantic v2 + pydantic-settings |
| Auth | Google Sign-In verified server-side, then app-issued JWTs |
| Transcript | YouTube captions, falling back to Groq Whisper on downloaded audio |
| Notes | Groq (default) or NVIDIA NIM |
| Audio | `downloader/` microservice (yt-dlp + ffmpeg) |

## How a video is processed

1. **Metadata** — the runtime comes from the downloader's yt-dlp probe, the
   title from YouTube's oEmbed API.
2. **Limits** — the runtime is checked against the free plan before any work
   starts (see below).
3. **Transcript** — YouTube's own captions are tried first, because they are
   free and instant.
4. **Audio fallback** — if there are no usable captions, the downloader service
   returns speech-grade mono mp3 (16 kHz, 64 kbps) and Groq Whisper transcribes
   it. The mp3 is roughly 20x smaller than the equivalent PCM wav, which is
   what makes this step take seconds rather than minutes.
5. **Notes** — a long transcript is sliced so the notes cover the whole video
   rather than only the part that fit in one request, then the parts are merged.
6. **Store** — notes are written to the database and to `notes/`.

### Free plan limits

| Video runtime | Limit |
|---|---|
| under 15 minutes | 10 per day |
| 15–30 minutes | 2 per day |
| over 30 minutes | rejected |

## Running locally

Requires Python 3.12+, PostgreSQL 16, and ffmpeg on `PATH`.

```bash
python -m venv .venv
.venv\Scripts\activate        # Windows; use: source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env          # then fill in DATABASE_URL, GOOGLE_CLIENT_ID, GROQ_API_KEY
alembic upgrade head

uvicorn app.main:app --reload --port 8000
```

The API serves on <http://localhost:8000>, with interactive docs at `/docs`.

The downloader is a separate process, and is only needed for videos without
usable captions:

```bash
cd downloader
pip install -r requirements.txt
uvicorn main:app --port 8001
```

### With Docker

```bash
cp .env.example .env
docker compose up --build
```

That brings up PostgreSQL, the API on 8000, and the downloader on 8001.

## Endpoints

| Method | Path | Description |
|---|---|---|
| GET | `/api/health` | Health check |
| POST | `/api/auth/google` | Exchange a Google credential for an app JWT |
| GET | `/api/auth/me` | Current user |
| POST | `/api/videos/process` | Submit a YouTube URL for processing |
| GET | `/api/videos` | List the caller's videos |
| GET | `/api/videos/{id}` | One video, including status |
| DELETE | `/api/videos/{id}` | Delete a video and its files |
| GET | `/api/notes` | List the caller's notes |
| GET | `/api/notes/{video_id}` | Notes for one video |
| GET | `/api/usage` | Usage against the free plan |

## Layout

```
app/
├── api/           # Route handlers
├── core/          # Settings, database session, security
├── middleware/    # Auth dependency
├── models/        # SQLAlchemy ORM models
├── repositories/  # Data access
├── schemas/       # Pydantic request/response models
├── services/      # Pipeline: download, transcribe, generate notes, limits
└── utils/         # Structured logging
alembic/           # Migrations
downloader/        # yt-dlp audio microservice (own Dockerfile)
tests/
```

## Configuration

Every setting in `app/core/config.py` can be supplied by environment variable or
`.env`; see `.env.example` for the full list. The ones without sensible
defaults are `DATABASE_URL`, `GOOGLE_CLIENT_ID`, and `GROQ_API_KEY`.

## Tests

```bash
pytest tests/ --tb=short -q
```
