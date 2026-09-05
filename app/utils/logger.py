import logging
import sys

import structlog

from app.core.config import get_settings


def _force_utf8_stdio() -> None:
    """Make stdout/stderr able to encode anything the logs carry.

    structlog's PrintLogger writes straight to sys.stdout, which on Windows
    defaults to the ANSI code page (cp1252) for both the console and pipes.
    Logging a video title that contains an emoji therefore raised
    UnicodeEncodeError from inside the logging call and aborted the whole
    processing pipeline. UTF-8 handles every title; errors="replace" keeps a
    stream we cannot switch from ever raising again.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            pass


def setup_logging() -> None:
    settings = get_settings()
    log_level = getattr(logging, settings.log_level.upper(), logging.INFO)

    _force_utf8_stdio()

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.StackInfoRenderer(),
            structlog.dev.set_exc_info,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.dev.ConsoleRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)
