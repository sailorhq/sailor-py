"""The :class:`Consumer` — public entry point of the Sailor v1 client."""

from __future__ import annotations

import threading
from typing import Any, Callable, TypeVar, overload

from .connection import is_loopback_addr, resolve_connection
from .decode import DecoderSet, normalize_lang
from .deps import _Deps
from .dev import DevReader
from .errors import (
    AlreadyStartedError,
    DuplicateResourceError,
    EmptyResourceNameError,
    EmptyResourcesError,
    InvalidFetchModeError,
    InvalidPullIntervalError,
    ResourceNotLoadedError,
    TypeMismatchError,
)
from .logger import Logger, LogLevel, StderrLogger, emit_event, redact_url
from .options import (
    DEFAULT_PULL_INTERVAL_SECONDS,
    DEFAULT_REPORT_TIMEOUT_SECONDS,
    DEFAULT_REQUEST_TIMEOUT_SECONDS,
    MIN_PULL_INTERVAL_SECONDS,
    FetchOption,
    InitOption,
    ResourceOption,
)
from .pull import Poller
from .report import Reporter
from .state import ResourceState, StateManager
from .transport import Transport
from .volume import DirectoryWatcher, VolumeReader

T = TypeVar("T")


def _build_state(ro: ResourceOption) -> ResourceState:
    name = ro.definition.name
    if not name:
        raise EmptyResourceNameError("resource definition name must not be empty")

    interval = ro.fetch_def.pull_interval
    if interval < 0:
        raise InvalidPullIntervalError(f"resource {name!r} has a negative pull_interval")
    if interval == 0:
        interval = DEFAULT_PULL_INTERVAL_SECONDS
    elif interval < MIN_PULL_INTERVAL_SECONDS:
        raise InvalidPullIntervalError(
            f"resource {name!r} pull_interval is below {MIN_PULL_INTERVAL_SECONDS}s"
        )

    mode = ro.fetch_def.fetch
    if mode is FetchOption.VOLUME:
        if not ro.definition.path:
            raise InvalidFetchModeError(f"resource {name!r} is VOLUME but has no path")
    elif mode not in (FetchOption.PULL, FetchOption.DEV):
        raise InvalidFetchModeError(f"resource {name!r} has invalid fetch mode {mode!r}")

    lang = "json" if ro.definition.encrypted else normalize_lang(ro.definition.lang)
    return ResourceState(
        name=name,
        mode=mode,
        once=ro.fetch_def.once,
        interval=interval,
        encrypted=ro.definition.encrypted,
        dir=ro.definition.path,
        lang=lang,
        decode=ro.decode,
    )


