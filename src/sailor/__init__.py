"""Sailor-Py — Python consumer for the Sailor v1 configuration & secret server."""

from __future__ import annotations

from . import defaults
from .consumer import Consumer
from .decode import DecodeFunc
from .errors import (
    AlreadyStartedError,
    DecodeFailedError,
    DecryptFailedError,
    DuplicateResourceError,
    EmptyResourceNameError,
    EmptyResourcesError,
    ErrorCode,
    FallbackFailedError,
    InitError,
    InvalidFetchModeError,
    InvalidPullIntervalError,
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
    ResourceNotLoadedError,
    SailorError,
    TypeMismatchError,
    UnsupportedLangError,
)
from .logger import Logger, LogLevel, StderrLogger
from .options import (
    ConnectionOption,
    FetchDefinition,
    FetchOption,
    InitOption,
    ResourceDecode,
    ResourceDefinition,
    ResourceOption,
    raw,
    resource,
)

__version__ = "1.0.0"

__all__ = [
    "Consumer",
    "defaults",
    "resource",
    "raw",
    "DecodeFunc",
    "ResourceDecode",
    # options
    "InitOption",
    "ConnectionOption",
    "ResourceOption",
    "ResourceDefinition",
    "FetchDefinition",
    "FetchOption",
    # logging
    "Logger",
    "LogLevel",
    "StderrLogger",
    # errors
    "ErrorCode",
    "SailorError",
    "InitError",
    "EmptyResourcesError",
    "EmptyResourceNameError",
    "NoConnectionError",
    "InvalidURIError",
    "InvalidURIPrefixError",
    "MissingAccessKeyError",
    "MissingSecretKeyError",
    "MissingPathComponentsError",
    "InvalidSchemeError",
    "MissingNamespaceAppError",
    "DuplicateResourceError",
    "InvalidPullIntervalError",
    "InvalidFetchModeError",
    "LocalConfigNotFoundError",
    "LocalConfigInvalidError",
    "LocalConfigEnvNotFoundError",
    "UnsupportedLangError",
    "ResourceNotLoadedError",
    "FallbackFailedError",
    "TypeMismatchError",
    "DecodeFailedError",
    "DecryptFailedError",
    "AlreadyStartedError",
]
