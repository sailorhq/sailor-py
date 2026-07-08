"""Resolve a fully-populated :class:`ConnectionOption`.

Resolution precedence (mirrors ``sailor-go``):

1. Explicit fields already set on the passed ``ConnectionOption``.
2. A ``sailor://`` URI (either the ``uri`` field or ``SAILOR_URL`` env var).
3. Individual environment variables.
4. ``~/.sailor/config`` (only when ``use_sailor_config=True``).

Fields resolved earlier are never overwritten by later sources.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from urllib.parse import unquote, urlparse

from .errors import (
    NoSailorAccessKeyError,
    NoSailorAppError,
    NoSailorNamespaceError,
    NoSailorSecretKeyError,
    NoSailorURLError,
)
from .options import ConnectionOption

ENV_URL = "SAILOR_URL"
ENV_NS = "SAILOR_NS"
ENV_APP = "SAILOR_APP"
ENV_ACCESS_KEY = "SAILOR_ACCESS_KEY"
ENV_SECRET_KEY = "SAILOR_SECRET_KEY"

LOCAL_CONFIG_PATH = Path.home() / ".sailor" / "config"


def _parse_uri(uri: str) -> dict[str, str]:
    """Parse ``sailor://accessKey:secretKey@host[:port]/namespace/app``."""
    parsed = urlparse(uri)
    out: dict[str, str] = {}
    if parsed.hostname:
        scheme = "https"
        netloc = parsed.hostname
        if parsed.port:
            netloc = f"{netloc}:{parsed.port}"
        out["addr"] = f"{scheme}://{netloc}"
    if parsed.username:
        out["access_key"] = unquote(parsed.username)
    if parsed.password:
        out["secret_key"] = unquote(parsed.password)
    parts = [p for p in parsed.path.split("/") if p]
    if len(parts) >= 1:
        out["namespace"] = parts[0]
    if len(parts) >= 2:
        out["app"] = parts[1]
    return out


def _from_local_config() -> dict[str, str]:
    """Load ``~/.sailor/config`` (JSON). Returns an empty dict if absent."""
    if not LOCAL_CONFIG_PATH.exists():
        return {}
    raw = json.loads(LOCAL_CONFIG_PATH.read_text())
    out: dict[str, str] = {}
    if "token" in raw:
        out["token"] = raw["token"]
    if "env" in raw:
        out["env"] = raw["env"]
    # environments[].{url,namespace,app,access_key,secret_key} for the active env
    envs = raw.get("environments") or []
    active = raw.get("env")
    for entry in envs:
        if active is None or entry.get("name") == active:
            for src, dst in (
                ("url", "addr"),
                ("namespace", "namespace"),
                ("app", "app"),
                ("access_key", "access_key"),
                ("secret_key", "secret_key"),
            ):
                if entry.get(src):
                    out.setdefault(dst, entry[src])
            break
    return out


def _fill(conn: ConnectionOption, source: dict[str, str]) -> None:
    """Set any unset connection field from ``source`` (in place)."""
    for field in ("addr", "namespace", "app", "access_key", "secret_key", "token", "env"):
        if getattr(conn, field) is None and source.get(field):
            setattr(conn, field, source[field])


def resolve_connection(
    conn: ConnectionOption, *, use_sailor_config: bool = False
) -> ConnectionOption:
    """Return a validated, fully-populated connection or raise an init error."""
    # 2. URI (explicit field, else env).
    uri = conn.uri or os.environ.get(ENV_URL)
    if uri and uri.startswith("sailor://"):
        _fill(conn, _parse_uri(uri))
    elif uri and conn.addr is None:
        # A plain URL in SAILOR_URL is treated as the address.
        conn.addr = uri

    # 3. Individual env vars.
    _fill(
        conn,
        {
            "namespace": os.environ.get(ENV_NS, ""),
            "app": os.environ.get(ENV_APP, ""),
            "access_key": os.environ.get(ENV_ACCESS_KEY, ""),
            "secret_key": os.environ.get(ENV_SECRET_KEY, ""),
        },
    )

    # 4. Local config file.
    if use_sailor_config:
        _fill(conn, _from_local_config())

    _validate(conn)
    return conn


def _validate(conn: ConnectionOption) -> None:
    if not conn.addr:
        raise NoSailorURLError("Sailor server address is not set")
    if not conn.namespace:
        raise NoSailorNamespaceError("Sailor namespace is not set")
    if not conn.app:
        raise NoSailorAppError("Sailor app is not set")
    if not conn.access_key:
        raise NoSailorAccessKeyError("Sailor access key is not set")
    if not conn.secret_key:
        raise NoSailorSecretKeyError("Sailor secret key is not set")
