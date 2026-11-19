"""Option validation, defaults helpers, and per-instance decoder tests."""

from __future__ import annotations

import pytest

from sailor import (
    ConnectionOption,
    Consumer,
    DuplicateResourceError,
    EmptyResourceNameError,
    EmptyResourcesError,
    ErrorCode,
    FetchDefinition,
    FetchOption,
    InitOption,
    InvalidFetchModeError,
    InvalidPullIntervalError,
    ResourceDefinition,
    UnsupportedLangError,
    defaults,
    raw,
    resource,
)
from sailor.decode import normalize_lang

from .fakes import Harness


def _conn() -> ConnectionOption:
    return ConnectionOption(addr="https://sailor.test", namespace="ns", app="app")


def test_c1_empty_resources_fails():
    with pytest.raises(EmptyResourcesError) as exc:
        Consumer(InitOption(connection=_conn(), resources=[]))
    assert exc.value.code is ErrorCode.EMPTY_RESOURCES


def test_validation_errors():
    h = Harness()
    # Empty resource name
    with pytest.raises(EmptyResourceNameError) as exc:
        Consumer(
            InitOption(
                connection=_conn(),
                resources=[raw(ResourceDefinition(name=""))],
            ),
            _deps=h.deps(),
        )
    assert exc.value.code is ErrorCode.EMPTY_RESOURCE_NAME

    # Duplicate resource
    with pytest.raises(DuplicateResourceError) as exc2:
        Consumer(
            InitOption(
                connection=_conn(),
                resources=[
                    raw(ResourceDefinition(name="config")),
                    raw(ResourceDefinition(name="config")),
                ],
            ),
            _deps=h.deps(),
        )
    assert exc2.value.code is ErrorCode.DUPLICATE_RESOURCE

    # Negative pull interval
    with pytest.raises(InvalidPullIntervalError) as exc3:
        Consumer(
            InitOption(
                connection=_conn(),
                resources=[
                    raw(
                        ResourceDefinition(name="config"),
                        FetchDefinition(fetch=FetchOption.PULL, pull_interval=-1.0),
                    )
                ],
            ),
            _deps=h.deps(),
        )
    assert exc3.value.code is ErrorCode.INVALID_PULL_INTERVAL

    # Below 1s minimum pull interval
    with pytest.raises(InvalidPullIntervalError):
        Consumer(
            InitOption(
                connection=_conn(),
                resources=[
                    raw(
                        ResourceDefinition(name="config"),
                        FetchDefinition(fetch=FetchOption.PULL, pull_interval=0.5),
                    )
                ],
            ),
            _deps=h.deps(),
        )

    # VOLUME without path
    with pytest.raises(InvalidFetchModeError) as exc4:
        Consumer(
            InitOption(
                connection=_conn(),
                resources=[
                    raw(
                        ResourceDefinition(name="config", path=""),
                        FetchDefinition(fetch=FetchOption.VOLUME),
                    )
                ],
            ),
            _deps=h.deps(),
        )
    assert exc4.value.code is ErrorCode.INVALID_FETCH_MODE


def test_standard_defaults():
    cm = defaults.config_map_default()
    assert cm.definition.name == "config"
    assert cm.definition.path == "/etc/sailor"
    assert cm.definition.encrypted is False
    assert cm.fetch_def.fetch is FetchOption.VOLUME

    sec = defaults.secrets_default()
    assert sec.definition.name == "secret"
    assert sec.definition.path == "/etc/sailor/secret"
    assert sec.definition.encrypted is True
    assert sec.fetch_def.fetch is FetchOption.VOLUME

    cp = defaults.config_pull_default()
    assert cp.definition.name == "config"
    assert cp.fetch_def.fetch is FetchOption.PULL
    assert cp.fetch_def.pull_interval == 10.0

    sp = defaults.secrets_pull_default()
    assert sp.definition.name == "secret"
    assert sp.definition.encrypted is True
    assert sp.fetch_def.fetch is FetchOption.PULL

    cd = defaults.config_dev_default()
    assert cd.definition.name == "config"
    assert cd.fetch_def.fetch is FetchOption.DEV

    sd = defaults.secrets_dev_default()
    assert sd.definition.name == "secret"
    assert sd.definition.encrypted is True
    assert sd.fetch_def.fetch is FetchOption.DEV

    mp = defaults.misc_pull_default("flags", interval=2.5)
    assert mp.definition.name == "flags"
    assert mp.fetch_def.fetch is FetchOption.PULL
    assert mp.fetch_def.pull_interval == 2.5
    assert mp.decode is None

    # CONS-RES-4: only the 7 standard constructors exist
    assert not hasattr(defaults, "misc_once_default")


def test_normalize_lang():
    assert normalize_lang("") == "json"
    assert normalize_lang("  ") == "json"
    assert normalize_lang(" YML ") == "yaml"
    assert normalize_lang("TOML") == "toml"


def test_per_instance_decoders_and_unsupported_lang():
    h = Harness()
    h.server.set_resource("cfg", b"key: value")

    c1 = Consumer(
        InitOption(
            connection=_conn(),
            resources=[
                resource(
                    ResourceDefinition(name="cfg", lang="yml"),
                    FetchDefinition(fetch=FetchOption.PULL, once=True),
                )
            ],
            decoders={"yaml": lambda b: {"parsed": b.decode()}},
            http_client=h.http_client(),
            logger=h.logger,
        ),
        _deps=h.deps(),
    )
    c1.start()
    assert c1.get("cfg") == {"parsed": "key: value"}
    c1.close()

    # c2 has no yaml decoder registered (CONS-RES-7, CONS-RES-8)
    c2 = Consumer(
        InitOption(
            connection=_conn(),
            resources=[
                resource(
                    ResourceDefinition(name="cfg", lang="yml"),
                    FetchDefinition(fetch=FetchOption.PULL, once=True),
                )
            ],
            http_client=h.http_client(),
            logger=h.logger,
        ),
        _deps=h.deps(),
    )
    with pytest.raises(UnsupportedLangError) as exc:
        c2.start()
    assert exc.value.code is ErrorCode.UNSUPPORTED_LANG
