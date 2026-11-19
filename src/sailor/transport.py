"""HTTP transport for Sailor v1 resource fetches and consumer reports."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import quote

import httpx

from .connection import ResolvedConnection


@dataclass(frozen=True)
class FetchResult:
    """HTTP response fields relevant to the consumer."""

    status_code: int
    content: bytes
    version: str
    encrypted_header: str


def version_from_headers(headers: httpx.Headers) -> str:
    """Extract version from ``ETag`` verbatim, else ``x-resource-version`` (CONS-STATE-4)."""
    etag = headers.get("etag")
    if isinstance(etag, str) and etag:
        return etag
    ver = headers.get("x-resource-version", "")
    return ver if isinstance(ver, str) else str(ver)


class Transport:
    """Thin wrapper over an ``httpx.Client`` scoped to one connection."""

    def __init__(
        self,
        conn: ResolvedConnection,
        *,
        http_client: httpx.Client | None = None,
        request_timeout: float = 10.0,
        report_timeout: float = 5.0,
    ) -> None:
        self._conn = conn
        self._owns_client = http_client is None
        self._client = http_client if http_client is not None else httpx.Client()
        self._request_timeout = request_timeout
        self._report_timeout = report_timeout

    @property
    def client(self) -> httpx.Client:
        return self._client

    def _resource_url(self, name: str) -> str:
        base = self._conn.addr.rstrip("/")
        ns = quote(self._conn.namespace, safe="")
        app = quote(self._conn.app, safe="")
        res = quote(name, safe="")
        return f"{base}/api/v1/resource/{ns}/{app}/{res}"

    def fetch_resource(self, name: str, *, if_none_match: str = "") -> FetchResult:
        """GET ``/api/v1/resource/{ns}/{app}/{name}`` with explicit timeout (CONS-CFG-2)."""
        headers: dict[str, str] = {}
        if self._conn.token:
            headers["x-token"] = self._conn.token
        if if_none_match:
            headers["If-None-Match"] = if_none_match

        resp = self._client.get(
            self._resource_url(name),
            headers=headers,
            timeout=self._request_timeout,
        )
        return FetchResult(
            status_code=resp.status_code,
            content=resp.content,
            version=version_from_headers(resp.headers),
            encrypted_header=resp.headers.get("x-resource-encrypted", ""),
        )

    def post_report(self, name: str, body: bytes) -> httpx.Response:
        """POST a consumer report bounded by ``report_timeout`` (CONS-HTTP-15..17)."""
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self._conn.token:
            headers["x-token"] = self._conn.token
        url = f"{self._resource_url(name)}/consumer-report"
        return self._client.post(
            url,
            content=body,
            headers=headers,
            timeout=self._report_timeout,
        )

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> Transport:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
