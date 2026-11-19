"""Conformance tests C-35, C-42, C-43: fallback HTTP resolution."""

from __future__ import annotations

import pytest

from sailor import (
    ConnectionOption,
    Consumer,
    ErrorCode,
    FallbackFailedError,
    FetchDefinition,
    FetchOption,
    InitOption,
    ResourceDefinition,
    ResourceNotLoadedError,
    defaults,
)
from sailor.fallback import ENV_FALLBACK_BASE_URL, load_fallback

from .fakes import Harness


def test_c35_volume_missing_uses_fallback():
    h = Harness(env={ENV_FALLBACK_BASE_URL: "https://fallback.test/snapshots/"})
    h.server.set_fallback("billing-config.sailor.fall", b'{"port":9090}')

    c = Consumer(
        InitOption(
            connection=ConnectionOption(addr="https://sailor.test", namespace="ns", app="billing"),
            resources=[defaults.config_map_default(path="/etc/sailor")],
            http_client=h.http_client(),
            logger=h.logger,
            watch=False,
        ),
        _deps=h.deps(),
    )
    c.start()
    try:
        assert c.get("config") == {"port": 9090}
        fb_events = h.logger.by_name("resource.fallback_used")
        assert len(fb_events) == 1
        assert fb_events[0].fields["url"] == "https://fallback.test/snapshots/billing-config.sailor.fall"
        # CONS-FB-2: x-token must not be sent on fallback requests
        fb_reqs = [r for r in h.server.requests if "fallback.test" in r.url]
        assert len(fb_reqs) == 1
        assert "x-token" not in fb_reqs[0].headers
    finally:
        c.close()


def test_c42_fallback_404_fails_and_body_not_stored():
    h = Harness(env={ENV_FALLBACK_BASE_URL: "https://fallback.test"})
    h.server.set_fallback("billing-config.sailor.fall", b'{"evil":true}', status_code=404)

    with pytest.raises(FallbackFailedError) as exc:
        load_fallback(
            "billing",
            "config",
            getenv=h.deps().getenv,
            http_client=h.http_client(),
        )
    assert exc.value.code is ErrorCode.FALLBACK_FAILED

    c = Consumer(
        InitOption(
            connection=ConnectionOption(addr="https://sailor.test", namespace="ns", app="billing"),
            resources=[
                defaults.resource(
                    ResourceDefinition(name="config"),
                    FetchDefinition(fetch=FetchOption.PULL, once=True),
                )
            ],
            http_client=h.http_client(),
            logger=h.logger,
        ),
        _deps=h.deps(),
    )
    with pytest.raises(ResourceNotLoadedError) as start_exc:
        c.start()
    assert start_exc.value.code is ErrorCode.RESOURCE_NOT_LOADED
    with pytest.raises(ResourceNotLoadedError):
        c.get("config")


def test_c43_fallback_base_unset_fails_immediately():
    h = Harness()
    with pytest.raises(FallbackFailedError) as exc:
        load_fallback("billing", "config", getenv=h.deps().getenv, http_client=h.http_client())
    assert exc.value.code is ErrorCode.FALLBACK_FAILED
    assert h.server.requests == []
