"""Standard resource constructors (SAILOR_CONSUMER.md section 4.1)."""

from __future__ import annotations

from pydantic import BaseModel

from .options import (
    DEFAULT_PULL_INTERVAL_SECONDS,
    FetchDefinition,
    FetchOption,
    ResourceDefinition,
    ResourceOption,
    raw,
    resource,
)

CONFIG_MOUNT_PATH = "/etc/sailor"
SECRET_MOUNT_PATH = "/etc/sailor/secret"


def config_map_default(
    model: type[BaseModel] | None = None,
    *,
    path: str = CONFIG_MOUNT_PATH,
) -> ResourceOption:
    """Resource ``config`` read from a mounted VOLUME directory."""
    return resource(
        ResourceDefinition(name="config", path=path),
        FetchDefinition(fetch=FetchOption.VOLUME),
        model=model,
    )


def secrets_default(
    model: type[BaseModel] | None = None,
    *,
    path: str = SECRET_MOUNT_PATH,
) -> ResourceOption:
    """Encrypted resource ``secret`` read from a mounted VOLUME directory."""
    return resource(
        ResourceDefinition(name="secret", encrypted=True, path=path),
        FetchDefinition(fetch=FetchOption.VOLUME),
        model=model,
    )


def config_pull_default(
    model: type[BaseModel] | None = None,
    *,
    interval: float = DEFAULT_PULL_INTERVAL_SECONDS,
) -> ResourceOption:
    """Resource ``config`` polled from the Sailor API on an interval."""
    return resource(
        ResourceDefinition(name="config"),
        FetchDefinition(fetch=FetchOption.PULL, pull_interval=interval),
        model=model,
    )


def secrets_pull_default(
    model: type[BaseModel] | None = None,
    *,
    interval: float = DEFAULT_PULL_INTERVAL_SECONDS,
) -> ResourceOption:
    """Encrypted resource ``secret`` polled from the Sailor API on an interval."""
    return resource(
        ResourceDefinition(name="secret", encrypted=True),
        FetchDefinition(fetch=FetchOption.PULL, pull_interval=interval),
        model=model,
    )


def config_dev_default(model: type[BaseModel] | None = None) -> ResourceOption:
    """Resource ``config`` in DEV mode (cached under ~/.sailor/cache)."""
    return resource(
        ResourceDefinition(name="config"),
        FetchDefinition(fetch=FetchOption.DEV),
        model=model,
    )


def secrets_dev_default(model: type[BaseModel] | None = None) -> ResourceOption:
    """Encrypted resource ``secret`` in DEV mode (cached under ~/.sailor/cache)."""
    return resource(
        ResourceDefinition(name="secret", encrypted=True),
        FetchDefinition(fetch=FetchOption.DEV),
        model=model,
    )


def misc_pull_default(
    name: str,
    *,
    interval: float = DEFAULT_PULL_INTERVAL_SECONDS,
) -> ResourceOption:
    """A named raw resource polled on an interval (CONS-RES-4)."""
    return raw(
        ResourceDefinition(name=name),
        FetchDefinition(fetch=FetchOption.PULL, pull_interval=interval),
    )
