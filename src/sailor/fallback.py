"""Fallback resource resolution.

When a primary fetch fails and a resource has ``fallback_enabled=True``, the
client attempts to read a fallback file at::

    {SAILOR_FALLBACK_BASE_URL}/{app}-{kind}.sailor.fall

The base may be a local directory path or an ``http(s)://`` URL, matching the
behaviour of ``sailor-go``.
"""

from __future__ import annotations

import os
from pathlib import Path

import httpx

from .errors import FetchFallbackFailedError
from .options import ResourceKind

ENV_FALLBACK_BASE_URL = "SAILOR_FALLBACK_BASE_URL"


def _fallback_filename(app: str, kind: ResourceKind) -> str:
    return f"{app}-{kind.value}.sailor.fall"


def load_fallback(
    app: str,
    kind: ResourceKind,
    *,
    base_url: str | None = None,
    timeout: float = 30.0,
) -> bytes:
    """Read the fallback payload for ``app``/``kind`` or raise.

    Raises :class:`FetchFallbackFailedError` if no base is configured or the
    fallback source cannot be read.
    """
    base = base_url if base_url is not None else os.environ.get(ENV_FALLBACK_BASE_URL)
    if not base:
        raise FetchFallbackFailedError(
            f"no fallback configured ({ENV_FALLBACK_BASE_URL} unset) for {app}/{kind.value}"
        )

    filename = _fallback_filename(app, kind)

    if base.startswith(("http://", "https://")):
        url = f"{base.rstrip('/')}/{filename}"
        try:
            resp = httpx.get(url, timeout=timeout)
            resp.raise_for_status()
            return resp.content
        except httpx.HTTPError as exc:
            raise FetchFallbackFailedError(f"fallback fetch failed: {url}") from exc

    path = Path(base) / filename
    try:
        return path.read_bytes()
    except OSError as exc:
        raise FetchFallbackFailedError(f"fallback read failed: {path}") from exc
