"""Consumer reporting and 10-minute heartbeat loop (CONS-RPT-*, CONS-HTTP-11..17)."""

from __future__ import annotations

import json
import threading
from typing import Any, Callable

import httpx

from .connection import ResolvedConnection
from .deps import _Deps
from .logger import Logger, LogLevel, emit_event, truncate
from .state import ResourceState
from .transport import Transport

HEARTBEAT_INTERVAL_SECONDS = 600.0  # 10 minutes (CONS-RPT-2, CONS-RPT-3)


class Reporter:
    """Sends change and heartbeat reports asynchronously without blocking callers."""

    def __init__(
        self,
        conn: ResolvedConnection,
        transport: Transport | None,
        deps: _Deps,
        logger: Logger,
        stop_event: threading.Event,
        spawn: Callable[[Callable[[], None], str], None],
    ) -> None:
        self._conn = conn
        self._transport = transport
        self._deps = deps
        self._logger = logger
        self._stop = stop_event
        self._spawn = spawn

    def log_no_token(self, state: ResourceState) -> None:
        """Emit ``report.skipped_no_token`` once per resource (CONS-RPT-8, CONS-LOG-14)."""
        with state.lock:
            if state.no_token_logged:
                return
            state.no_token_logged = True
        emit_event(self._logger, LogLevel.WARN, "report.skipped_no_token", resource=state.name)

    def report_async(self, state: ResourceState, version: str, digest: str) -> None:
        """Dispatch a consumer report in a tracked background thread (CONS-RPT-4)."""
        if not self._conn.token or self._transport is None:
            return
        self._spawn(
            lambda: self._post_report(state, version, digest),
            f"sailor-report-{state.name}",
        )

    def _identity(self) -> str:
        try:
            host = self._deps.hostname()
        except Exception:
            return "unknown"
        return host or "unknown"

    def _details(self) -> dict[str, str]:
        mapping = {
            "pod_name": "POD_NAME",
            "pod_ip": "POD_IP",
            "node_name": "NODE_NAME",
        }
        out: dict[str, str] = {}
        for key, env_name in mapping.items():
            val = self._deps.getenv(env_name)
            if val:
                out[key] = val
        return out

    def _build_payload(self, version: str, digest: str) -> bytes:
        payload: dict[str, Any] = {
            "consumer": self._identity(),
            "digest": digest,
        }
        if version:
            payload["version"] = version
        details = self._details()
        if details:
            payload["details"] = details
        return json.dumps(payload).encode("utf-8")

    def _post_report(self, state: ResourceState, version: str, digest: str) -> None:
        if self._transport is None:
            return
        body = self._build_payload(version, digest)
        try:
            resp = self._transport.post_report(state.name, body)
        except httpx.HTTPError as exc:
            if not self._stop.is_set():
                emit_event(
                    self._logger,
                    LogLevel.WARN,
                    "report.failed",
                    resource=state.name,
                    status=0,
                    message=truncate(str(exc), 4096),
                )
            return

        if 200 <= resp.status_code < 300:
            emit_event(
                self._logger,
                LogLevel.DEBUG,
                "report.acknowledged",
                resource=state.name,
                status=resp.status_code,
            )
            return

        emit_event(
            self._logger,
            LogLevel.WARN,
            "report.failed",
            resource=state.name,
            status=resp.status_code,
            message=truncate(resp.text, 4096),
        )

    def heartbeat_loop(self, state: ResourceState) -> None:
        """Re-report current state every 10 minutes (CONS-RPT-2)."""
        while not self._deps.clock.wait(HEARTBEAT_INTERVAL_SECONDS, self._stop):
            loaded, version, digest = state.snapshot()
            if loaded:
                self.report_async(state, version, digest)
