"""VOLUME fetch engine: read mounted files, optionally watching for changes.

Sailor mounts ConfigMaps/Secrets as files (e.g. ``/etc/sailor/_config``). This
engine reads them once and, when watching is enabled, uses ``watchdog`` to
re-read on modification — the equivalent of ``sailor-go``'s fsnotify watcher.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer
from watchdog.observers.api import BaseObserver

from .errors import FetchError
from .fallback import load_fallback
from .options import (
    DEFAULT_CONFIG_VOLUME_PATH,
    DEFAULT_SECRET_VOLUME_PATH,
    ConnectionOption,
    FetchOption,
    ResourceKind,
    ResourceOption,
)

OnRaw = Callable[[ResourceKind, str, bytes], None]
OnError = Callable[[str], None]


def default_path_for(kind: ResourceKind) -> str:
    if kind is ResourceKind.SECRETS:
        return DEFAULT_SECRET_VOLUME_PATH
    return DEFAULT_CONFIG_VOLUME_PATH


class _ReloadHandler(FileSystemEventHandler):
    def __init__(self, path: Path, reload: Callable[[], None]) -> None:
        self._path = path.resolve()
        self._reload = reload

    def on_modified(self, event: FileSystemEvent) -> None:
        if not event.is_directory and Path(str(event.src_path)).resolve() == self._path:
            self._reload()

    # ConfigMap updates land via atomic symlink swap -> often a create/move.
    on_created = on_modified
    on_moved = on_modified


class VolumeReader:
    """Reads VOLUME resources and optionally watches them for changes."""

    def __init__(
        self,
        conn: ConnectionOption,
        on_raw: OnRaw,
        on_error: OnError | None = None,
    ) -> None:
        self._conn = conn
        self._on_raw = on_raw
        self._on_error = on_error or (lambda _msg: None)
        self._observer: BaseObserver | None = None

    def _read(self, res: ResourceOption) -> bytes:
        path = Path(res.definition.path or default_path_for(res.definition.kind))
        try:
            return path.read_bytes()
        except OSError as exc:
            if res.fallback_enabled:
                return load_fallback(
                    self._conn.app or "",
                    res.definition.kind,
                    timeout=self._conn.socket_timeout,
                )
            raise FetchError(f"volume read failed: {path}") from exc

    def start(self, resources: list[ResourceOption], *, watch: bool) -> None:
        volume_resources = [r for r in resources if r.fetch_def.fetch is FetchOption.VOLUME]
        for res in volume_resources:
            self._on_raw(res.definition.kind, res.definition.name, self._read(res))

        if not watch or not volume_resources:
            return

        self._observer = Observer()
        watched_dirs: set[str] = set()
        for res in volume_resources:
            path = Path(res.definition.path or default_path_for(res.definition.kind))

            def reload(r: ResourceOption = res) -> None:
                try:
                    self._on_raw(r.definition.kind, r.definition.name, self._read(r))
                except Exception as exc:
                    self._on_error(f"volume reload failed: {exc}")

            handler = _ReloadHandler(path, reload)
            watch_dir = str(path.parent)
            self._observer.schedule(handler, watch_dir, recursive=False)
            watched_dirs.add(watch_dir)
        self._observer.start()

    def stop(self) -> None:
        if self._observer is not None:
            self._observer.stop()
            self._observer.join(timeout=1.0)
            self._observer = None
