"""Injectable structured logger and redaction helpers (CONS-LOG-*)."""

from __future__ import annotations

import json
import sys
from enum import Enum
from typing import Any, Protocol, TextIO
from urllib.parse import urlsplit, urlunsplit


class LogLevel(str, Enum):
    DEBUG = "debug"
    INFO = "info"
    WARN = "warn"
    ERROR = "error"


class Logger(Protocol):
    """Structured logger interface (CONS-LOG-1)."""

    def debug(self, msg: str, **fields: Any) -> None: ...

    def info(self, msg: str, **fields: Any) -> None: ...

    def warn(self, msg: str, **fields: Any) -> None: ...

    def error(self, msg: str, **fields: Any) -> None: ...


_LEVEL_ORDER = {
    LogLevel.DEBUG: 0,
    LogLevel.INFO: 1,
    LogLevel.WARN: 2,
    LogLevel.ERROR: 3,
}


def _format_value(val: Any) -> str:
    if isinstance(val, bool):
        return "true" if val else "false"
    if isinstance(val, (int, float)):
        return str(val)
    text = str(val)
    if text == "" or any(ch.isspace() or ch in '="' for ch in text):
        return json.dumps(text)
    return text


class StderrLogger:
    """Default structured line logger writing to stderr (CONS-LOG-1, CONS-LOG-6)."""

    def __init__(
        self,
        *,
        min_level: LogLevel = LogLevel.INFO,
        stream: TextIO | None = None,
    ) -> None:
        self._min_level = min_level
        self._stream = stream

    def _write(self, level: LogLevel, msg: str, fields: dict[str, Any]) -> None:
        if _LEVEL_ORDER[level] < _LEVEL_ORDER[self._min_level]:
            return
        ordered: dict[str, Any] = {}
        ordered["event"] = fields.get("event", msg)
        ordered["level"] = level.value
        for k, v in fields.items():
            if k != "event":
                ordered[k] = v
        line = " ".join(f"{k}={_format_value(v)}" for k, v in ordered.items())
        target = self._stream if self._stream is not None else sys.stderr
        target.write(line + "\n")
        target.flush()

    def debug(self, msg: str, **fields: Any) -> None:
        self._write(LogLevel.DEBUG, msg, fields)

    def info(self, msg: str, **fields: Any) -> None:
        self._write(LogLevel.INFO, msg, fields)

    def warn(self, msg: str, **fields: Any) -> None:
        self._write(LogLevel.WARN, msg, fields)

    def error(self, msg: str, **fields: Any) -> None:
        self._write(LogLevel.ERROR, msg, fields)


def emit_event(logger: Logger, level: LogLevel, event: str, **fields: Any) -> None:
    """Emit a structured event with ``event`` as the first field (CONS-LOG-3)."""
    payload = {"event": event, **fields}
    if level is LogLevel.DEBUG:
        logger.debug(event, **payload)
    elif level is LogLevel.INFO:
        logger.info(event, **payload)
    elif level is LogLevel.WARN:
        logger.warn(event, **payload)
    else:
        logger.error(event, **payload)


def short_digest(digest: str) -> str:
    """Truncate ``sha256:<hex>`` to 12 hex characters (CONS-LOG-11)."""
    prefix = "sha256:"
    if not digest.startswith(prefix):
        return digest
    return prefix + digest[len(prefix) : len(prefix) + 12]


def redact_url(raw: str) -> str:
    """Remove userinfo from a URL before logging (CONS-LOG-10)."""
    try:
        parts = urlsplit(raw)
    except ValueError:
        return "<unparseable>"
    netloc = parts.netloc
    if "@" in netloc:
        netloc = netloc.rsplit("@", 1)[1]
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


def truncate(text: str, limit: int = 4096) -> str:
    """Truncate text to at most ``limit`` characters (CONS-HTTP-17)."""
    if len(text) <= limit:
        return text
    return text[:limit]
