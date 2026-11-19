"""Integration tests for real production seams (RealFS, RealClock, StderrLogger, Watchdog)."""

from __future__ import annotations

import io
import os
import threading

from sailor.deps import _Deps, _RealClock, _RealFS, _WatchdogWatcher
from sailor.logger import LogLevel, StderrLogger, emit_event


def test_real_fs_permissions(tmp_path):
    fs = _RealFS()
    target_dir = tmp_path / "a" / "b"
    fs.mkdir_p(target_dir, 0o755)
    target_file = target_dir / "cache.json"
    fs.write_bytes(target_file, b"{}", 0o644)

    assert fs.read_bytes(target_file) == b"{}"
    assert (os.stat(target_dir).st_mode & 0o777) == 0o755
    assert (os.stat(target_file).st_mode & 0o777) == 0o644


def test_real_clock_wait_and_timer():
    clock = _RealClock()
    stop = threading.Event()
    stop.set()
    assert clock.wait(10.0, stop) is True

    fired = threading.Event()
    handle = clock.schedule_after(0.01, fired.set)
    assert fired.wait(timeout=1.0)
    handle.cancel()


def test_stderr_logger_format_and_level():
    buf = io.StringIO()
    log = StderrLogger(stream=buf)
    emit_event(log, LogLevel.DEBUG, "debug.ignored", a=1)
    emit_event(log, LogLevel.INFO, "config.resolved", namespace="ns", token=True)
    output = buf.getvalue().strip().splitlines()
    assert len(output) == 1
    assert output[0].startswith("event=config.resolved level=info ")
    assert "namespace=ns" in output[0]
    assert "token=true" in output[0]


def test_real_watchdog_watcher(tmp_path):
    watcher = _WatchdogWatcher()
    watcher.add(str(tmp_path))
    watcher.start()
    try:
        p = tmp_path / "_config"
        p.write_bytes(b"hello")
        ev = watcher.events.get(timeout=2.0)
        assert ev is not None
        assert ev.path is not None
    finally:
        watcher.close()


def test_default_deps_constructs():
    d = _Deps()
    assert callable(d.getenv)
    assert callable(d.home_dir)
    assert callable(d.hostname)