class Consumer:
    """Keeps named Sailor resources current in memory (SAILOR_CONSUMER.md)."""

    def __init__(self, init: InitOption, *, _deps: _Deps | None = None) -> None:
        if not init.resources:
            raise EmptyResourcesError("InitOption.resources must not be empty")

        deps = _deps if _deps is not None else _Deps()
        by_name: dict[str, ResourceState] = {}
        ordered: list[ResourceState] = []
        for ro in init.resources:
            state = _build_state(ro)
            if state.name in by_name:
                raise DuplicateResourceError(f"resource {state.name!r} is declared twice")
            by_name[state.name] = state
            ordered.append(state)

        conn = resolve_connection(init, deps=deps)
        logger: Logger = init.logger if init.logger is not None else StderrLogger()
        decoders = DecoderSet(init.decoders)

        self._deps = deps
        self._conn = conn
        self._logger = logger
        self._decoders = decoders
        self._states = ordered
        self._by_name = by_name
        self._watch = True if init.watch is None else init.watch
        self._request_timeout = (
            init.request_timeout if init.request_timeout > 0 else DEFAULT_REQUEST_TIMEOUT_SECONDS
        )
        self._report_timeout = (
            init.report_timeout if init.report_timeout > 0 else DEFAULT_REPORT_TIMEOUT_SECONDS
        )
        self._http_client = init.http_client

        self._lifecycle_lock = threading.Lock()
        self._threads_lock = threading.Lock()
        self._threads: set[threading.Thread] = set()
        self._stop_event = threading.Event()
        self._started = False
        self._closed = False
        self._started_at = 0.0

        self._transport: Transport | None = None
        self._dir_watcher: DirectoryWatcher | None = None

        if conn.token and conn.addr.startswith("http://") and not is_loopback_addr(conn.addr):
            emit_event(
                logger,
                LogLevel.WARN,
                "config.insecure_transport",
                host=redact_url(conn.addr),
            )
        emit_event(
            logger,
            LogLevel.INFO,
            "config.resolved",
            source=conn.source,
            addr=redact_url(conn.addr),
            namespace=conn.namespace,
            app=conn.app,
            token=bool(conn.token),
            resources=len(ordered),
        )

    def _spawn(self, target: Callable[[], None], name: str) -> None:
        thread_holder: list[threading.Thread] = []

        def _runner() -> None:
            try:
                target()
            finally:
                with self._threads_lock:
                    if thread_holder:
                        self._threads.discard(thread_holder[0])

        thread = threading.Thread(target=_runner, name=name, daemon=True)
        thread_holder.append(thread)
        with self._threads_lock:
            if self._closed or self._stop_event.is_set():
                return
            self._threads.add(thread)
            thread.start()

    def start(self) -> None:
        """Load every resource once and start background pollers/watchers/heartbeats."""
        with self._lifecycle_lock:
            if self._started:
                raise AlreadyStartedError("start() has already been called")
            if self._closed:
                raise AlreadyStartedError("consumer is closed")
            self._started = True
            self._started_at = self._deps.clock.now()

        transport = Transport(
            self._conn,
            http_client=self._http_client,
            request_timeout=self._request_timeout,
            report_timeout=self._report_timeout,
        )
        self._transport = transport

        reporter = Reporter(
            self._conn,
            transport,
            self._deps,
            self._logger,
            self._stop_event,
            self._spawn,
        )
        state_mgr = StateManager(
            self._conn,
            self._decoders,
            self._logger,
            reporter.report_async,
        )

        if not self._conn.token:
            for s in self._states:
                reporter.log_no_token(s)

        poller = Poller(
            transport,
            self._conn,
            state_mgr,
            self._deps,
            self._logger,
            self._stop_event,
            self._spawn,
            request_timeout=self._request_timeout,
        )
        volume_reader = VolumeReader(
            self._conn,
            state_mgr,
            self._deps,
            self._logger,
            http_client=transport.client,
            request_timeout=self._request_timeout,
        )
        dev_reader = DevReader(
            transport,
            self._conn,
            state_mgr,
            self._deps,
            self._logger,
        )

        try:
            for s in self._states:
                if s.mode is FetchOption.PULL:
                    poller.load_initial(s)
                elif s.mode is FetchOption.VOLUME:
                    volume_reader.load_initial(s)
                else:
                    dev_reader.load_initial(s)
        except Exception:
            self._stop_event.set()
            self._join_threads()
            transport.close()
            self._transport = None
            raise

        def _reread(state: ResourceState) -> None:
            if state.mode is FetchOption.VOLUME:
                volume_reader.reread(state)
            elif state.mode is FetchOption.DEV:
                dev_reader.reread(state)

        if self._watch:
            watcher = DirectoryWatcher(
                self._states,
                self._deps,
                self._logger,
                self._stop_event,
                self._spawn,
                _reread,
            )
            self._dir_watcher = watcher
            watcher.start()

        pull_states = [s for s in self._states if s.mode is FetchOption.PULL]
        poller.start_loops(pull_states)

        for s in self._states:

            def _hb(st: ResourceState = s) -> None:
                reporter.heartbeat_loop(st)

            self._spawn(_hb, f"sailor-heartbeat-{s.name}")

        emit_event(self._logger, LogLevel.INFO, "consumer.started", resources=len(self._states))

    def _join_threads(self) -> None:
        while True:
            with self._threads_lock:
                pending = list(self._threads)
            if not pending:
                return
            for thread in pending:
                thread.join()

    def close(self) -> None:
        """Stop all background threads and watchers; idempotent (CONS-CONC-4)."""
        with self._lifecycle_lock:
            if self._closed:
                return
            self._closed = True
            started = self._started
            started_at = self._started_at
            watcher = self._dir_watcher
            self._dir_watcher = None
            transport = self._transport
            self._transport = None

        self._stop_event.set()
        if watcher is not None:
            watcher.stop()
        self._join_threads()
        if transport is not None:
            transport.close()

        if started:
            uptime = int(max(0.0, self._deps.clock.now() - started_at))
            emit_event(
                self._logger,
                LogLevel.INFO,
                "consumer.stopped",
                resources=len(self._states),
                uptime_seconds=uptime,
            )

    def __enter__(self) -> Consumer:
        self.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    @overload
    def get(self, name: str, expected_type: type[T]) -> T: ...

    @overload
    def get(self, name: str) -> Any: ...

    def get(self, name: str, expected_type: type[T] | None = None) -> T | Any:
        """Return the current value of ``name``, checking ``expected_type`` if given."""
        state = self._by_name.get(name)
        if state is None:
            raise ResourceNotLoadedError(f"no resource named {name!r} is declared")
        with state.lock:
            if not state.loaded:
                raise ResourceNotLoadedError(f"resource {name!r} has no value yet")
            val = state.value

        if expected_type is not None and not isinstance(val, expected_type):
            raise TypeMismatchError(
                f"resource {name!r} holds {type(val).__name__}, not {expected_type.__name__}"
            )
        return val
