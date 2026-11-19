"""Conformance tests C-44 to C-49: DEV mode cache, sidecar, overrides, and live reload."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import httpx
import pytest

from sailor import (
    ConnectionOption,
    Consumer,
    ErrorCode,
    InitOption,
    ResourceNotLoadedError,
    defaults,
)
from sailor.connection import ResolvedConnection
from sailor.dev import dev_cache_path
from sailor.volume import WATCH_DEBOUNCE_SECONDS

from .fakes import Harness


def _conn() -> ConnectionOption:
    return ConnectionOption(
        addr="https://sailor.test",
        namespace="ns",
        app="app",
        env="sit",
    )


def _cache_file(home: Path, name: str = "config") -> Path:
    rc = ResolvedConnection(
        source="fields", addr="https://sailor.test", namespace="ns", app="app", env="sit"
    )
    return dev_cache_path(home, rc, name)


def test_c44_dev_cold_cache_writes_files_with_modes():
    h = Harness()
    body = b'{"env":"sit"}'
    h.server.set_resource("config", body, version="7")

    c = Consumer(
        InitOption(
            connection=_conn(),
            resources=[defaults.config_dev_default()],
            http_client=h.http_client(),
            logger=h.logger,
            watch=False,
        ),
        _deps=h.deps(),
    )
    c.start()
    try:
        assert c.get("config") == {"env": "sit"}
        cache_p = _cache_file(h.home)
        meta_p = Path(f"{cache_p}.meta.json")
        assert h.fs.read_bytes(cache_p) == body
        assert h.fs.file_modes[cache_p] == 0o644
        assert h.fs.file_modes[meta_p] == 0o644
        assert h.fs.dir_modes[cache_p.parent] == 0o755
        meta = json.loads(h.fs.read_bytes(meta_p))
        assert meta == {"version": "7", "hash": hashlib.sha256(body).hexdigest()}
    finally:
        c.close()


def test_c45_dev_pristine_updates_on_new_server_digest():
    h = Harness()
    cache_p = _cache_file(h.home)
    old_body = b'{"v":1}'
    h.fs.write_bytes(cache_p, old_body)
    h.fs.write_bytes(
        Path(f"{cache_p}.meta.json"),
        json.dumps({"version": "1", "hash": hashlib.sha256(old_body).hexdigest()}).encode(),
    )
    new_body = b'{"v":2}'
    h.server.set_resource("config", new_body, version="2")

    c = Consumer(
        InitOption(
            connection=_conn(),
            resources=[defaults.config_dev_default()],
            http_client=h.http_client(),
            logger=h.logger,
            watch=False,
        ),
        _deps=h.deps(),
    )
    c.start()
    try:
        assert c.get("config") == {"v": 2}
        assert h.fs.read_bytes(cache_p) == new_body
        meta = json.loads(h.fs.read_bytes(Path(f"{cache_p}.meta.json")))
        assert meta["version"] == "2"
        assert meta["hash"] == hashlib.sha256(new_body).hexdigest()
    finally:
        c.close()


def test_c46_dev_pristine_server_unreachable_uses_cache_and_marks_stale():
    h = Harness()
    cache_p = _cache_file(h.home)
    cached = b'{"cached":true}'
    h.fs.write_bytes(cache_p, cached)
    h.fs.write_bytes(
        Path(f"{cache_p}.meta.json"),
        json.dumps({"version": "5", "hash": hashlib.sha256(cached).hexdigest()}).encode(),
    )
    h.server.set_resource("config", b"", raise_error=httpx.ConnectError("down"))

    c = Consumer(
        InitOption(
            connection=_conn(),
            resources=[defaults.config_dev_default()],
            http_client=h.http_client(),
            logger=h.logger,
            watch=False,
        ),
        _deps=h.deps(),
    )
    c.start()
    try:
        assert c.get("config") == {"cached": True}
        assert len(h.logger.by_name("resource.stale")) == 1
    finally:
        c.close()


def test_c47_dev_locally_edited_keeps_file_and_logs_override():
    h = Harness()
    cache_p = _cache_file(h.home)
    orig = b'{"v":1}'
    edited = b'{"v":999}'
    h.fs.write_bytes(cache_p, edited)
    h.fs.write_bytes(
        Path(f"{cache_p}.meta.json"),
        json.dumps({"version": "1", "hash": hashlib.sha256(orig).hexdigest()}).encode(),
    )
    h.server.set_resource("config", b'{"v":2}', version="2")

    c = Consumer(
        InitOption(
            connection=_conn(),
            resources=[defaults.config_dev_default()],
            http_client=h.http_client(),
            logger=h.logger,
            watch=False,
        ),
        _deps=h.deps(),
    )
    c.start()
    try:
        assert c.get("config") == {"v": 999}
        assert h.fs.read_bytes(cache_p) == edited
        assert len(h.logger.by_name("resource.local_override")) == 1
        assert h.server.resource_gets("config") == []
    finally:
        c.close()


def test_c48_dev_missing_sidecar_treated_as_local_edit():
    h = Harness()
    cache_p = _cache_file(h.home)
    h.fs.write_bytes(cache_p, b'{"hand":true}')
    h.server.set_resource("config", b'{"server":true}', version="2")

    c = Consumer(
        InitOption(
            connection=_conn(),
            resources=[defaults.config_dev_default()],
            http_client=h.http_client(),
            logger=h.logger,
            watch=False,
        ),
        _deps=h.deps(),
    )
    c.start()
    try:
        assert c.get("config") == {"hand": True}
        assert len(h.logger.by_name("resource.local_override")) == 1
    finally:
        c.close()


def test_c49_dev_no_polling_after_start_and_live_reload_on_edit():
    h = Harness()
    h.server.set_resource("config", b'{"v":1}', version="1")

    c = Consumer(
        InitOption(
            connection=_conn(),
            resources=[defaults.config_dev_default()],
            http_client=h.http_client(),
            logger=h.logger,
            watch=True,
        ),
        _deps=h.deps(),
    )
    c.start()
    try:
        assert len(h.server.resource_gets("config")) == 1
        h.clock.advance(120.0)
        assert len(h.server.resource_gets("config")) == 1  # no polling

        # Live edit on cache file triggers debounced reload
        cache_p = _cache_file(h.home)
        h.fs.write_bytes(cache_p, b'{"v":2}')
        h.watcher.emit(str(cache_p), kind="modified")
        h.clock.wait_for_timers(1)
        h.clock.advance(WATCH_DEBOUNCE_SECONDS)
        assert c.get("config") == {"v": 2}
    finally:
        c.close()


def test_dev_cold_cache_server_down_fails_without_fallback():
    h = Harness(env={"SAILOR_FALLBACK_BASE_URL": "https://fallback.test"})
    h.server.set_fallback("app-config.sailor.fall", b'{"fb":true}')
    h.server.set_resource("config", b"", raise_error=httpx.ConnectError("down"))

    c = Consumer(
        InitOption(
            connection=_conn(),
            resources=[defaults.config_dev_default()],
            http_client=h.http_client(),
            logger=h.logger,
            watch=False,
        ),
        _deps=h.deps(),
    )
    with pytest.raises(ResourceNotLoadedError) as exc:
        c.start()
    assert exc.value.code is ErrorCode.RESOURCE_NOT_LOADED
    assert len(h.logger.by_name("resource.load_failed")) == 1
