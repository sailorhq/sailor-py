"""Configuration option models for the Sailor v1 consumer."""

from __future__ import annotations

# ruff: noqa: UP045, UP007
from enum import IntEnum
from typing import Any, Callable, Optional

from pydantic import BaseModel, ConfigDict, Field

from .decode import DecodeFunc

DEFAULT_PULL_INTERVAL_SECONDS = 10.0
MIN_PULL_INTERVAL_SECONDS = 1.0
DEFAULT_REQUEST_TIMEOUT_SECONDS = 10.0
DEFAULT_REPORT_TIMEOUT_SECONDS = 5.0

ResourceDecode = Callable[[bytes, DecodeFunc], Any]


class FetchOption(IntEnum):
    """How a resource is retrieved (SAILOR_CONSUMER.md section 7)."""

    VOLUME = 1
    PULL = 2
    DEV = 3


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)


class ConnectionOption(_Model):
    """How to reach and authenticate against the Sailor server."""

    uri: Optional[str] = None
    addr: Optional[str] = None
    namespace: Optional[str] = None
    app: Optional[str] = None
    access_key: Optional[str] = None
    secret_key: Optional[str] = None
    token: Optional[str] = None
    env: Optional[str] = None


class ResourceDefinition(_Model):
    """Identifies a resource and its format/storage attributes."""

    name: str
    lang: str = "json"
    encrypted: bool = False
    path: str = ""


class FetchDefinition(_Model):
    """How and how often a resource is fetched."""

    fetch: FetchOption = FetchOption.PULL
    once: bool = False
    pull_interval: float = DEFAULT_PULL_INTERVAL_SECONDS


class ResourceOption(_Model):
    """A single resource to manage and its optional decode hook."""

    definition: ResourceDefinition
    fetch_def: FetchDefinition = Field(default_factory=FetchDefinition)
    decode: Optional[ResourceDecode] = None


class InitOption(_Model):
    """Top-level options passed to :class:`~sailor.consumer.Consumer`."""

    connection: ConnectionOption = Field(default_factory=ConnectionOption)
    resources: list[ResourceOption]
    use_local_config: bool = False
    watch: Optional[bool] = None
    request_timeout: float = DEFAULT_REQUEST_TIMEOUT_SECONDS
    report_timeout: float = DEFAULT_REPORT_TIMEOUT_SECONDS
    logger: Optional[Any] = None
    http_client: Optional[Any] = None
    decoders: Optional[dict[str, DecodeFunc]] = None


def resource(
    definition: ResourceDefinition,
    fetch_def: FetchDefinition | None = None,
    *,
    model: type[BaseModel] | None = None,
    decode: ResourceDecode | None = None,
) -> ResourceOption:
    """Declare a decoded resource, optionally validating into a Pydantic ``model``."""
    fd = fetch_def if fetch_def is not None else FetchDefinition()
    if decode is not None:
        decode_fn = decode
    elif model is not None:

        def _decode_model(raw_bytes: bytes, lang_decode: DecodeFunc) -> Any:
            return model.model_validate(lang_decode(raw_bytes))

        decode_fn = _decode_model
    else:

        def _decode_plain(raw_bytes: bytes, lang_decode: DecodeFunc) -> Any:
            return lang_decode(raw_bytes)

        decode_fn = _decode_plain

    return ResourceOption(definition=definition, fetch_def=fd, decode=decode_fn)


def raw(
    definition: ResourceDefinition,
    fetch_def: FetchDefinition | None = None,
) -> ResourceOption:
    """Declare a raw resource stored as ``bytes`` with no decoding (CONS-CONC-11)."""
    fd = fetch_def if fetch_def is not None else FetchDefinition()
    return ResourceOption(definition=definition, fetch_def=fd, decode=None)
