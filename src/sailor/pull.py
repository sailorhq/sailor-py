"""PULL fetch engine: initial fetch plus optional background polling.

Each non-``once`` PULL resource gets a daemon thread that re-fetches every
``pull_interval`` seconds. Failures fall back (when enabled) and otherwise are
reported to a logger callback and retried on the next tick — the thread never
dies on a transient error, matching ``sailor-go``'s ``keepPullingResource``.
"""

from __future__ import annotations

import threading
from typing import Callable

from .errors import FetchError, FetchFallbackFailedError
from .fallback import load_fallback
from .options import ConnectionOption, FetchOption, ResourceKind, ResourceOption
from .transport import Transport

# Callback invoked with freshly fetched raw bytes for a resource.
OnRaw = Callable[[ResourceKind, str, bytes], None]
OnError = Callable[[str], None]


class Poller:
    """Owns PULL threads for a set of resources."""

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
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []

    def _fetch_once(self, res: ResourceOption) -> bytes:
        """Fetch a resource, falling back if enabled. Raises on total failure."""
        kind = res.definition.kind
        name = res.definition.name
        try:
            return self._transport.fetch(kind, name)
        except FetchError as exc:
            if res.fallback_enabled:
                return load_fallback(
                    self._conn.app or "",
                    kind,
                    timeout=self._conn.socket_timeout,
                )
            raise FetchFallbackFailedError(str(exc)) from exc

    def start(self, resources: list[ResourceOption]) -> None:
        """Do the initial fetch for every PULL resource, then spawn pollers."""
        pull_resources = [r for r in resources if r.fetch_def.fetch is FetchOption.PULL]
        # Initial synchronous fetch — surfaces auth/connectivity errors eagerly.
        for res in pull_resources:
            data = self._fetch_once(res)
            self._on_raw(res.definition.kind, res.definition.name, data)

        # Background polling for resources that are not one-shot.
        for res in pull_resources:
            if res.fetch_def.once:
                continue
            thread = threading.Thread(
                target=self._poll_loop,
                args=(res,),
                name=f"sailor-pull-{res.definition.kind.value}-{res.definition.name}",
                daemon=True,
            )
            thread.start()
            self._threads.append(thread)

    def _poll_loop(self, res: ResourceOption) -> None:
        interval = res.fetch_def.pull_interval
        while not self._stop.wait(interval):
            try:
                data = self._fetch_once(res)
                self._on_raw(res.definition.kind, res.definition.name, data)
            except Exception as exc:  # keep polling despite transient failures
                self._on_error(f"pull failed for {res.definition.kind.value}: {exc}")

    def stop(self) -> None:
        self._stop.set()
        for thread in self._threads:
            thread.join(timeout=1.0)
        self._threads.clear()
