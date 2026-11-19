"""In-process fakes for deterministic, sleep-free conformance testing (CONS-TEST-1..4)."""

from __future__ import annotations

import json
import queue
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import httpx

from sailor.deps import _Deps, _TimerHandle, _WatchEvent


@dataclass
class RecordedEvent:
    level: str
    msg: str
    fields: dict[str, Any]


class RecordingLogger:
    """Thread-safe structured logger capturing every emitted event."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.events: list[RecordedEvent] = []

    def _record(self, level: str, msg: str, fields: dict[str, Any]) -> None:
        with self._lock:
            self.events.append(RecordedEvent(level=level, msg=msg, fields=dict(fields)))

    def debug(self, msg: str, **fields: Any) -> None:
        self._record("debug", msg, fields)

    def info(self, msg: str, **fields: Any) -> None:
        self._record("info", msg, fields)

    def warn(self, msg: str, **fields: Any) -> None:
        self._record("warn", msg, fields)

    def error(self, msg: str, **fields: Any) -> None:
        self._record("error", msg, fields)

    def by_name(self, event_name: str) -> list[RecordedEvent]:
        with self._lock:
            return [e for e in self.events if e.fields.get("event") == event_name]

    def all_serialized(self) -> str:
        with self._lock:
            return "\n".join(
                f"{e.level} {e.msg} {json.dumps(e.fields, sort_keys=True)}" for e in self.events
            )


class FakeFS:
    """In-memory filesystem tracking file bytes and POSIX modes."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.files: dict[Path, bytes] = {}
        self.file_modes: dict[Path, int] = {}
        self.dir_modes: dict[Path, int] = {}

    def read_bytes(self, path: Path) -> bytes:
        with self._lock:
            if path not in self.files:
                raise FileNotFoundError(str(path))
            return self.files[path]

    def write_bytes(self, path: Path, data: bytes, mode: int = 0o644) -> None:
        with self._lock:
            self.files[path] = data
            self.file_modes[path] = mode

    def mkdir_p(self, path: Path, mode: int = 0o755) -> None:
        with self._lock:
            self.dir_modes[path] = mode


@dataclass
class _Waiter:
    deadline: float
    stop_event: threading.Event
    fired: bool = False


@dataclass
class _FakeTimerHandle:
    timer_id: int
    clock: FakeClock
    cancelled: bool = False

    def cancel(self) -> None:
        self.cancelled = True


@dataclass
class _ScheduledTimer:
    timer_id: int
    deadline: float
    callback: Callable[[], None]
    handle: _FakeTimerHandle


class FakeClock:
    """Deterministic virtual clock; tests advance time without sleeping (CONS-TEST-2)."""

    def __init__(self, initial: float = 1000.0) -> None:
        self._cond = threading.Condition()
        self._now = initial
        self._waiters: list[_Waiter] = []
        self._timers: list[_ScheduledTimer] = []
        self._next_id = 1

    def now(self) -> float:
        with self._cond:
            return self._now

    def wait(self, seconds: float, stop_event: threading.Event) -> bool:
        with self._cond:
            if stop_event.is_set():
                return True
            waiter = _Waiter(deadline=self._now + seconds, stop_event=stop_event)
            self._waiters.append(waiter)
            self._cond.notify_all()
            while not waiter.fired and not stop_event.is_set():
                # Poll stop_event on condition wait only when woken by notify_all or stop.
                if stop_event.is_set():
                    break
                self._cond.wait(timeout=0.05)
            if waiter in self._waiters:
                self._waiters.remove(waiter)
            return stop_event.is_set()

    def schedule_after(self, seconds: float, callback: Callable[[], None]) -> _TimerHandle:
        with self._cond:
            tid = self._next_id
            self._next_id += 1
            handle = _FakeTimerHandle(timer_id=tid, clock=self)
            self._timers.append(
                _ScheduledTimer(
                    timer_id=tid,
                    deadline=self._now + seconds,
                    callback=callback,
                    handle=handle,
                )
            )
            self._cond.notify_all()
            return handle

    def wait_for_waiters(self, count: int, timeout: float = 2.0) -> None:
        with self._cond:
            ok = self._cond.wait_for(lambda: len(self._waiters) >= count, timeout=timeout)
            if not ok:
                raise TimeoutError(
                    f"expected {count} clock waiters, got {len(self._waiters)}"
                )

    def wait_for_timers(self, count: int, timeout: float = 2.0) -> None:
        with self._cond:
            ok = self._cond.wait_for(
                lambda: sum(1 for t in self._timers if not t.handle.cancelled) >= count,
                timeout=timeout,
            )
            if not ok:
                raise TimeoutError(f"expected {count} active scheduled timers")

    def advance(self, seconds: float) -> None:
        due_callbacks: list[Callable[[], None]] = []
        with self._cond:
            self._now += seconds
            remaining_waiters: list[_Waiter] = []
            for waiter in self._waiters:
                if waiter.deadline <= self._now:
                    waiter.fired = True
                else:
                    remaining_waiters.append(waiter)
            self._waiters = remaining_waiters
            remaining: list[_ScheduledTimer] = []
            for t in self._timers:
                if t.handle.cancelled:
                    continue
                if t.deadline <= self._now:
                    due_callbacks.append(t.callback)
                else:
                    remaining.append(t)
            self._timers = remaining
            self._cond.notify_all()
        for cb in due_callbacks:
            cb()


