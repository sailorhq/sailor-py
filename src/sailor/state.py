"""Shared per-resource state, atomic store path, and transition logging (CONS-STATE-*)."""

from __future__ import annotations

import hashlib
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .connection import ResolvedConnection
from .crypto import decrypt_resource
from .decode import DecoderSet
from .errors import DecodeFailedError, DecryptFailedError, SailorError
from .logger import Logger, LogLevel, emit_event, short_digest
from .options import FetchOption, ResourceDecode

_KEY_RE = re.compile(r"key '([^']+)'")


def mode_name(mode: FetchOption) -> str:
    if mode is FetchOption.VOLUME:
        return "volume"
    if mode is FetchOption.PULL:
        return "pull"
    if mode is FetchOption.DEV:
        return "dev"
    return "unknown"


def digest_of(raw: bytes) -> str:
    """Compute ``sha256:<lowercase hex>`` on bytes as received (CONS-STATE-1)."""
    return f"sha256:{hashlib.sha256(raw).hexdigest()}"


@dataclass
class ResourceState:
    """One managed resource and its mutex-protected runtime state (CONS-CONC-6)."""

    name: str
    mode: FetchOption
    once: bool
    interval: float
    encrypted: bool
    dir: str
    lang: str
    decode: ResourceDecode | None
    cache_path: Path | None = None

    lock: threading.Lock = field(default_factory=threading.Lock)
    loaded: bool = False
    value: Any = None
    version: str = ""
    digest: str = ""
    stale: bool = False
    last_success: float = 0.0
    no_token_logged: bool = False
    last_bad_digest: str = ""
    mismatch_logged: bool = False

    def volume_file(self) -> Path:
        """Return ``{Path}/_{Name}`` (CONS-VOL-1)."""
        return Path(self.dir) / f"_{self.name}"

    def known_version(self) -> str:
        with self.lock:
            return self.version

    def snapshot(self) -> tuple[bool, str, str]:
        with self.lock:
            return self.loaded, self.version, self.digest

    def version_digest(self) -> tuple[str, str]:
        with self.lock:
            return self.version, self.digest


class StateManager:
    """Coordinates single-path state storage and lifecycle logging across fetch modes."""

    def __init__(
        self,
        conn: ResolvedConnection,
        decoders: DecoderSet,
        logger: Logger,
        on_change: Callable[[ResourceState, str, str], None],
    ) -> None:
        self._conn = conn
        self._decoders = decoders
        self._logger = logger
        self._on_change = on_change

    def store_source(
        self,
        state: ResourceState,
        src: bytes,
        version: str,
        now: float,
    ) -> bool:
        """Decrypt, decode, and atomically replace state (CONS-STATE-5)."""
        digest = digest_of(src)
        plain = src
        if state.encrypted:
            plain = decrypt_resource(
                src,
                access_key=self._conn.access_key,
                secret_key=self._conn.secret_key,
            )

        value = self._build_value(state, plain)

        with state.lock:
            changed = (not state.loaded) or (state.digest != digest)
            state.value = value
            state.digest = digest
            if version:
                state.version = version
            state.loaded = True
            state.last_success = now
            return changed

    def _build_value(self, state: ResourceState, plain: bytes) -> Any:
        if state.decode is None:
            return bytes(plain)
        fn = self._decoders.lookup(state.lang)
        try:
            return state.decode(plain, fn)
        except SailorError:
            raise
        except Exception as exc:
            raise DecodeFailedError(f"decoding resource {state.name!r}", cause=exc) from exc

    def apply(
        self,
        state: ResourceState,
        body: bytes,
        version: str,
        source: str,
        now: float,
    ) -> None:
        """Store bytes and emit ``resource.loaded`` or ``resource.changed`` (CONS-STATE-2/6)."""
        old_version, old_digest = state.version_digest()
        changed = self.store_source(state, body, version, now)
        if not changed:
            return

        _, new_version, new_digest = state.snapshot()
        if old_digest == "":
            emit_event(
                self._logger,
                LogLevel.INFO,
                "resource.loaded",
                resource=state.name,
                mode=mode_name(state.mode),
                source=source,
                version=new_version,
                digest=short_digest(new_digest),
            )
        else:
            emit_event(
                self._logger,
                LogLevel.INFO,
                "resource.changed",
                resource=state.name,
                mode=mode_name(state.mode),
                source=source,
                old_version=old_version,
                new_version=new_version,
                old_digest=short_digest(old_digest),
                new_digest=short_digest(new_digest),
            )
        self._on_change(state, new_version, new_digest)

    def mark_stale(self, state: ResourceState, cause: str, now: float) -> None:
        """Log ``resource.stale`` once on transition into failure (CONS-PULL-6, CONS-LOG-5)."""
        with state.lock:
            if not state.loaded or state.stale:
                return
            state.stale = True
            version = state.version
            elapsed = int(max(0.0, now - state.last_success))
        emit_event(
            self._logger,
            LogLevel.WARN,
            "resource.stale",
            resource=state.name,
            mode=mode_name(state.mode),
            cause=cause,
            version=version,
            seconds_since_success=elapsed,
        )

    def mark_recovered(self, state: ResourceState, now: float) -> None:
        """Log ``resource.recovered`` once when refresh succeeds again (CONS-PULL-6)."""
        with state.lock:
            state.last_success = now
            if not state.stale:
                return
            state.stale = False
            version = state.version
        emit_event(
            self._logger,
            LogLevel.INFO,
            "resource.recovered",
            resource=state.name,
            version=version,
        )

    def note_decode_failure(self, state: ResourceState, body: bytes, err: Exception) -> None:
        """Log ``resource.decode_failed`` once per distinct bad payload (section 18.1)."""
        digest = digest_of(body)
        with state.lock:
            if state.last_bad_digest == digest:
                return
            state.last_bad_digest = digest
        fields: dict[str, Any] = {"resource": state.name}
        if isinstance(err, DecryptFailedError):
            match = _KEY_RE.search(str(err))
            if match:
                fields["key"] = match.group(1)
        fields["digest"] = short_digest(digest)
        fields["cause"] = str(err)
        emit_event(self._logger, LogLevel.ERROR, "resource.decode_failed", **fields)

    def check_encrypted_header(self, state: ResourceState, server_header: str) -> None:
        """Warn once if ``x-resource-encrypted`` disagrees with declaration (CONS-CRYPT-9)."""
        if not server_header:
            return
        server_encrypted = server_header.strip().lower() == "true"
        if server_encrypted == state.encrypted:
            return
        with state.lock:
            if state.mismatch_logged:
                return
            state.mismatch_logged = True
        emit_event(
            self._logger,
            LogLevel.WARN,
            "resource.encryption_mismatch",
            resource=state.name,
            declared=state.encrypted,
            server=server_header,
        )
