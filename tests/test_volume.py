"""Conformance tests C-34, C-36, C-37 and VOLUME watcher edge cases."""

from __future__ import annotations

from pathlib import Path

from sailor import (
    ConnectionOption,
    Consumer,
    FetchDefinition,
    FetchOption,
    InitOption,
    ResourceDefinition,
    defaults,
    resource,
)
from sailor.volume import WATCH_DEBOUNCE_SECONDS

from .fakes import FakeWatcher, Harness


def _conn() -> ConnectionOption:
    return ConnectionOption(
        addr="https://sailor.test",
        namespace="ns",
        app="app",
        token="tok",
    )


def test_c34_volume_reads_from_dir_underscore_name():
    h = Harness()
    h.fs.write_bytes(Path("/etc/sailor/_config"), b'{"port":8081}')

    c = Consumer(
        InitOption(
            connection=_conn(),
            resources=[defaults.config_map_default()],
            http_client=h.http_client(),
            logger=h.logger,
            watch=False,
        ),
        _deps=h.deps(),
    )
    c.start()
    try:
        assert c.get("config", dict) == {"port": 8081}
    finally:
        c.close()


def test_c36_volume_symlink_swap_debounced_and_reports_once():
    h = Harness()
    h.fs.write_bytes(Path("/etc/sailor/_config"), b'{"v":1}')

    c = Consumer(
        InitOption(
            connection=_conn(),
            resources=[defaults.config_map_default()],
            http_client=h.http_client(),
            logger=h.logger,
            watch=True,
        ),
        _deps=h.deps(),
    )
    c.start()
    try:
        assert h.watcher.watched_dirs == ["/etc/sailor"]
        assert c.get("config") == {"v": 1}
        assert len(h.server.reports) == 1

        # Simulate Kubernetes symlink swap bursting multiple filesystem events.
        h.fs.write_bytes(Path("/etc/sailor/_config"), b'{"v":2}')
        h.watcher.emit("/etc/sailor/..data", kind="created")
        h.clock.wait_for_timers(1)
        h.watcher.emit("/etc/sailor/_config", kind="moved")
        h.clock.wait_for_timers(1)

        # Before 500ms debounce elapses, value has not updated yet.
        h.clock.advance(0.2)
        assert c.get("config") == {"v": 1}

        # After 500ms quiet period, single reread + single change report fires.
        h.clock.advance(WATCH_DEBOUNCE_SECONDS)
        assert c.get("config") == {"v": 2}

        # Wait briefly for async report thread to complete by closing consumer.
        c.close()
        assert len(h.server.reports) == 2
        assert len(h.logger.by_name("resource.changed")) == 1
    finally:
        c.close()


def test_c37_volume_reread_preserves_version():
    h = Harness()
    # Start with PULL? No, test StateManager / VolumeReader directly or seed version via state:
    # Actually CONS-STATE-3 also covers VOLUME keeping a known version when re-read has none.
    h.fs.write_bytes(Path("/etc/sailor/_config"), b'{"v":1}')
    c = Consumer(
        InitOption(
            connection=_conn(),
            resources=[defaults.config_map_default()],
            http_client=h.http_client(),
            logger=h.logger,
            watch=True,
        ),
        _deps=h.deps(),
    )
    c.start()
    try:
        # Simulate a prior known version on the state record (e.g. from an earlier source).
        state = c._by_name["config"]
        with state.lock:
            state.version = "42"

        h.fs.write_bytes(Path("/etc/sailor/_config"), b'{"v":2}')
        h.watcher.emit("/etc/sailor/_config", kind="modified")
        h.clock.wait_for_timers(1)
        h.clock.advance(WATCH_DEBOUNCE_SECONDS)

        assert c.get("config") == {"v": 2}
        assert state.known_version() == "42"
    finally:
        c.close()


def test_volume_unmatched_event_rereads_all_and_decode_failure_keeps_old():
    h = Harness()
    h.fs.write_bytes(Path("/dir1/_a"), b'{"a":1}')
    h.fs.write_bytes(Path("/dir2/_b"), b'{"b":1}')

    c = Consumer(
        InitOption(
            connection=_conn(),
            resources=[
                resource(
                    ResourceDefinition(name="a", path="/dir1"),
                    FetchDefinition(fetch=FetchOption.VOLUME),
                ),
                resource(
                    ResourceDefinition(name="b", path="/dir2"),
                    FetchDefinition(fetch=FetchOption.VOLUME),
                ),
            ],
            http_client=h.http_client(),
            logger=h.logger,
            watch=True,
        ),
        _deps=h.deps(),
    )
    c.start()
    try:
        h.fs.write_bytes(Path("/dir1/_a"), b"{broken-json")
        h.fs.write_bytes(Path("/dir2/_b"), b'{"b":2}')
        # Unmatched event (no path) re-reads all VOLUME resources (CONS-VOL-6).
        h.watcher.emit(None, kind="modified")
        h.clock.wait_for_timers(1)
        h.clock.advance(WATCH_DEBOUNCE_SECONDS)

        assert c.get("a") == {"a": 1}  # kept last good value
        assert c.get("b") == {"b": 2}
        assert len(h.logger.by_name("resource.decode_failed")) == 1
        assert len(h.logger.by_name("resource.stale")) == 1
    finally:
        c.close()


def test_watcher_failures_are_logged_without_failing_start():
    h = Harness(watcher=FakeWatcher(fail_start=True))
    h.fs.write_bytes(Path("/etc/sailor/_config"), b'{"ok":true}')

    c = Consumer(
        InitOption(
            connection=_conn(),
            resources=[defaults.config_map_default()],
            http_client=h.http_client(),
            logger=h.logger,
            watch=True,
        ),
        _deps=h.deps(),
    )
    c.start()
    try:
        assert c.get("config") == {"ok": True}
        errs = h.logger.by_name("watcher.failed")
        assert len(errs) == 1
        assert errs[0].fields["path"] == ""
    finally:
        c.close()

    h2 = Harness(watcher=FakeWatcher(fail_add=True))
    h2.fs.write_bytes(Path("/etc/sailor/_config"), b'{"ok":true}')
    c2 = Consumer(
        InitOption(
            connection=_conn(),
            resources=[defaults.config_map_default()],
            http_client=h2.http_client(),
            logger=h2.logger,
            watch=True,
        ),
        _deps=h2.deps(),
    )
    c2.start()
    try:
        errs2 = h2.logger.by_name("watcher.failed")
        assert len(errs2) == 1
        assert errs2[0].fields["path"] == "/etc/sailor"
    finally:
        c2.close()
