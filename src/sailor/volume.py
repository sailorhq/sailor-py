"""VOLUME fetch engine and shared debounced directory watcher (CONS-VOL-*)."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Callable

import httpx

from .connection import ResolvedConnection
from .deps import _Deps, _TimerHandle, _Watcher
from .fallback import apply_fallback
from .logger import Logger, LogLevel, emit_event
from .options import FetchOption
from .state import ResourceState, StateManager, mode_name

WATCH_DEBOUNCE_SECONDS = 0.5  # CONS-VOL-5


class VolumeReader:
    """Loads initial VOLUME resources from ``{Path}/_{Name}`` with fallback."""

    def __init__(
        self,
        conn: ResolvedConnection,
        state_mgr: StateManager,
        deps: _Deps,
        logger: Logger,
        *,
        http_client: httpx.Client | None = None,
        request_timeout: float = 10.0,
    ) -> None:
        self._conn = conn
        self._state_mgr = state_mgr
        self._deps = deps
        self._logger = logger
        self._http_client = http_client
        self._request_timeout = request_timeout

    def load_initial(self, state: ResourceState) -> None:
        """Read ``{Path}/_{Name}`` or fall back if missing/unreadable (CONS-VOL-1/2)."""
        now = self._deps.clock.now()
        try:
            data = self._deps.fs.read_bytes(state.volume_file())
        except OSError as exc:
            apply_fallback(
                self._conn.app,
                state,
                self._state_mgr,
                self._deps,
                self._logger,
                http_client=self._http_client,
                timeout=self._request_timeout,
                reason=f"volume file unreadable: {exc}",
            )
            return

        try:
            self._state_mgr.apply(state, data, "", "volume", now)
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

    def reread(self, state: ResourceState) -> None:
        """Re-read a VOLUME file after a debounced watch event (CONS-VOL-6/7)."""
        now = self._deps.clock.now()
        try:
            data = self._deps.fs.read_bytes(state.volume_file())
        except OSError as exc:
            self._state_mgr.mark_stale(state, f"read: {exc}", now)
            return

        try:
            self._state_mgr.apply(state, data, "", "volume", now)
        except Exception as err:
            self._state_mgr.note_decode_failure(state, data, err)
            self._state_mgr.mark_stale(state, f"decode: {err}", now)
            return
        self._state_mgr.mark_recovered(state, now)


class DirectoryWatcher:
    """Watches directories of VOLUME and DEV resources with 500 ms debouncing."""

    def __init__(
        self,
        states: list[ResourceState],
        deps: _Deps,
        logger: Logger,
        stop_event: threading.Event,
        spawn: Callable[[Callable[[], None], str], None],
        on_reread: Callable[[ResourceState], None],
    ) -> None:
        self._states = states
        self._deps = deps
        self._logger = logger
        self._stop = stop_event
        self._spawn = spawn
        self._on_reread = on_reread

        self._lock = threading.Lock()
        self._watcher: _Watcher | None = None
        self._pending: list[ResourceState] = []
        self._timer: _TimerHandle | None = None

    def start(self) -> None:
        """Start watching unique resource directories (CONS-VOL-3, CONS-ERR-2)."""
        dirs: list[str] = []
        seen: set[str] = set()
        for s in self._states:
            if s.mode in (FetchOption.VOLUME, FetchOption.DEV) and s.dir and s.dir not in seen:
                seen.add(s.dir)
                dirs.append(s.dir)
        if not dirs:
            return

        try:
            watcher = self._deps.watcher_factory()
        except Exception as exc:
            emit_event(self._logger, LogLevel.ERROR, "watcher.failed", path="", cause=str(exc))
            return

        for directory in dirs:
            try:
                watcher.add(directory)
            except Exception as exc:
                emit_event(
                    self._logger,
                    LogLevel.ERROR,
                    "watcher.failed",
                    path=directory,
                    cause=str(exc),
                )

        try:
            watcher.start()
        except Exception as exc:
            emit_event(self._logger, LogLevel.ERROR, "watcher.failed", path="", cause=str(exc))
            return

        with self._lock:
            self._watcher = watcher
        self._spawn(lambda: self._watch_loop(watcher), "sailor-watcher")

    def _watch_loop(self, watcher: _Watcher) -> None:
        while not self._stop.is_set():
            ev = watcher.events.get()
            if ev is None or self._stop.is_set():
                return
            with self._lock:
                self._mark_dirty_locked(ev.path)
                if self._timer is not None:
                    self._timer.cancel()
                self._timer = self._deps.clock.schedule_after(
                    WATCH_DEBOUNCE_SECONDS,
                    self._flush_pending,
                )

    def _mark_dirty_locked(self, event_path: str | None) -> None:
        base = Path(event_path).name if event_path else ""
        matched = False
        if base:
            for s in self._states:
                if (s.mode is FetchOption.VOLUME and base == f"_{s.name}") or (
                    s.mode is FetchOption.DEV
                    and s.cache_path is not None
                    and base == s.cache_path.name
                ):
                    if s not in self._pending:
                        self._pending.append(s)
                    matched = True
        if matched:
            return
        for s in self._states:
            if s.mode is FetchOption.VOLUME and s not in self._pending:
                self._pending.append(s)

    def _flush_pending(self) -> None:
        if self._stop.is_set():
            return
        with self._lock:
            batch = list(self._pending)
            self._pending.clear()
            self._timer = None
        for s in batch:
            if self._stop.is_set():
                return
            self._on_reread(s)

    def stop(self) -> None:
        with self._lock:
            watcher = self._watcher
            self._watcher = None
            timer = self._timer
            self._timer = None
            self._pending.clear()
        if timer is not None:
            timer.cancel()
        if watcher is not None:
            watcher.close()
