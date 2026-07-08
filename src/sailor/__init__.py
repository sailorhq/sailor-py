"""Sailor-Py — a type-safe Python client for Sailor.

Configuration and secret management for cloud-native applications. See the
project README for usage.
"""

from __future__ import annotations

from . import defaults
from .consumer import Consumer
from .crypto import SecretRecord
from .errors import (
    ConfigsNotLoadedError,
    DecryptionError,
    EmptyResourceListError,
    FetchError,
    FetchFallbackFailedError,
    InitError,
    MiscNotLoadedError,
    NoSailorAccessKeyError,
    NoSailorAppError,
    NoSailorNamespaceError,
    NoSailorSecretKeyError,
    NoSailorURIError,
    NoSailorURLError,
    ResourceNotLoadedError,
    SailorError,
    SecretsNotLoadedError,
)
from .options import (
    ConnectionOption,
    FetchDefinition,
    FetchOption,
    InitOption,
    ResourceDefinition,
    ResourceKind,
    ResourceOption,
)

__version__ = "0.1.0"

__all__ = [
    "Consumer",
    "defaults",
    "SecretRecord",
    # options
    "InitOption",
    "ConnectionOption",
    "ResourceOption",
    "ResourceDefinition",
    "FetchDefinition",
    "ResourceKind",
    "FetchOption",
    # errors
    "SailorError",
    "InitError",
    "EmptyResourceListError",
    "NoSailorURIError",
    "NoSailorURLError",
    "NoSailorNamespaceError",
    "NoSailorAppError",
    "NoSailorAccessKeyError",
    "NoSailorSecretKeyError",
    "ResourceNotLoadedError",
    "ConfigsNotLoadedError",
    "SecretsNotLoadedError",
    "MiscNotLoadedError",
    "FetchError",
    "FetchFallbackFailedError",
    "DecryptionError",
]