class FakeWatcher:
    """Controllable directory watcher for VOLUME and DEV tests."""

    def __init__(self, *, fail_add: bool = False, fail_start: bool = False) -> None:
        self.events: queue.Queue[_WatchEvent | None] = queue.Queue()
        self.watched_dirs: list[str] = []
        self.started = False
        self.closed = False
        self._fail_add = fail_add
        self._fail_start = fail_start

    def add(self, path: str) -> None:
        if self._fail_add:
            raise OSError(f"cannot watch {path}")
        self.watched_dirs.append(path)

    def start(self) -> None:
        if self._fail_start:
            raise OSError("watcher start failed")
        self.started = True

    def emit(self, path: str | None, kind: str = "modified") -> None:
        self.events.put(_WatchEvent(path=path, kind=kind))

    def close(self) -> None:
        if not self.closed:
            self.closed = True
            self.events.put(None)


@dataclass
class ResourceResponse:
    status_code: int = 200
    body: bytes = b"{}"
    version: str = ""
    etag: str = ""
    encrypted: str = ""
    raise_error: Exception | None = None


@dataclass
class RecordedRequest:
    method: str
    url: str
    headers: dict[str, str]
    body: bytes


class FakeServer(httpx.BaseTransport):
    """In-process fake Sailor HTTP server implementing the section 5 wire contract."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.resources: dict[str, ResourceResponse] = {}
        self.fallbacks: dict[str, ResourceResponse] = {}
        self.respect_if_none_match = False
        self.report_status = 200
        self.report_body = b'{"message":"ok"}'
        self.report_gate: threading.Event | None = None
        self.report_started: threading.Event | None = None
        self.requests: list[RecordedRequest] = []
        self.reports: list[dict[str, Any]] = []
        self.report_headers: list[dict[str, str]] = []

    def set_resource(
        self,
        name: str,
        body: bytes,
        *,
        status_code: int = 200,
        version: str = "1",
        etag: str = "",
        encrypted: str = "",
        raise_error: Exception | None = None,
    ) -> None:
        with self._lock:
            self.resources[name] = ResourceResponse(
                status_code=status_code,
                body=body,
                version=version,
                etag=etag,
                encrypted=encrypted,
                raise_error=raise_error,
            )

    def set_fallback(
        self,
        filename: str,
        body: bytes,
        *,
        status_code: int = 200,
    ) -> None:
        with self._lock:
            self.fallbacks[filename] = ResourceResponse(status_code=status_code, body=body)

    def resource_gets(self, name: str | None = None) -> list[RecordedRequest]:
        with self._lock:
            out = [
                r
                for r in self.requests
                if r.method == "GET" and "/api/v1/resource/" in r.url
            ]
            if name is not None:
                out = [r for r in out if r.url.endswith(f"/{name}")]
            return out

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        body = request.content
        headers = dict(request.headers)
        with self._lock:
            self.requests.append(
                RecordedRequest(
                    method=request.method,
                    url=str(request.url),
                    headers=headers,
                    body=body,
                )
            )

        path = request.url.path
        if request.method == "POST" and path.endswith("/consumer-report"):
            if self.report_started is not None:
                self.report_started.set()
            if self.report_gate is not None:
                self.report_gate.wait(timeout=5.0)
            with self._lock:
                self.report_headers.append(headers)
                if body:
                    self.reports.append(json.loads(body.decode("utf-8")))
                status = self.report_status
                resp_body = self.report_body
            return httpx.Response(status, content=resp_body)

        if request.method == "GET" and "/api/v1/resource/" in path:
            res_name = path.rsplit("/", 1)[-1]
            with self._lock:
                spec = self.resources.get(res_name)
                respect_inm = self.respect_if_none_match
            if spec is None:
                return httpx.Response(
                    404, json={"message": "this resource was never deployed!"}
                )
            if spec.raise_error is not None:
                raise spec.raise_error
            inm = headers.get("if-none-match", "")
            current_ver = spec.etag or spec.version
            if (
                respect_inm
                and spec.status_code == 200
                and inm
                and current_ver
                and inm == current_ver
            ):
                return httpx.Response(304)
            resp_headers: dict[str, str] = {}
            if spec.version:
                resp_headers["x-resource-version"] = spec.version
            if spec.etag:
                resp_headers["ETag"] = spec.etag
            if spec.encrypted:
                resp_headers["x-resource-encrypted"] = spec.encrypted
            return httpx.Response(spec.status_code, content=spec.body, headers=resp_headers)

        # Fallback endpoint
        if request.method == "GET":
            fname = path.rsplit("/", 1)[-1]
            with self._lock:
                fb = self.fallbacks.get(fname)
            if fb is None:
                return httpx.Response(404, content=b"fallback not found")
            return httpx.Response(fb.status_code, content=fb.body)

        return httpx.Response(404)

    def client(self) -> httpx.Client:
        return httpx.Client(transport=self)


@dataclass
class Harness:
    """Bundled test seams ensuring tests never touch real env/home/hostname/clock."""

    env: dict[str, str] = field(default_factory=dict)
    home: Path = Path("/fake-home")
    host: str = "test-host"
    fs: FakeFS = field(default_factory=FakeFS)
    clock: FakeClock = field(default_factory=FakeClock)
    watcher: FakeWatcher = field(default_factory=FakeWatcher)
    logger: RecordingLogger = field(default_factory=RecordingLogger)
    server: FakeServer = field(default_factory=FakeServer)

    def deps(self) -> _Deps:
        return _Deps(
            getenv=lambda k: self.env.get(k, ""),
            home_dir=lambda: self.home,
            hostname=lambda: self.host,
            fs=self.fs,
            clock=self.clock,
            watcher_factory=lambda: self.watcher,
        )

    def http_client(self) -> httpx.Client:
        return self.server.client()
