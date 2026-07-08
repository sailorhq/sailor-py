"""End-to-end Consumer tests over a mocked Sailor HTTP API."""

from __future__ import annotations

import httpx
import pytest
import respx
from pydantic import BaseModel

from sailor import (
    ConfigsNotLoadedError,
    ConnectionOption,
    Consumer,
    EmptyResourceListError,
    FetchDefinition,
    FetchOption,
    InitOption,
    MiscNotLoadedError,
    ResourceDefinition,
    ResourceKind,
    ResourceOption,
)

from .conftest import encrypted_secrets_payload

ADDR = "https://sailor.test"
NS = "team"
APP = "billing"
AK = "access-key"
SK = "secret-key"

BASE = f"{ADDR}/api/v1/resource/{NS}/{APP}"


class AppConfig(BaseModel):
    database_url: str
    port: int = 8080


class AppSecrets(BaseModel):
    api_key: str


def _conn() -> ConnectionOption:
    return ConnectionOption(
        addr=ADDR, namespace=NS, app=APP, access_key=AK, secret_key=SK, token="tok"
    )


def _once(kind: ResourceKind, name: str = "") -> ResourceOption:
    return ResourceOption(
        definition=ResourceDefinition(kind=kind, name=name),
        fetch_def=FetchDefinition(fetch=FetchOption.PULL, once=True),
    )


def test_empty_resource_list_raises():
    with pytest.raises(EmptyResourceListError):
        Consumer(InitOption(connection=_conn(), resources=[]))


@respx.mock
def test_pull_config_secret_misc():
    respx.get(f"{BASE}/config").mock(
        return_value=httpx.Response(200, json={"database_url": "postgres://db", "port": 5432})
    )
    respx.get(f"{BASE}/secret").mock(
        return_value=httpx.Response(
            200,
            content=encrypted_secrets_payload(
                {"api_key": "sk-live-42"}, secret_key=SK, access_key=AK
            ),
        )
    )
    respx.get(f"{BASE}/misc/banner").mock(return_value=httpx.Response(200, content=b"hello world"))

    init = InitOption(
        connection=_conn(),
        resources=[
            _once(ResourceKind.CONFIGS),
            _once(ResourceKind.SECRETS),
            _once(ResourceKind.MISC, "banner"),
        ],
    )
    with Consumer[AppConfig, AppSecrets](
        init, config_type=AppConfig, secrets_type=AppSecrets
    ) as consumer:
        cfg = consumer.get()
        assert isinstance(cfg, AppConfig)
        assert cfg.database_url == "postgres://db"
        assert cfg.port == 5432

        secrets = consumer.get_secret()
        assert secrets.api_key == "sk-live-42"

        assert consumer.get_misc("banner") == b"hello world"


@respx.mock
def test_token_header_sent():
    route = respx.get(f"{BASE}/config").mock(
        return_value=httpx.Response(200, json={"database_url": "x"})
    )
    init = InitOption(connection=_conn(), resources=[_once(ResourceKind.CONFIGS)])
    with Consumer[AppConfig, AppSecrets](init, config_type=AppConfig):
        pass
    assert route.calls.last.request.headers["x-token"] == "tok"


@respx.mock
def test_get_before_load_raises():
    respx.get(f"{BASE}/config").mock(return_value=httpx.Response(200, json={"database_url": "x"}))
    init = InitOption(connection=_conn(), resources=[_once(ResourceKind.CONFIGS)])
    consumer = Consumer[AppConfig, AppSecrets](init, config_type=AppConfig)
    with pytest.raises(ConfigsNotLoadedError):
        consumer.get()  # not started yet


@respx.mock
def test_untyped_config_returns_dict():
    respx.get(f"{BASE}/config").mock(return_value=httpx.Response(200, json={"anything": [1, 2, 3]}))
    init = InitOption(connection=_conn(), resources=[_once(ResourceKind.CONFIGS)])
    with Consumer(init) as consumer:
        assert consumer.get() == {"anything": [1, 2, 3]}


@respx.mock
def test_missing_misc_raises():
    respx.get(f"{BASE}/config").mock(return_value=httpx.Response(200, json={"database_url": "x"}))
    init = InitOption(connection=_conn(), resources=[_once(ResourceKind.CONFIGS)])
    with (
        Consumer[AppConfig, AppSecrets](init, config_type=AppConfig) as consumer,
        pytest.raises(MiscNotLoadedError),
    ):
        consumer.get_misc("nope")
