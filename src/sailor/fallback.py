"""Fallback resource resolution (SAILOR_CONSUMER.md section 7.4)."""

from __future__ import annotations

import os
from typing import Callable

import httpx

from .deps import _Deps
from .errors import FallbackFailedError, ResourceNotLoadedError
from .logger import Logger, LogLevel, emit_event, redact_url
from .state import ResourceState, StateManager, mode_name

ENV_FALLBACK_BASE_URL = "SAILOR_FALLBACK_BASE_URL"


def fallback_url(base: str, app: str, name: str) -> str:
    """Build ``{base}/{app}-{name}.sailor.fall`` with trailing ``/`` stripped (CONS-FB-1)."""
    return f"{base.rstrip('/')}/{app}-{name}.sailor.fall"


def load_fallback(
    app: str,
    name: str,
    *,
    getenv: Callable[[str], str] | None = None,
    http_client: httpx.Client | None = None,
    timeout: float = 10.0,
) -> tuple[bytes, str]:
    """Fetch the fallback payload for ``app``/``name`` without ``x-token`` (CONS-FB-2..6).

    Returns ``(body_bytes, target_url)`` or raises :class:`FallbackFailedError`.
    """
    read_env = getenv if getenv is not None else (lambda k: os.environ.get(k, ""))
    base = read_env(ENV_FALLBACK_BASE_URL)
    if not base:
        raise FallbackFailedError(f"{ENV_FALLBACK_BASE_URL} is not set")

    target = fallback_url(base, app, name)
    owns_client = http_client is None
    client = http_client if http_client is not None else httpx.Client()
    try:
        try:
            resp = client.get(target, timeout=timeout)
        except httpx.HTTPError as exc:
            raise FallbackFailedError(f"fallback fetch failed: {target}", cause=exc) from exc
        if resp.status_code != 200:
            raise FallbackFailedError(f"fallback answered HTTP {resp.status_code}")
        return resp.content, target
    finally:
        if owns_client:
            client.close()


def apply_fallback(
    app: str,
    state: ResourceState,
    state_mgr: StateManager,
    deps: _Deps,
    logger: Logger,
    *,
    http_client: httpx.Client | None,
    timeout: float,
    reason: str,
) -> None:
    """Attempt fallback load for initial VOLUME/PULL failure (CONS-FB-4..7)."""
    try:
        body, target = load_fallback(
            app,
            state.name,
            getenv=deps.getenv,
            http_client=http_client,
            timeout=timeout,
        )
    except FallbackFailedError as fb_err:
        cause = f"{reason}; fallback: {fb_err}"
        emit_event(
            logger,
            LogLevel.ERROR,
            "resource.load_failed",
            resource=state.name,
            mode=mode_name(state.mode),
            cause=cause,
        )
        raise ResourceNotLoadedError(
            f"resource {state.name!r} has no value", cause=fb_err
        ) from fb_err

    emit_event(
        logger,
        LogLevel.WARN,
        "resource.fallback_used",
        resource=state.name,
        reason=reason,
        url=redact_url(target),
    )
    try:
        state_mgr.apply(state, body, "", "fallback", deps.clock.now())
    except Exception as err:
        emit_event(
            logger,
            LogLevel.ERROR,
            "resource.load_failed",
            resource=state.name,
            mode=mode_name(state.mode),
            cause=str(err),
        )
        raise
