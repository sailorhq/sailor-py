"""DEV fetch engine: cache-backed local development with sidecar validation (CONS-DEV-*)."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import httpx

from .connection import ResolvedConnection
from .deps import _Deps
from .errors import ResourceNotLoadedError
from .logger import Logger, LogLevel, emit_event
from .state import ResourceState, StateManager, mode_name
from .transport import Transport


def _hex_sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def dev_cache_path(home_dir: Path, conn: ResolvedConnection, name: str) -> Path:
    """Return ``{home}/.sailor/cache/{ns}-{app}-{env}-{name}.json`` (CONS-DEV-1)."""
    filename = f"{conn.namespace}-{conn.app}-{conn.env}-{name}.json"
    return home_dir / ".sailor" / "cache" / filename


def _meta_path(cache_path: Path) -> Path:
    return Path(f"{cache_path}.meta.json")


class DevReader:
    """Implements the CONS-DEV-4 decision table and live cache reloads."""

    def __init__(
        self,
        transport: Transport,
        conn: ResolvedConnection,
        state_mgr: StateManager,
        deps: _Deps,
        logger: Logger,
    ) -> None:
        self._transport = transport
        self._conn = conn
        self._state_mgr = state_mgr
        self._deps = deps
        self._logger = logger

    def _read_meta(self, cache_path: Path) -> tuple[str, str] | None:
        try:
            raw = self._deps.fs.read_bytes(_meta_path(cache_path))
            data = json.loads(raw.decode("utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return None
        if not isinstance(data, dict):
            return None
        version = data.get("version")
        digest_hex = data.get("hash")
        if not isinstance(version, str) or not isinstance(digest_hex, str):
            return None
        return version, digest_hex

    def _write_cache(self, cache_path: Path, body: bytes, version: str) -> None:
        self._deps.fs.mkdir_p(cache_path.parent, 0o755)
        self._deps.fs.write_bytes(cache_path, body, 0o644)
        meta = json.dumps({"version": version, "hash": _hex_sha256(body)}).encode("utf-8")
        self._deps.fs.write_bytes(_meta_path(cache_path), meta, 0o644)

    def _fail_load(self, state: ResourceState, err: Exception) -> Exception:
        emit_event(
            self._logger,
            LogLevel.ERROR,
            "resource.load_failed",
            resource=state.name,
            mode=mode_name(state.mode),
            cause=str(err),
        )
        return err

    def load_initial(self, state: ResourceState) -> None:
        """Load DEV resource per CONS-DEV-4 decision table."""
        now = self._deps.clock.now()
        try:
            home = self._deps.home_dir()
        except Exception as exc:
            err = ResourceNotLoadedError("finding home directory for DEV cache", cause=exc)
            raise self._fail_load(state, err) from exc

        cache_path = dev_cache_path(home, self._conn, state.name)
        state.cache_path = cache_path
        state.dir = str(cache_path.parent)

        try:
            cached = self._deps.fs.read_bytes(cache_path)
        except OSError:
            self._load_cold(state, cache_path, now)
            return

        meta = self._read_meta(cache_path)
        if meta is None or meta[1] != _hex_sha256(cached):
            emit_event(
                self._logger,
                LogLevel.INFO,
                "resource.local_override",
                resource=state.name,
                path=str(cache_path),
            )
            try:
                self._state_mgr.apply(state, cached, "", "local_override", now)
            except Exception as err:
                raise self._fail_load(state, err) from err
            return

        meta_version, meta_hash = meta
        try:
            res = self._transport.fetch_resource(state.name, if_none_match=meta_version)
        except httpx.HTTPError as exc:
            self._use_cached_stale(state, cached, meta_version, now, f"transport: {exc}")
            return

        self._state_mgr.check_encrypted_header(state, res.encrypted_header)
        if res.status_code == 304:
            try:
                self._state_mgr.apply(state, cached, meta_version, "cache", now)
            except Exception as err:
                raise self._fail_load(state, err) from err
            return

        if res.status_code == 200:
            if _hex_sha256(res.content) == meta_hash:
                try:
                    self._state_mgr.apply(state, cached, res.version or meta_version, "cache", now)
                except Exception as err:
                    raise self._fail_load(state, err) from err
                return
            try:
                self._write_cache(cache_path, res.content, res.version)
                self._state_mgr.apply(state, res.content, res.version, "server", now)
            except Exception as err:
                raise self._fail_load(state, err) from err
            return

        self._use_cached_stale(state, cached, meta_version, now, f"HTTP {res.status_code}")

    def _load_cold(self, state: ResourceState, cache_path: Path, now: float) -> None:
        try:
            res = self._transport.fetch_resource(state.name)
        except httpx.HTTPError as exc:
            err = ResourceNotLoadedError("no cache and server unreachable", cause=exc)
            raise self._fail_load(state, err) from exc

        self._state_mgr.check_encrypted_header(state, res.encrypted_header)
        if res.status_code != 200:
            err = ResourceNotLoadedError(f"no cache and server answered HTTP {res.status_code}")
            raise self._fail_load(state, err)

        try:
            self._write_cache(cache_path, res.content, res.version)
            self._state_mgr.apply(state, res.content, res.version, "server", now)
        except Exception as err:
            raise self._fail_load(state, err) from err

    def _use_cached_stale(
        self,
        state: ResourceState,
        cached: bytes,
        version: str,
        now: float,
        cause: str,
    ) -> None:
        try:
            self._state_mgr.apply(state, cached, version, "cache", now)
        except Exception as err:
            raise self._fail_load(state, err) from err
        self._state_mgr.mark_stale(state, cause, now)

    def reread(self, state: ResourceState) -> None:
        """Reload a DEV cache file after a local edit (CONS-DEV-6)."""
        if state.cache_path is None:
            return
        now = self._deps.clock.now()
        try:
            data = self._deps.fs.read_bytes(state.cache_path)
        except OSError as exc:
            self._state_mgr.mark_stale(state, f"read: {exc}", now)
            return

        meta = self._read_meta(state.cache_path)
        if meta is None or meta[1] != _hex_sha256(data):
            emit_event(
                self._logger,
                LogLevel.INFO,
                "resource.local_override",
                resource=state.name,
                path=str(state.cache_path),
            )

        try:
            self._state_mgr.apply(state, data, "", "cache", now)
        except Exception as err:
            self._state_mgr.note_decode_failure(state, data, err)
            self._state_mgr.mark_stale(state, f"decode: {err}", now)
            return
        self._state_mgr.mark_recovered(state, now)
