"""Connection resolution, URI parsing, and local config loading (CONS-CFG, CONS-URI, CONS-LOCAL)."""

from __future__ import annotations

import ipaddress
import json
from dataclasses import dataclass
from urllib.parse import parse_qs, unquote, urlsplit

from .deps import _Deps
from .errors import (
    InvalidSchemeError,
    InvalidURIError,
    InvalidURIPrefixError,
    LocalConfigEnvNotFoundError,
    LocalConfigInvalidError,
    LocalConfigNotFoundError,
    MissingAccessKeyError,
    MissingNamespaceAppError,
    MissingPathComponentsError,
    MissingSecretKeyError,
    NoConnectionError,
)
from .options import InitOption

DEFAULT_SCHEME = "http"
ENV_URI = "SAILOR_URI"
ENV_TOKEN = "SAILOR_TOKEN"


@dataclass(frozen=True)
class ResolvedConnection:
    """Validated, immutable connection resolved during Consumer construction."""

    source: str  # env_uri | local_config | uri_option | fields
    addr: str  # always includes scheme
    namespace: str
    app: str
    access_key: str = ""
    secret_key: str = ""
    token: str = ""
    env: str = ""


def normalize_addr(addr: str) -> str:
    """Normalise an address string per CONS-URI-10..12."""
    if not addr:
        raise NoConnectionError("Sailor server address is empty")
    if "://" in addr:
        return addr
    return f"{DEFAULT_SCHEME}://{addr}"


def is_loopback_addr(addr: str) -> bool:
    """Return True if ``addr`` points to localhost, 127.0.0.0/8, or ::1 (CONS-URI-13)."""
    try:
        parts = urlsplit(addr)
        host = parts.hostname
    except ValueError:
        return False
    if not host:
        return False
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def parse_uri(raw: str) -> ResolvedConnection:
    """Parse ``sailor://<ak>:<sk>@<host>[:port]/<ns>/<app>[?scheme=<http|https>]``."""
    try:
        parts = urlsplit(raw)
        # Access port to trigger ValueError on malformed port specifications.
        _ = parts.port
    except ValueError as exc:
        raise InvalidURIError("URI does not parse", cause=exc) from exc

    if parts.scheme != "sailor":
        raise InvalidURIPrefixError(f"URI scheme must be 'sailor', got {parts.scheme!r}")

    netloc = parts.netloc
    if "@" not in netloc:
        raise MissingAccessKeyError("URI is missing userinfo")
    userinfo, hostport = netloc.rsplit("@", 1)

    if ":" not in userinfo:
        access_key = unquote(userinfo)
        if not access_key:
            raise MissingAccessKeyError("URI access key is empty")
        raise MissingSecretKeyError("URI is missing secret key")

    raw_ak, raw_sk = userinfo.split(":", 1)
    access_key = unquote(raw_ak)
    secret_key = unquote(raw_sk)
    if not access_key:
        raise MissingAccessKeyError("URI access key is empty")
    if not secret_key:
        raise MissingSecretKeyError("URI secret key is empty")

    if not hostport:
        raise NoConnectionError("URI has no host")

    trimmed = parts.path[1:] if parts.path.startswith("/") else parts.path
    segments = trimmed.split("/")
    if len(segments) != 2 or not segments[0] or not segments[1]:
        raise MissingPathComponentsError("URI path must be /<namespace>/<app>")

    scheme = DEFAULT_SCHEME
    if parts.query:
        qs = parse_qs(parts.query, keep_blank_values=True)
        if "scheme" in qs:
            scheme = qs["scheme"][0]
            if scheme not in ("http", "https"):
                raise InvalidSchemeError("URI scheme parameter must be http or https")

    return ResolvedConnection(
        source="",
        addr=f"{scheme}://{hostport}",
        namespace=segments[0],
        app=segments[1],
        access_key=access_key,
        secret_key=secret_key,
    )


def _read_local_config(deps: _Deps) -> tuple[str, str, str]:
    """Load ``~/.sailor/config`` per section 12. Returns ``(addr, token, env)``."""
    try:
        home = deps.home_dir()
        raw_bytes = deps.fs.read_bytes(home / ".sailor" / "config")
    except OSError as exc:
        raise LocalConfigNotFoundError("~/.sailor/config not found", cause=exc) from exc

    try:
        data = json.loads(raw_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LocalConfigInvalidError("~/.sailor/config is not valid JSON", cause=exc) from exc

    if not isinstance(data, dict):
        raise LocalConfigInvalidError("~/.sailor/config must be a JSON object")

    active_env = data.get("env") or ""
    if not isinstance(active_env, str):
        raise LocalConfigInvalidError("~/.sailor/config 'env' must be a string")

    manifest = data.get("manifest")
    envs = manifest.get("envs") if isinstance(manifest, dict) else None
    if not isinstance(envs, list):
        envs = []

    tokens = data.get("tokens")
    token = ""
    if isinstance(tokens, dict):
        tok_val = tokens.get(active_env)
        if isinstance(tok_val, str):
            token = tok_val

    for entry in envs:
        if isinstance(entry, dict) and entry.get("name") == active_env:
            host = entry.get("host") or ""
            if not isinstance(host, str) or not host:
                raise NoConnectionError("local config environment host is empty")
            return normalize_addr(host), token, active_env

    raise LocalConfigEnvNotFoundError(
        f"active environment {active_env!r} is not in local config manifest"
    )


def resolve_connection(init: InitOption, *, deps: _Deps | None = None) -> ResolvedConnection:
    """Resolve connection following the exclusive precedence in CONS-CFG-4..8."""
    d = deps if deps is not None else _Deps()

    env_uri = d.getenv(ENV_URI)
    if env_uri:
        parsed = parse_uri(env_uri)
        return ResolvedConnection(
            source="env_uri",
            addr=parsed.addr,
            namespace=parsed.namespace,
            app=parsed.app,
            access_key=parsed.access_key,
            secret_key=parsed.secret_key,
            token=d.getenv(ENV_TOKEN),
            env=init.connection.env or "",
        )

    conn = init.connection
    if init.use_local_config:
        if not conn.namespace or not conn.app:
            raise MissingNamespaceAppError(
                "namespace and app must be set when use_local_config is True"
            )
        addr, file_token, active_env = _read_local_config(d)
        return ResolvedConnection(
            source="local_config",
            addr=addr,
            namespace=conn.namespace,
            app=conn.app,
            access_key=conn.access_key or "",
            secret_key=conn.secret_key or "",
            token=conn.token or file_token,
            env=conn.env or active_env,
        )

    if conn.uri:
        parsed = parse_uri(conn.uri)
        token = conn.token or d.getenv(ENV_TOKEN)
        return ResolvedConnection(
            source="uri_option",
            addr=parsed.addr,
            namespace=parsed.namespace,
            app=parsed.app,
            access_key=parsed.access_key,
            secret_key=parsed.secret_key,
            token=token,
            env=conn.env or "",
        )

    if conn.addr:
        if not conn.namespace or not conn.app:
            raise MissingNamespaceAppError("namespace and app must be set with addr")
        return ResolvedConnection(
            source="fields",
            addr=normalize_addr(conn.addr),
            namespace=conn.namespace,
            app=conn.app,
            access_key=conn.access_key or "",
            secret_key=conn.secret_key or "",
            token=conn.token or "",
            env=conn.env or "",
        )

    raise NoConnectionError("no Sailor connection source configured")
