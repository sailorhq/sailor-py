"""VOLUME fetch engine tests: read + hot-reload via the file watcher."""

from __future__ import annotations

import time

from sailor.options import (
    ConnectionOption,
    FetchDefinition,
    FetchOption,
    ResourceDefinition,
    ResourceKind,
    ResourceOption,
)
from sailor.volume import VolumeReader

CONN = ConnectionOption(
    addr="https://h", namespace="ns", app="app", access_key="ak", secret_key="sk"
)


def _volume_resource(path: str) -> ResourceOption:
    return ResourceOption(
        definition=ResourceDefinition(kind=ResourceKind.CONFIGS, path=path),
        fetch_def=FetchDefinition(fetch=FetchOption.VOLUME),
    )


def test_volume_read_once(tmp_path):
    f = tmp_path / "_config"
    f.write_bytes(b'{"database_url":"file"}')

    received: dict[str, bytes] = {}
    reader = VolumeReader(CONN, lambda kind, name, data: received.__setitem__(kind.value, data))
    reader.start([_volume_resource(str(f))], watch=False)
    reader.stop()

    assert received["config"] == b'{"database_url":"file"}'


def test_volume_hot_reload(tmp_path):
    f = tmp_path / "_config"
    f.write_bytes(b"v1")

    updates: list[bytes] = []
    reader = VolumeReader(CONN, lambda kind, name, data: updates.append(data))
    reader.start([_volume_resource(str(f))], watch=True)
    try:
        assert updates == [b"v1"]
        f.write_bytes(b"v2")
        # Wait for the watchdog observer to fire.
        deadline = time.time() + 5
        while time.time() < deadline and b"v2" not in updates:
            time.sleep(0.05)
        assert b"v2" in updates
    finally:
        reader.stop()
