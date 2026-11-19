"""Internal dependency seams for deterministic testing (CONS-TEST-1..3)."""

from __future__ import annotations

import os
import queue
import socket
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Protocol

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer
from watchdog.observers.api import BaseObserver

_WATCH_EVENT_TYPES = frozenset({"created", "modified", "moved", "attrib"})


class _FileSystem(Protocol):
    def read_bytes(self, path: Path) -> bytes: ...

    def write_bytes(self, path: Path, data: bytes, mode: int = 0o644) -> None: ...

    def mkdir_p(self, path: Path, mode: int = 0o755) -> None: ...


class _TimerHandle(Protocol):
    def cancel(self) -> None: ...


class _Clock(Protocol):
    def now(self) -> float: ...

    def wait(self, seconds: float, stop_event: threading.Event) -> bool: ...

    def schedule_after(self, seconds: float, callback: Callable[[], None]) -> _TimerHandle: ...


@dataclass(frozen=True)
class _WatchEvent:
    path: str | None
    kind: str


class _Watcher(Protocol):
    events: queue.Queue[_WatchEvent | None]

    def add(self, path: str) -> None: ...

    def start(self) -> None: ...

    def close(self) -> None: ...


class _RealFS:
    def read_bytes(self, path: Path) -> bytes:
        return path.read_bytes()

    def write_bytes(self, path: Path, data: bytes, mode: int = 0o644) -> None:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
        try:
            view = memoryview(data)
            written = 0
            while written < len(data):
                written += os.write(fd, view[written:])
        finally:
            os.close(fd)
        os.chmod(path, mode)

    def mkdir_p(self, path: Path, mode: int = 0o755) -> None:
        missing: list[Path] = []
        cur = path
        while not cur.exists():
            missing.append(cur)
            parent = cur.parent
            if parent == cur:
                break
            cur = parent
        path.mkdir(parents=True, exist_ok=True)
        for d in reversed(missing):
            if d.exists():
                os.chmod(d, mode)


class _RealTimerHandle:
    def __init__(self, timer: threading.Timer) -> None:
        self._timer = timer

    def cancel(self) -> None:
        self._timer.cancel()


class _RealClock:
    def now(self) -> float:
        return time.monotonic()

    def wait(self, seconds: float, stop_event: threading.Event) -> bool:
        return stop_event.wait(seconds)

    def schedule_after(self, seconds: float, callback: Callable[[], None]) -> _TimerHandle:
        timer = threading.Timer(seconds, callback)
        timer.daemon = True
        timer.start()
        return _RealTimerHandle(timer)


class _QueueEventHandler(FileSystemEventHandler):
    def __init__(self, out: queue.Queue[_WatchEvent | None]) -> None:
        self._out = out

    def on_any_event(self, event: FileSystemEvent) -> None:
        if event.event_type not in _WATCH_EVENT_TYPES:
            return
        dest = getattr(event, "dest_path", None)
        raw_path = dest if dest else event.src_path
        path = os.fsdecode(raw_path) if isinstance(raw_path, bytes) else str(raw_path)
        self._out.put(_WatchEvent(path=path, kind=event.event_type))


class _WatchdogWatcher:
    def __init__(self) -> None:
        self.events: queue.Queue[_WatchEvent | None] = queue.Queue()
        self._observer: BaseObserver = Observer()
        self._handler = _QueueEventHandler(self.events)
        self._closed = False

    def add(self, path: str) -> None:
        self._observer.schedule(self._handler, path, recursive=False)

    def start(self) -> None:
        self._observer.start()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._observer.stop()
        self._observer.join(timeout=1.0)
        self.events.put(None)


def _default_getenv(name: str) -> str:
    return os.environ.get(name, "")


def _default_home_dir() -> Path:
    return Path.home()


def _default_hostname() -> str:
    return socket.gethostname()


@dataclass
class _Deps:
    """Internal runtime dependencies; tests override individual fields."""

    getenv: Callable[[str], str] = _default_getenv
    home_dir: Callable[[], Path] = _default_home_dir
    hostname: Callable[[], str] = _default_hostname
    fs: _FileSystem = field(default_factory=_RealFS)
    clock: _Clock = field(default_factory=_RealClock)
    watcher_factory: Callable[[], _Watcher] = _WatchdogWatcher
