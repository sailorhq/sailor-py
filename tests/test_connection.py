"""Connection resolution: URI, env vars, precedence, and validation."""

from __future__ import annotations

import pytest

from sailor.connection import _parse_uri, resolve_connection
from sailor.errors import (
    NoSailorAccessKeyError,
    NoSailorAppError,
    NoSailorURLError,
)
from sailor.options import ConnectionOption


def test_parse_full_uri():
    parts = _parse_uri("sailor://ak:sk@sailor.example.com:8443/team-a/billing")
    assert parts["addr"] == "http://sailor.example.com:8443"
    assert parts["access_key"] == "ak"
    assert parts["secret_key"] == "sk"
    assert parts["namespace"] == "team-a"
    assert parts["app"] == "billing"


def test_uri_url_encoded_credentials():
    parts = _parse_uri("sailor://a%40k:s%2Fk@host/ns/app")
    assert parts["access_key"] == "a@k"
    assert parts["secret_key"] == "s/k"


def test_resolve_from_uri_field():
    conn = resolve_connection(ConnectionOption(uri="sailor://ak:sk@host/ns/app"))
    assert conn.addr == "http://host"
    assert conn.namespace == "ns"
    assert conn.app == "app"
    assert conn.access_key == "ak"
    assert conn.secret_key == "sk"


def test_explicit_fields_take_precedence_over_env(monkeypatch):
    monkeypatch.setenv("SAILOR_NS", "env-ns")
    conn = resolve_connection(
        ConnectionOption(
            addr="https://h",
            namespace="explicit-ns",
            app="app",
            access_key="ak",
            secret_key="sk",
        )
    )
    assert conn.namespace == "explicit-ns"


def test_resolve_from_sailor_uri_env(monkeypatch):
    # SAILOR_URI (Go's canonical env var) supplies a full sailor:// URI.
    monkeypatch.delenv("SAILOR_URL", raising=False)
    monkeypatch.setenv("SAILOR_URI", "sailor://ak:sk@host/ns/app")
    conn = resolve_connection(ConnectionOption())
    assert conn.addr == "http://host"
    assert conn.namespace == "ns"
    assert conn.app == "app"
    assert conn.access_key == "ak"
    assert conn.secret_key == "sk"


def test_sailor_uri_takes_precedence_over_url(monkeypatch):
    monkeypatch.setenv("SAILOR_URI", "sailor://ak:sk@uri-host/ns/app")
    monkeypatch.setenv("SAILOR_URL", "https://url-host")
    conn = resolve_connection(ConnectionOption())
    assert conn.addr == "http://uri-host"


def test_local_config_go_shape(tmp_path, monkeypatch):
    import json as _json

    import sailor.connection as connmod

    cfg = tmp_path / "config"
    cfg.write_text(
        _json.dumps(
            {
                "manifest": {"envs": [{"name": "prod", "host": "https://prod-host"}]},
                "env": "prod",
                "token": "tok-123",
                "user": "dev@example.com",
            }
        )
    )
    monkeypatch.setattr(connmod, "LOCAL_CONFIG_PATH", cfg)
    conn = resolve_connection(
        ConnectionOption(namespace="ns", app="app", access_key="ak", secret_key="sk"),
        use_sailor_config=True,
    )
    assert conn.addr == "https://prod-host"
    assert conn.token == "tok-123"
    assert conn.env == "prod"


def test_resolve_from_env(monkeypatch):
    monkeypatch.setenv("SAILOR_URL", "https://h")
    monkeypatch.setenv("SAILOR_NS", "ns")
    monkeypatch.setenv("SAILOR_APP", "app")
    monkeypatch.setenv("SAILOR_ACCESS_KEY", "ak")
    monkeypatch.setenv("SAILOR_SECRET_KEY", "sk")
    conn = resolve_connection(ConnectionOption())
    assert conn.addr == "https://h"
    assert conn.app == "app"
    assert conn.secret_key == "sk"


def test_missing_addr_raises(monkeypatch):
    monkeypatch.delenv("SAILOR_URL", raising=False)
    with pytest.raises(NoSailorURLError):
        resolve_connection(
            ConnectionOption(namespace="ns", app="a", access_key="k", secret_key="s")
        )


def test_missing_app_raises():
    with pytest.raises(NoSailorAppError):
        resolve_connection(
            ConnectionOption(addr="https://h", namespace="ns", access_key="k", secret_key="s")
        )


def test_missing_access_key_raises():
    with pytest.raises(NoSailorAccessKeyError):
        resolve_connection(
            ConnectionOption(addr="https://h", namespace="ns", app="a", secret_key="s")
        )
