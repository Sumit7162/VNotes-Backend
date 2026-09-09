---
title: VNotes Backend
emoji: 📝
colorFrom: blue
colorTo: indigo
sdk: docker
app_port: 7860
pinned: false
short_description: FastAPI backend for VNotes, an AI note-taker for YouTube.
---

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
| Transcript | [YouTubeTranscripts.co](https://youtubetranscripts.co) REST API |
| Notes | Groq (default) or NVIDIA NIM |

## How a video is processed

1. **Metadata** — the runtime and title come from the YouTube Data API when
   `YOUTUBE_API_KEY` is set, falling back to oEmbed for the title. The Data API
   is preferred because it reports the runtime, which oEmbed does not.
2. **Limits** — the runtime is checked against the free plan before any work
   starts (see below). A video whose runtime no source could report is accepted,
   and the runtime the transcript API reports is recorded instead.
3. **Transcript** — one call to the YouTubeTranscripts.co API. It serves the
   video's native captions where they exist and runs its own Whisper
   transcription where they do not, so nothing here downloads media: no yt-dlp,
   no ffmpeg, and no residential proxy to get past YouTube's datacenter-IP
   block. The API is asynchronous, so the request is enqueued and then polled
   until it completes.
4. **Notes** — a long transcript is sliced so the notes cover the whole video
   rather than only the part that fit in one request, then the parts are merged.
5. **Store** — notes are written to the database and to `notes/`.

## Notes from a transcript you already have

`POST /api/videos/process-transcript` is the second way in. The user uploads or
pastes a transcript and it goes straight to step 4 above - there is no metadata
lookup, no transcript API call, and so no credit spent. It exists for material
the transcript API cannot reach: a lecture recording, a meeting export, a
private or age-restricted video, or a caption file the user already downloaded.

The endpoint takes the raw text, so `.srt` and `.vtt` files can be sent exactly
as they come. `app/services/transcript_input.py` strips the cue numbers,
timings and inline caption markup, drops the repeated lines that rolling
captions produce, and rejoins cues that broke mid-sentence; plain prose passes
through with its paragraphs intact. The frontend reads an uploaded file in the
browser and posts its text, so a paste and a file upload are the same request.

Uploads are not rationed: unlimited in length, unlimited in number, and not
counted against the daily video allowance. The plan's caps exist to ration what
a submission costs - a transcript API credit and the wait for a fetch - and an
upload spends neither, because the text arrives with the request. A runtime is
still estimated from the word count at 150 words per minute, but only so the
finished record has a length to display.

The one remaining bound is a sanity ceiling on the request body, set well past
any real transcript, so that a runaway paste fails with a clear message rather
than an opaque error from the model.

### Transcript API credits

Native captions cost 1 credit per video, the Whisper fallback 1 credit per
minute (rounded up), and a repeat of a video already fetched is free. Set
`TRANSCRIPT_NATIVE_ONLY=true` to skip the Whisper fallback entirely, which caps
the spend at one credit per video at the cost of failing on videos that have no
captions.

### Free plan limits

| Video runtime | Limit |
|---|---|
| under 15 minutes | 10 per day |
| 15–30 minutes | 2 per day |
| over 30 minutes | rejected |

## Running locally

Requires Python 3.12+ and PostgreSQL 16.

```bash
python -m venv .venv
.venv\Scripts\activate        # Windows; use: source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env          # then fill in DATABASE_URL, GOOGLE_CLIENT_ID,
                              # GROQ_API_KEY and TRANSCRIPT_API_KEY
alembic upgrade head

uvicorn app.main:app --reload --port 8000
```

The API serves on <http://localhost:8000>, with interactive docs at `/docs`.

### With Docker

```bash
cp .env.example .env
docker compose up --build
```

That brings up PostgreSQL and the API on 8000.

## Deploying to Hugging Face Spaces

The repository root doubles as a Docker Space: the YAML block at the top of this
file is the Space config (`sdk: docker`, `app_port: 7860`) and the `Dockerfile`
runs as UID 1000, which is what Spaces requires.

```bash
git remote add hf https://huggingface.co/spaces/<user>/<space>
git push hf main
```

Set every secret under **Space → Settings → Variables and secrets** rather than
committing a `.env`; the Space repository is public. The ones the app cannot
start usefully without are `DATABASE_URL`, `GOOGLE_CLIENT_ID`, `GROQ_API_KEY`
and `TRANSCRIPT_API_KEY`, plus `JWT_SECRET_KEY` and a `CORS_ORIGINS` that lists
the deployed frontend.

Two limits are worth knowing. The Space filesystem is ephemeral, so `transcripts/`
and `notes/` are lost on every restart — the database is the durable copy, which
is why notes are written to both. And a free CPU Space is paused after a stretch
of inactivity, so the first request after a pause waits for a cold start.

## Endpoints

| Method | Path | Description |
|---|---|---|
| GET | `/api/health` | Health check |
| POST | `/api/auth/google` | Exchange a Google credential for an app JWT |
| GET | `/api/auth/me` | Current user |
| POST | `/api/videos/process` | Submit a YouTube URL for processing |
| POST | `/api/videos/process-transcript` | Submit a transcript directly, skipping the fetch |
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
├── services/      # Pipeline: metadata, transcript, note generation, limits
└── utils/         # Structured logging
alembic/           # Migrations
tests/
```

## Configuration

Every setting in `app/core/config.py` can be supplied by environment variable or
`.env`; see `.env.example` for the full list. The ones without sensible
defaults are `DATABASE_URL`, `GOOGLE_CLIENT_ID`, `GROQ_API_KEY`, and
`TRANSCRIPT_API_KEY` (create one at
<https://youtubetranscripts.co/dashboard/api-keys>).

## Tests

```bash
pytest tests/ --tb=short -q
```
