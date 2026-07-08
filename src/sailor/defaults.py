"""Convenience constructors for common :class:`ResourceOption` shapes.

Mirrors ``sailor-go``'s ``defaults.go`` helpers.
"""

from __future__ import annotations

from .options import (
    DEFAULT_CONFIG_VOLUME_PATH,
    DEFAULT_PULL_INTERVAL_SECONDS,
    DEFAULT_SECRET_VOLUME_PATH,
    FetchDefinition,
    FetchOption,
    ResourceDefinition,
    ResourceKind,
    ResourceOption,
)

# --- VOLUME defaults --------------------------------------------------------


def config_map_default(path: str = DEFAULT_CONFIG_VOLUME_PATH) -> ResourceOption:
    """CONFIGS read from a mounted volume."""
    return ResourceOption(
        definition=ResourceDefinition(kind=ResourceKind.CONFIGS, path=path),
        fetch_def=FetchDefinition(fetch=FetchOption.VOLUME),
    )


def secrets_default(path: str = DEFAULT_SECRET_VOLUME_PATH) -> ResourceOption:
    """SECRETS read from a mounted volume."""
    return ResourceOption(
        definition=ResourceDefinition(kind=ResourceKind.SECRETS, path=path),
        fetch_def=FetchDefinition(fetch=FetchOption.VOLUME),
    )


# --- PULL defaults ----------------------------------------------------------


def config_pull_default(
    interval: float = DEFAULT_PULL_INTERVAL_SECONDS,
) -> ResourceOption:
    """CONFIGS pulled from the Sailor API on an interval."""
    return ResourceOption(
        definition=ResourceDefinition(kind=ResourceKind.CONFIGS),
        fetch_def=FetchDefinition(fetch=FetchOption.PULL, pull_interval=interval),
    )


def secrets_pull_default(
    interval: float = DEFAULT_PULL_INTERVAL_SECONDS,
) -> ResourceOption:
    """SECRETS pulled from the Sailor API on an interval."""
    return ResourceOption(
        definition=ResourceDefinition(kind=ResourceKind.SECRETS),
        fetch_def=FetchDefinition(fetch=FetchOption.PULL, pull_interval=interval),
    )


# --- MISC defaults ----------------------------------------------------------


def misc_once_default(name: str) -> ResourceOption:
    """A misc resource fetched exactly once."""
    return ResourceOption(
        definition=ResourceDefinition(kind=ResourceKind.MISC, name=name),
        fetch_def=FetchDefinition(fetch=FetchOption.PULL, once=True),
    )


def misc_pull_default(name: str, interval: float = DEFAULT_PULL_INTERVAL_SECONDS) -> ResourceOption:
    """A misc resource pulled on an interval."""
    return ResourceOption(
        definition=ResourceDefinition(kind=ResourceKind.MISC, name=name),
        fetch_def=FetchDefinition(fetch=FetchOption.PULL, pull_interval=interval),
    )
