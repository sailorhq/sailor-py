"""Conformance tests C-2 to C-18: configuration precedence, URI parsing, and security."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sailor import (
    ConnectionOption,
    Consumer,
    ErrorCode,
    InitOption,
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
    defaults,
)
from sailor.connection import normalize_addr, parse_uri, resolve_connection

from .fakes import Harness


def _init(conn: ConnectionOption | None = None, **kwargs: object) -> InitOption:
    return InitOption(
        connection=conn or ConnectionOption(),
        resources=[defaults.config_pull_default()],
        **kwargs,  # type: ignore[arg-type]
    )


def test_c2_env_uri_replaces_connection():
    h = Harness(env={"SAILOR_URI": "sailor://ak:sk@envhost:7766/envns/envapp"})
    conn = resolve_connection(
        _init(ConnectionOption(addr="https://codehost", namespace="codens", app="codeapp")),
        deps=h.deps(),
    )
    assert conn.source == "env_uri"
    assert conn.addr == "http://envhost:7766"
    assert conn.namespace == "envns"
    assert conn.app == "envapp"
    assert conn.access_key == "ak"
    assert conn.secret_key == "sk"


def test_c3_local_config_needs_namespace_and_app():
    h = Harness()
    with pytest.raises(MissingNamespaceAppError) as exc:
        resolve_connection(_init(ConnectionOption(), use_local_config=True), deps=h.deps())
    assert exc.value.code is ErrorCode.MISSING_NAMESPACE_APP


def test_c4_no_connection_fails():
    h = Harness()
    with pytest.raises(NoConnectionError) as exc:
        resolve_connection(_init(), deps=h.deps())
    assert exc.value.code is ErrorCode.NO_CONNECTION


def test_c5_sailor_token_ignored_for_field_connection():
    h = Harness(env={"SAILOR_TOKEN": "env-tok"})
    conn = resolve_connection(
        _init(ConnectionOption(addr="https://host", namespace="ns", app="app", token="field-tok")),
        deps=h.deps(),
    )
    assert conn.token == "field-tok"

    conn_no_tok = resolve_connection(
        _init(ConnectionOption(addr="https://host", namespace="ns", app="app")),
        deps=h.deps(),
    )
    assert conn_no_tok.token == ""


def test_c6_uri_userinfo_percent_decoded_and_host_verbatim():
    parsed = parse_uri("sailor://a%40b:c%3Ad@host.example:7766/ns/app?scheme=https")
    assert parsed.access_key == "a@b"
    assert parsed.secret_key == "c:d"
    assert parsed.addr == "https://host.example:7766"
    assert parsed.namespace == "ns"
    assert parsed.app == "app"


def test_c7_uri_defaults_to_http():
    parsed = parse_uri("sailor://a:b@host/ns/app")
    assert parsed.addr == "http://host"


@pytest.mark.parametrize(
    ("uri", "exc_cls", "code"),
    [
        ("sailor://a:b@host/ns/app?scheme=ftp", InvalidSchemeError, ErrorCode.INVALID_SCHEME),
        ("http://a:b@host/ns/app", InvalidURIPrefixError, ErrorCode.INVALID_URI_PREFIX),
        ("sailor://:b@host/ns/app", MissingAccessKeyError, ErrorCode.MISSING_ACCESS_KEY),
        ("sailor://a@host/ns/app", MissingSecretKeyError, ErrorCode.MISSING_SECRET_KEY),
        ("sailor://a:b@host/ns", MissingPathComponentsError, ErrorCode.MISSING_PATH_COMPONENTS),
        (
            "sailor://a:b@host/ns/app/extra",
            MissingPathComponentsError,
            ErrorCode.MISSING_PATH_COMPONENTS,
        ),
        ("sailor://a:b@host:badport/ns/app", InvalidURIError, ErrorCode.INVALID_URI),
    ],
)
def test_c8_to_c13_uri_errors(uri: str, exc_cls: type[Exception], code: ErrorCode):
    with pytest.raises(exc_cls) as exc:
        parse_uri(uri)
    assert exc.value.code is code  # type: ignore[attr-defined]


def test_c14_to_c16_addr_normalisation():
    assert normalize_addr("host:7766") == "http://host:7766"
    assert normalize_addr("https://host") == "https://host"
    with pytest.raises(NoConnectionError) as exc:
        normalize_addr("")
    assert exc.value.code is ErrorCode.NO_CONNECTION


def test_c17_and_c18_insecure_transport_warning():
    h1 = Harness()
    Consumer(
        InitOption(
            connection=ConnectionOption(
                addr="http://sailor.prod:7766", namespace="ns", app="app", token="tok"
            ),
            resources=[defaults.config_pull_default()],
            logger=h1.logger,
        ),
        _deps=h1.deps(),
    )
    assert len(h1.logger.by_name("config.insecure_transport")) == 1

    for local in ("http://localhost:7766", "http://127.0.0.1:7766", "http://[::1]:7766"):
        h2 = Harness()
        Consumer(
            InitOption(
                connection=ConnectionOption(addr=local, namespace="ns", app="app", token="tok"),
                resources=[defaults.config_pull_default()],
                logger=h2.logger,
            ),
            _deps=h2.deps(),
        )
        assert len(h2.logger.by_name("config.insecure_transport")) == 0


def test_local_config_resolution_and_errors():
    h = Harness(home=Path("/h"))
    with pytest.raises(LocalConfigNotFoundError) as exc:
        resolve_connection(
            _init(ConnectionOption(namespace="ns", app="app"), use_local_config=True),
            deps=h.deps(),
        )
    assert exc.value.code is ErrorCode.LOCAL_CONFIG_NOT_FOUND

    cfg_path = Path("/h/.sailor/config")
    h.fs.write_bytes(cfg_path, b"{not-json")
    with pytest.raises(LocalConfigInvalidError) as exc2:
        resolve_connection(
            _init(ConnectionOption(namespace="ns", app="app"), use_local_config=True),
            deps=h.deps(),
        )
    assert exc2.value.code is ErrorCode.LOCAL_CONFIG_INVALID

    h.fs.write_bytes(
        cfg_path,
        json.dumps(
            {
                "env": "missing-env",
                "manifest": {"envs": [{"name": "sit", "host": "sit.local:7766"}]},
                "tokens": {"sit": "sit-tok"},
            }
        ).encode(),
    )
    with pytest.raises(LocalConfigEnvNotFoundError) as exc3:
        resolve_connection(
            _init(ConnectionOption(namespace="ns", app="app"), use_local_config=True),
            deps=h.deps(),
        )
    assert exc3.value.code is ErrorCode.LOCAL_CONFIG_ENV_NOT_FOUND

    h.fs.write_bytes(
        cfg_path,
        json.dumps(
            {
                "env": "sit",
                "manifest": {"envs": [{"name": "sit", "host": "sit.local:7766"}]},
                "tokens": {"sit": "sit-tok"},
            }
        ).encode(),
    )
    conn = resolve_connection(
        _init(ConnectionOption(namespace="ns", app="app"), use_local_config=True),
        deps=h.deps(),
    )
    assert conn.addr == "http://sit.local:7766"
    assert conn.token == "sit-tok"
    assert conn.env == "sit"

    # Caller's token overrides file token (CONS-LOCAL-4)
    conn_override = resolve_connection(
        _init(
            ConnectionOption(namespace="ns", app="app", token="caller-tok"),
            use_local_config=True,
        ),
        deps=h.deps(),
    )
    assert conn_override.token == "caller-tok"


def test_uri_option_token_precedence():
    h = Harness(env={"SAILOR_TOKEN": "env-tok"})
    conn1 = resolve_connection(
        _init(ConnectionOption(uri="sailor://ak:sk@host/ns/app")), deps=h.deps()
    )
    assert conn1.token == "env-tok"

    conn2 = resolve_connection(
        _init(ConnectionOption(uri="sailor://ak:sk@host/ns/app", token="opt-tok")),
        deps=h.deps(),
    )
    assert conn2.token == "opt-tok"
