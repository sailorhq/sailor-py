"""DEV fetch-mode tests: cache-miss fetch, cache-hit, path derivation, reload."""

from __future__ import annotations

import time

from sailor import defaults
from sailor.dev import DevReader, dev_cache_path
from sailor.options import (
    ConnectionOption,
    FetchOption,
    ResourceKind,
)

CONN = ConnectionOption(
    addr="https://h", namespace="ns", app="app", env="sit", access_key="ak", secret_key="sk"
)


class FakeTransport:
    def __init__(self, payloads):
        self.payloads = payloads
        self.calls = []

    def fetch(self, kind, name=""):
        self.calls.append((kind, name))
        return self.payloads[kind]


def test_dev_defaults():
    c = defaults.config_dev_default()
    assert c.definition.kind is ResourceKind.CONFIGS
    assert c.fetch_def.fetch is FetchOption.DEV
    assert defaults.secrets_dev_default().fetch_def.fetch is FetchOption.DEV


def test_cache_path_derivation():
    p = dev_cache_path(CONN, ResourceKind.CONFIGS)
    assert p.name == "ns-app-sit-config.json"
    assert p.parent.name == "cache"


def test_cache_path_empty_env():
    conn = ConnectionOption(namespace="ns", app="app")
    assert dev_cache_path(conn, ResourceKind.SECRETS).name == "ns-app--secret.json"


def test_dev_cache_miss_fetches_and_writes(tmp_path, monkeypatch):
    monkeypatch.setattr("sailor.dev.CACHE_DIR", tmp_path / "cache")
    transport = FakeTransport({ResourceKind.CONFIGS: b'{"a":1}'})
    received = {}
    reader = DevReader(transport, CONN, lambda k, n, d: received.__setitem__(k, d))
    reader.start([defaults.config_dev_default()], watch=False)
    reader.stop()

    assert received[ResourceKind.CONFIGS] == b'{"a":1}'
    assert transport.calls == [(ResourceKind.CONFIGS, "")]  # fetched on miss
    # And it wrote the cache file for next time.
    assert (tmp_path / "cache" / "ns-app-sit-config.json").read_bytes() == b'{"a":1}'


def test_dev_cache_hit_skips_fetch(tmp_path, monkeypatch):
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / "ns-app-sit-config.json").write_bytes(b'{"cached":true}')
    monkeypatch.setattr("sailor.dev.CACHE_DIR", cache)

    transport = FakeTransport({ResourceKind.CONFIGS: b'{"fresh":true}'})
    received = {}
    reader = DevReader(transport, CONN, lambda k, n, d: received.__setitem__(k, d))
    reader.start([defaults.config_dev_default()], watch=False)
    reader.stop()

    assert received[ResourceKind.CONFIGS] == b'{"cached":true}'
    assert transport.calls == []  # cache hit -> no fetch


def test_dev_live_reload(tmp_path, monkeypatch):
    monkeypatch.setattr("sailor.dev.CACHE_DIR", tmp_path / "cache")
    transport = FakeTransport({ResourceKind.CONFIGS: b'{"v":1}'})
    updates = []
    reader = DevReader(transport, CONN, lambda k, n, d: updates.append(d))
    reader.start([defaults.config_dev_default()], watch=True)
    try:
        assert updates == [b'{"v":1}']
        (tmp_path / "cache" / "ns-app-sit-config.json").write_bytes(b'{"v":2}')
        deadline = time.time() + 5
        while time.time() < deadline and b'{"v":2}' not in updates:
            time.sleep(0.05)
        assert b'{"v":2}' in updates
    finally:
        reader.stop()
