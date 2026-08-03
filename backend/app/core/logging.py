"""Structured JSON logging to stdout.

Cloud Logging's agent parses a JSON line and promotes a top-level `severity`
field to the entry's log level; plain-text lines are all filed as INFO, which
makes severity filters useless. With more than one replica serving, being able
to filter by severity and correlate by build_session_id is the difference
between a usable query and grepping interleaved output from every pod.
"""

import json
import logging
import os
import sys
from contextvars import ContextVar
from datetime import datetime, timezone

# Set once per pipeline run (see BuildRecorder) and read by the formatter, so
# every log line emitted during that run carries the id without each call site
# having to thread it through.
_build_session_id: ContextVar[str | None] = ContextVar("build_session_id", default=None)

# Python level names map 1:1 onto Cloud Logging severities for the levels we use.
_SEVERITY = {
    "DEBUG": "DEBUG",
    "INFO": "INFO",
    "WARNING": "WARNING",
    "ERROR": "ERROR",
    "CRITICAL": "CRITICAL",
}

# Standard LogRecord attributes. Anything outside this set arrived via
# logger.info(..., extra={...}) and is promoted to a top-level JSON field.
_RESERVED_ATTRS = frozenset(
    {
        "args",
        "asctime",
        "created",
        "exc_info",
        "exc_text",
        "filename",
        "funcName",
        "levelname",
        "levelno",
        "lineno",
        "message",
        "module",
        "msecs",
        "msg",
        "name",
        "pathname",
        "process",
        "processName",
        "relativeCreated",
        "stack_info",
        "taskName",
        "thread",
        "threadName",
    }
)


def set_build_session_id(session_id: str | None) -> None:
    """Bind a build session id to the current context (task or thread)."""
    _build_session_id.set(str(session_id) if session_id is not None else None)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict = {
            "severity": _SEVERITY.get(record.levelname, "DEFAULT"),
            "message": record.getMessage(),
            "timestamp": datetime.fromtimestamp(
                record.created, timezone.utc
            ).isoformat(),
            "logger": record.name,
        }

        session_id = _build_session_id.get()
        if session_id:
            payload["build_session_id"] = session_id

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        if record.stack_info:
            payload["stack"] = self.formatStack(record.stack_info)

        for key, value in record.__dict__.items():
            if key not in _RESERVED_ATTRS and not key.startswith("_"):
                payload[key] = value

        # default=str so a UUID/Decimal/datetime in an `extra` can't kill the
        # log line — same guard the SSE encoder uses.
        return json.dumps(payload, default=str)


def configure_logging(level: str | None = None) -> None:
    """Install the JSON formatter as the only root handler.

    Safe to call more than once; handlers are replaced rather than appended.
    """
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level or os.getenv("LOG_LEVEL", "INFO").upper())

    # uvicorn installs its own text handlers at startup. Clear them and let the
    # records propagate to root, otherwise every access line is emitted twice —
    # once as JSON, once as plain text.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        uvicorn_logger = logging.getLogger(name)
        uvicorn_logger.handlers = []
        uvicorn_logger.propagate = True
