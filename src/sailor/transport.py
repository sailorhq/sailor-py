"""HTTP transport for PULL fetches.

Endpoint layout (mirrors ``sailor-go``)::

    GET {addr}/api/v1/resource/{namespace}/{app}/config
    GET {addr}/api/v1/resource/{namespace}/{app}/secret
    GET {addr}/api/v1/resource/{namespace}/{app}/misc/{name}

The optional ``x-token`` header carries ``ConnectionOption.token``.
"""

from __future__ import annotations

import httpx

from .errors import FetchError
from .options import ConnectionOption, ResourceKind


class Transport:
    """Thin wrapper over an ``httpx.Client`` scoped to one connection."""

    def __init__(self, conn: ConnectionOption) -> None:
        self._conn = conn
        headers = {}
        if conn.token:
            headers["x-token"] = conn.token
        self._client = httpx.Client(timeout=conn.socket_timeout, headers=headers)

    def _resource_url(self, kind: ResourceKind, name: str = "") -> str:
        base = (
            f"{(self._conn.addr or '').rstrip('/')}"
            f"/api/v1/resource/{self._conn.namespace}/{self._conn.app}"
        )
        if kind is ResourceKind.MISC:
            return f"{base}/misc/{name}"
        return f"{base}/{kind.value}"

    def fetch(self, kind: ResourceKind, name: str = "") -> bytes:
        """GET a resource and return its raw body, or raise :class:`FetchError`."""
        url = self._resource_url(kind, name)
        try:
            resp = self._client.get(url)
        except httpx.HTTPError as exc:
            raise FetchError(f"request failed: {url}") from exc
        if resp.status_code != 200:
            raise FetchError(f"unexpected status {resp.status_code} from {url}")
        return resp.content

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> Transport:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    @property
    def base_url(self) -> str | None:
        return self._conn.addr
