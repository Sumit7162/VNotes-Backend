"""Build env.yaml for `gcloud run deploy --env-vars-file` out of .env.

Cloud Run takes its configuration as a YAML mapping rather than a dotenv file,
and a few values need care on the way across: the CORS list is JSON and the
CORS regex is full of backslashes, so everything is emitted as a quoted string
instead of letting the YAML parser guess. The two storage paths are overridden
because the local values point at Windows-relative directories that do not
exist inside the container.

Run it from the backend directory:

    python make_env_yaml.py

It prints only key names and a status column, never the secret values, so the
output is safe to paste into a chat or an issue.
"""

import re
import sys
from pathlib import Path

# Everything the deployed service reads. Anything in .env but not listed here
# (local database URLs, editor settings) is deliberately left behind.
WANTED = [
    "DATABASE_URL",
    "GOOGLE_CLIENT_ID",
    "YOUTUBE_API_KEY",
    "JWT_SECRET_KEY",
    "JWT_ALGORITHM",
    "JWT_ACCESS_TOKEN_EXPIRE_MINUTES",
    "GROQ_API_KEY",
    "GROQ_MODEL",
    "NVIDIA_API_KEY",
    "NVIDIA_BASE_URL",
    "NVIDIA_MODEL",
    "TRANSCRIPT_API_KEY",
    "TRANSCRIPT_API_BASE_URL",
    "TRANSCRIPT_LANGUAGE",
    "TRANSCRIPT_TRANSLATE_TO",
    "TRANSCRIPT_NATIVE_ONLY",
    "TRANSCRIPT_POLL_INTERVAL_SECONDS",
    "TRANSCRIPT_POLL_TIMEOUT_SECONDS",
    "FREE_DAILY_VIDEOS",
    "FREE_DAILY_SHORT_VIDEOS",
    "FREE_MAX_DURATION_MINUTES",
    "LOG_LEVEL",
    "CORS_ORIGINS",
    "CORS_ORIGIN_REGEX",
]

# Secrets the service cannot start usefully without.
REQUIRED = {
    "DATABASE_URL",
    "GOOGLE_CLIENT_ID",
    "JWT_SECRET_KEY",
    "GROQ_API_KEY",
    "TRANSCRIPT_API_KEY",
}


def read_dotenv(path: Path) -> dict:
    env = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        env[key.strip().upper()] = value
    return env


def main() -> int:
    source = Path(".env")
    if not source.exists():
        print("No .env in this directory. Run this from backend/.")
        return 1

    env = read_dotenv(source)

    lines = [
        "# Cloud Run environment, generated from .env by make_env_yaml.py.",
        "# Contains live secrets: never commit it, never paste it anywhere.",
    ]
    report = []
    missing_required = []

    for key in WANTED:
        value = env.get(key, "")
        if not value:
            report.append((key, "missing", ""))
            if key in REQUIRED:
                missing_required.append(key)
            continue
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        lines.append(f'{key}: "{escaped}"')
        local = bool(re.search(r"localhost|127\.0\.0\.1", value))
        report.append((key, "LOCAL VALUE" if local else "ok", f"{len(value)} chars"))

    # The container's writable paths, not the local Windows ones.
    lines.append('TRANSCRIPT_DIR: "/app/transcripts"')
    lines.append('NOTES_DIR: "/app/notes"')

    Path("env.yaml").write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")

    print(f"{'KEY':34} {'STATUS':12} SIZE")
    print("-" * 60)
    for key, status, size in report:
        print(f"{key:34} {status:12} {size}")

    print("\nWrote env.yaml")
    if missing_required:
        print("\nMissing values the service needs: " + ", ".join(missing_required))
    locals_found = [k for k, s, _ in report if s == "LOCAL VALUE"]
    if locals_found:
        print(
            "\nThese still point at localhost and will not work once deployed: "
            + ", ".join(locals_found)
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
