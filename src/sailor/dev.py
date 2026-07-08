"""DEV fetch engine: cache-backed local development with live reload.

Mirrors ``sailor-go``'s ``opts.DEV`` mode. For each DEV resource:

1. Derive a cache path ``~/.sailor/cache/{ns}-{app}-{env}-{kind}.json``.
2. If the cache file exists, load it (a "cache hit"); otherwise fetch once from
   the Sailor API and write the response to the cache.
3. Store it in memory.
4. Watch the cache file so a developer editing it locally gets live reload —
   the whole point of DEV mode.

This lets you pull real config once, then iterate on a local JSON copy without a
server round-trip.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from watchdog.observers import Observer
from watchdog.observers.api import BaseObserver

from .options import ConnectionOption, FetchOption, ResourceKind, ResourceOption
from .transport import Transport
from .volume import _ReloadHandler

OnRaw = Callable[[ResourceKind, str, bytes], None]
OnError = Callable[[str], None]

# Default cache directory, matching sailor-go's ~/.sailor/cache.
CACHE_DIR = Path.home() / ".sailor" / "cache"


def dev_cache_path(conn: ConnectionOption, kind: ResourceKind, name: str = "") -> Path:
    """Cache file path for a DEV resource: {ns}-{app}-{env}-{kind}[-{name}].json."""
    env = conn.env or ""
    stem = f"{conn.namespace}-{conn.app}-{env}-{kind.value}"
    if kind is ResourceKind.MISC and name:
        stem = f"{stem}-{name}"
    return CACHE_DIR / f"{stem}.json"


class DevReader:
    """Loads DEV resources from a local cache (fetching once on a miss) and
    watches the cache files for live-reload edits."""

    def __init__(
        self,
        transport: Transport,
        conn: ConnectionOption,
        on_raw: OnRaw,
        on_error: OnError | None = None,
    ) -> None:
        self._transport = transport
        self._conn = conn
        self._on_raw = on_raw
        self._on_error = on_error or (lambda _msg: None)
        self._observer: BaseObserver | None = None

    def _load_or_fetch(self, res: ResourceOption) -> bytes:
        kind, name = res.definition.kind, res.definition.name
        path = dev_cache_path(self._conn, kind, name)
        if path.exists():
            self._on_error(f"dev cache hit: {path}")  # informational (dev logging)
            return path.read_bytes()
        data = self._transport.fetch(kind, name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return data

    def start(self, resources: list[ResourceOption], *, watch: bool) -> None:
        dev_resources = [r for r in resources if r.fetch_def.fetch is FetchOption.DEV]
        for res in dev_resources:
            self._on_raw(res.definition.kind, res.definition.name, self._load_or_fetch(res))

        if not watch or not dev_resources:
            return

        self._observer = Observer()
        for res in dev_resources:
            path = dev_cache_path(self._conn, res.definition.kind, res.definition.name)

            def reload(r: ResourceOption = res, p: Path = path) -> None:
                try:
                    self._on_raw(r.definition.kind, r.definition.name, p.read_bytes())
                except Exception as exc:
                    self._on_error(f"dev reload failed: {exc}")

            self._observer.schedule(_ReloadHandler(path, reload), str(path.parent), recursive=False)
        self._observer.start()

    def stop(self) -> None:
        if self._observer is not None:
            self._observer.stop()
            self._observer.join(timeout=1.0)
            self._observer = None
