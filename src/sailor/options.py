"""Configuration option models, mirroring ``sailor-go``'s ``pkg/opts``.

Go field names are mapped to snake_case. Enum values match the wire/library
constants used by Sailor exactly.
"""

from __future__ import annotations

from enum import Enum, IntEnum

from pydantic import BaseModel, ConfigDict, Field

# Default mounted volume paths (match sailor-go defaults).
DEFAULT_CONFIG_VOLUME_PATH = "/etc/sailor/_config"
DEFAULT_SECRET_VOLUME_PATH = "/etc/sailor/secret/_secret"

# Default polling interval for PULL resources.
DEFAULT_PULL_INTERVAL_SECONDS = 10.0


class ResourceKind(str, Enum):
    """Kind of resource managed by Sailor (matches Go string constants)."""

    CONFIGS = "config"
    SECRETS = "secret"
    MISC = "misc"


class FetchOption(IntEnum):
    """How a resource is retrieved (matches Go integer constants)."""

    VOLUME = 1
    PULL = 2
    DEV = 3


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ConnectionOption(_Model):
    """How to reach and authenticate against the Sailor server."""

    uri: str | None = None
    addr: str | None = None
    namespace: str | None = None
    app: str | None = None
    access_key: str | None = None
    secret_key: str | None = None
    token: str | None = None
    env: str | None = None
    socket_timeout: float = 30.0


class ResourceDefinition(_Model):
    """Identifies a resource: its kind, optional misc name, and volume path."""

    kind: ResourceKind
    name: str = ""
    path: str = ""


class FetchDefinition(_Model):
    """How and how often a resource is fetched."""

    fetch: FetchOption = FetchOption.PULL
    once: bool = False
    pull_interval: float = DEFAULT_PULL_INTERVAL_SECONDS


class ResourceOption(_Model):
    """A single resource to manage, its fetch strategy, and fallback flag."""

    definition: ResourceDefinition
    fetch_def: FetchDefinition = Field(default_factory=FetchDefinition)
    fallback_enabled: bool = False


class InitOption(_Model):
    """Top-level options passed to :class:`~sailor.consumer.Consumer`."""

    connection: ConnectionOption
    resources: list[ResourceOption]
    logging: bool = False
    watch: bool | None = None
    use_sailor_config: bool = False
