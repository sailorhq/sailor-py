"""The :class:`Consumer` — the public entry point of the Sailor client.

Usage mirrors ``sailor-go``'s generic ``Consumer[C, S]``. Because Python type
parameters are not available at runtime, the concrete Pydantic models are passed
explicitly via ``config_type`` and ``secrets_type``.

    consumer = Consumer[AppConfig, AppSecrets](
        init_option,
        config_type=AppConfig,
        secrets_type=AppSecrets,
    )
    consumer.start()
    cfg = consumer.get()

Current values are held behind a lock and swapped atomically, so ``get`` /
``get_secret`` / ``get_misc`` are safe to call from any thread while background
polling or file-watching updates them.
"""

from __future__ import annotations

import json
import threading
from typing import Generic, TypeVar

from pydantic import BaseModel

from .connection import resolve_connection
from .crypto import decrypt_secrets
from .dev import DevReader
from .errors import (
    ConfigsNotLoadedError,
    EmptyResourceListError,
    MiscNotLoadedError,
    SecretsNotLoadedError,
)
from .options import FetchOption, InitOption, ResourceKind
from .pull import Poller
from .transport import Transport
from .volume import VolumeReader

C = TypeVar("C", bound=BaseModel)
S = TypeVar("S", bound=BaseModel)


class Consumer(Generic[C, S]):
    """Loads and keeps fresh a Sailor app's config, secrets, and misc blobs."""

    def __init__(
        self,
        init: InitOption,
        *,
        config_type: type[C] | None = None,
        secrets_type: type[S] | None = None,
    ) -> None:
        if not init.resources:
            raise EmptyResourceListError("InitOption.resources must not be empty")

        self._init = init
        self._config_type = config_type
        self._secrets_type = secrets_type

        self._conn = resolve_connection(init.connection, use_sailor_config=init.use_sailor_config)

        self._lock = threading.RLock()
        self._config: C | None = None
        self._secrets: S | None = None
        self._misc: dict[str, bytes] = {}

        self._transport: Transport | None = None
        self._poller: Poller | None = None
        self._volume: VolumeReader | None = None
        self._dev: DevReader | None = None
        self._started = False

    # -- lifecycle ----------------------------------------------------------

    def start(self) -> None:
        """Perform initial fetches and launch any background pollers/watchers."""
        if self._started:
            return

        resources = self._init.resources
        needs_pull = any(r.fetch_def.fetch is FetchOption.PULL for r in resources)
        needs_volume = any(r.fetch_def.fetch is FetchOption.VOLUME for r in resources)
        needs_dev = any(r.fetch_def.fetch is FetchOption.DEV for r in resources)
        watch = True if self._init.watch is None else self._init.watch

        # DEV (cache miss) and PULL both fetch over HTTP — share one transport.
        if needs_pull or needs_dev:
            transport = Transport(self._conn)
            self._transport = transport

            if needs_pull:
                self._poller = Poller(transport, self._conn, self._store_raw, self._log)
                self._poller.start(resources)

            if needs_dev:
                self._dev = DevReader(transport, self._conn, self._store_raw, self._log)
                self._dev.start(resources, watch=watch)

        if needs_volume:
            self._volume = VolumeReader(self._conn, self._store_raw, self._log)
            self._volume.start(resources, watch=watch)

        self._started = True

    def close(self) -> None:
        """Stop all background activity and release resources."""
        if self._poller is not None:
            self._poller.stop()
        if self._volume is not None:
            self._volume.stop()
        if self._dev is not None:
            self._dev.stop()
        if self._transport is not None:
            self._transport.close()
        self._started = False

    def __enter__(self) -> Consumer[C, S]:
        self.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- accessors ----------------------------------------------------------

    def get(self) -> C:
        """Return the current config, or raise :class:`ConfigsNotLoadedError`."""
        with self._lock:
            if self._config is None:
                raise ConfigsNotLoadedError("config resource is not loaded")
            return self._config

    def get_secret(self) -> S:
        """Return the current secrets, or raise :class:`SecretsNotLoadedError`."""
        with self._lock:
            if self._secrets is None:
                raise SecretsNotLoadedError("secret resource is not loaded")
            return self._secrets

    def get_misc(self, name: str) -> bytes:
        """Return raw bytes of a misc resource, or raise :class:`MiscNotLoadedError`."""
        with self._lock:
            if name not in self._misc:
                raise MiscNotLoadedError(f"misc resource {name!r} is not loaded")
            return self._misc[name]

    # -- internal store -----------------------------------------------------

    def _store_raw(self, kind: ResourceKind, name: str, data: bytes) -> None:
        """Parse fetched bytes into the typed snapshot and swap atomically."""
        if kind is ResourceKind.CONFIGS:
            parsed = self._parse_model(data, self._config_type)
            with self._lock:
                self._config = parsed
        elif kind is ResourceKind.SECRETS:
            parsed = self._parse_secrets(data)
            with self._lock:
                self._secrets = parsed
        else:  # MISC
            with self._lock:
                self._misc[name] = data

    def _parse_model(self, data: bytes, model: type[BaseModel] | None):  # type: ignore[no-untyped-def]
        if model is None:
            return json.loads(data)
        return model.model_validate_json(data)

    def _parse_secrets(self, data: bytes):  # type: ignore[no-untyped-def]
        encrypted = json.loads(data)
        plaintext = decrypt_secrets(
            encrypted,
            secret_key=self._conn.secret_key or "",
            access_key=self._conn.access_key or "",
        )
        if self._secrets_type is None:
            return plaintext
        return self._secrets_type.model_validate(plaintext)

    def _log(self, message: str) -> None:
        if self._init.logging:
            print(f"[sailor] {message}")  # noqa: T201 — opt-in dev logging
