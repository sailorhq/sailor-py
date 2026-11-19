"""Exception hierarchy and stable error codes for the Sailor client.

Every error raised by the consumer derives from :class:`SailorError` and carries
a machine-readable :class:`ErrorCode` (``SAILOR_CONSUMER.md`` section 14).
"""

from __future__ import annotations

from enum import Enum


class ErrorCode(str, Enum):
    """Stable error codes defined by the Sailor v1 consumer specification."""

    EMPTY_RESOURCES = "EMPTY_RESOURCES"
    EMPTY_RESOURCE_NAME = "EMPTY_RESOURCE_NAME"
    NO_CONNECTION = "NO_CONNECTION"
    INVALID_URI = "INVALID_URI"
    INVALID_URI_PREFIX = "INVALID_URI_PREFIX"
    MISSING_ACCESS_KEY = "MISSING_ACCESS_KEY"
    MISSING_SECRET_KEY = "MISSING_SECRET_KEY"
    MISSING_PATH_COMPONENTS = "MISSING_PATH_COMPONENTS"
    INVALID_SCHEME = "INVALID_SCHEME"
    MISSING_NAMESPACE_APP = "MISSING_NAMESPACE_APP"
    DUPLICATE_RESOURCE = "DUPLICATE_RESOURCE"
    INVALID_PULL_INTERVAL = "INVALID_PULL_INTERVAL"
    INVALID_FETCH_MODE = "INVALID_FETCH_MODE"
    UNSUPPORTED_LANG = "UNSUPPORTED_LANG"
    LOCAL_CONFIG_NOT_FOUND = "LOCAL_CONFIG_NOT_FOUND"
    LOCAL_CONFIG_INVALID = "LOCAL_CONFIG_INVALID"
    LOCAL_CONFIG_ENV_NOT_FOUND = "LOCAL_CONFIG_ENV_NOT_FOUND"
    RESOURCE_NOT_LOADED = "RESOURCE_NOT_LOADED"
    FALLBACK_FAILED = "FALLBACK_FAILED"
    TYPE_MISMATCH = "TYPE_MISMATCH"
    DECODE_FAILED = "DECODE_FAILED"
    DECRYPT_FAILED = "DECRYPT_FAILED"
    ALREADY_STARTED = "ALREADY_STARTED"


class SailorError(Exception):
    """Base class for every error raised by the Sailor client."""

    code: ErrorCode

    def __init__(
        self,
        code: ErrorCode,
        message: str = "",
        *,
        cause: BaseException | None = None,
    ) -> None:
        self.code = code
        self.cause = cause
        detail = f"sailor: {code.value}: {message}" if message else f"sailor: {code.value}"
        super().__init__(detail)


class _CodedError(SailorError):
    """Helper mixin for concrete exception classes bound to a fixed ErrorCode."""

    default_code: ErrorCode

    def __init__(self, message: str = "", *, cause: BaseException | None = None) -> None:
        super().__init__(self.default_code, message, cause=cause)


# --- Construction / configuration errors ------------------------------------


class InitError(_CodedError):
    """Raised while constructing a :class:`~sailor.consumer.Consumer`."""

    default_code = ErrorCode.NO_CONNECTION


class EmptyResourcesError(InitError):
    default_code = ErrorCode.EMPTY_RESOURCES


class EmptyResourceNameError(InitError):
    default_code = ErrorCode.EMPTY_RESOURCE_NAME


class NoConnectionError(InitError):
    default_code = ErrorCode.NO_CONNECTION


class InvalidURIError(InitError):
    default_code = ErrorCode.INVALID_URI


class InvalidURIPrefixError(InitError):
    default_code = ErrorCode.INVALID_URI_PREFIX


class MissingAccessKeyError(InitError):
    default_code = ErrorCode.MISSING_ACCESS_KEY


class MissingSecretKeyError(InitError):
    default_code = ErrorCode.MISSING_SECRET_KEY


class MissingPathComponentsError(InitError):
    default_code = ErrorCode.MISSING_PATH_COMPONENTS


class InvalidSchemeError(InitError):
    default_code = ErrorCode.INVALID_SCHEME


class MissingNamespaceAppError(InitError):
    default_code = ErrorCode.MISSING_NAMESPACE_APP


class DuplicateResourceError(InitError):
    default_code = ErrorCode.DUPLICATE_RESOURCE


class InvalidPullIntervalError(InitError):
    default_code = ErrorCode.INVALID_PULL_INTERVAL


class InvalidFetchModeError(InitError):
    default_code = ErrorCode.INVALID_FETCH_MODE


class LocalConfigNotFoundError(InitError):
    default_code = ErrorCode.LOCAL_CONFIG_NOT_FOUND


class LocalConfigInvalidError(InitError):
    default_code = ErrorCode.LOCAL_CONFIG_INVALID


class LocalConfigEnvNotFoundError(InitError):
    default_code = ErrorCode.LOCAL_CONFIG_ENV_NOT_FOUND


# --- Runtime / decode / fetch errors ----------------------------------------


class UnsupportedLangError(_CodedError):
    default_code = ErrorCode.UNSUPPORTED_LANG


class ResourceNotLoadedError(_CodedError):
    default_code = ErrorCode.RESOURCE_NOT_LOADED


class FallbackFailedError(_CodedError):
    default_code = ErrorCode.FALLBACK_FAILED


class TypeMismatchError(_CodedError):
    default_code = ErrorCode.TYPE_MISMATCH


class DecodeFailedError(_CodedError):
    default_code = ErrorCode.DECODE_FAILED


class DecryptFailedError(_CodedError):
    default_code = ErrorCode.DECRYPT_FAILED


class AlreadyStartedError(_CodedError):
    default_code = ErrorCode.ALREADY_STARTED
