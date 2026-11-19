"""PULL fetch engine: initial fetch with fallback + non-recursive polling loop (CONS-PULL-*)."""

from __future__ import annotations

import threading
from typing import Callable

import httpx

from .connection import ResolvedConnection
from .deps import _Deps
from .fallback import apply_fallback
from .logger import Logger, LogLevel, emit_event
from .state import ResourceState, StateManager, mode_name
from .transport import Transport


class Poller:
    """Manages initial PULL loads and background polling threads."""

    def __init__(
        self,
        transport: Transport,
        conn: ResolvedConnection,
        state_mgr: StateManager,
        deps: _Deps,
        logger: Logger,
        stop_event: threading.Event,
        spawn: Callable[[Callable[[], None], str], None],
        *,
        request_timeout: float = 10.0,
    ) -> None:
        self._transport = transport
        self._conn = conn
        self._state_mgr = state_mgr
        self._deps = deps
        self._logger = logger
        self._stop = stop_event
        self._spawn = spawn
        self._request_timeout = request_timeout

    def load_initial(self, state: ResourceState) -> None:
        """Fetch initially, falling back on transport/non-200 error (CONS-PULL-1)."""
        now = self._deps.clock.now()
        try:
            res = self._transport.fetch_resource(state.name)
        except httpx.HTTPError as exc:
            apply_fallback(
                self._conn.app,
                state,
                self._state_mgr,
                self._deps,
                self._logger,
                http_client=self._transport.client,
                timeout=self._request_timeout,
                reason=f"transport: {exc}",
            )
            return

        self._state_mgr.check_encrypted_header(state, res.encrypted_header)
        if res.status_code != 200:
            apply_fallback(
                self._conn.app,
                state,
                self._state_mgr,
                self._deps,
                self._logger,
                http_client=self._transport.client,
                timeout=self._request_timeout,
                reason=f"HTTP {res.status_code}",
            )
            return

        try:
            self._state_mgr.apply(state, res.content, res.version, "server", now)
        except Exception as err:
            emit_event(
                self._logger,
                LogLevel.ERROR,
                "resource.load_failed",
                resource=state.name,
                mode=mode_name(state.mode),
                cause=str(err),
            )
            raise

    def start_loops(self, pull_states: list[ResourceState]) -> None:
        """Spawn one polling thread per non-``once`` PULL resource (CONS-PULL-2/3)."""
        for state in pull_states:
            if state.once:
                continue

            def _run(s: ResourceState = state) -> None:
                self._poll_loop(s)

            self._spawn(_run, f"sailor-pull-{state.name}")

    def _poll_loop(self, state: ResourceState) -> None:
        """Single loop with timer and stop signal (CONS-DES-4, CONS-PULL-3)."""
        while not self._deps.clock.wait(state.interval, self._stop):
            self._poll_once(state)

    def _poll_once(self, state: ResourceState) -> None:
        now = self._deps.clock.now()
        try:
            res = self._transport.fetch_resource(
                state.name,
                if_none_match=state.known_version(),
            )
        except httpx.HTTPError as exc:
            if not self._stop.is_set():
                self._state_mgr.mark_stale(state, f"transport: {exc}", now)
            return

        self._state_mgr.check_encrypted_header(state, res.encrypted_header)
        if res.status_code == 304:
            self._state_mgr.mark_recovered(state, now)
            return

        if res.status_code == 200:
            try:
                self._state_mgr.apply(state, res.content, res.version, "server", now)
            except Exception as err:
                self._state_mgr.note_decode_failure(state, res.content, err)
                self._state_mgr.mark_stale(state, f"decode: {err}", now)
                return
            self._state_mgr.mark_recovered(state, now)
            return

        self._state_mgr.mark_stale(state, f"HTTP {res.status_code}", now)
