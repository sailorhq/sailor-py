"""Exception hierarchy for the Sailor client.

Go's ``sailor-go`` distinguishes errors with ``errors.Is``; the Pythonic
equivalent is catching specific exception types. All exceptions derive from
:class:`SailorError`.
"""

from __future__ import annotations


class SailorError(Exception):
    """Base class for every error raised by the Sailor client."""


# --- Initialization / configuration errors ---------------------------------


class InitError(SailorError):
    """Raised while constructing a :class:`~sailor.consumer.Consumer`."""


class EmptyResourceListError(InitError):
    """No resources were supplied in ``InitOption.resources``."""


class NoSailorURIError(InitError):
    """A connection could not be resolved from URI, options, env, or config."""


class NoSailorURLError(InitError):
    """The Sailor server address (``addr``/``SAILOR_URL``) is missing."""


class NoSailorNamespaceError(InitError):
    """The namespace (``namespace``/``SAILOR_NS``) is missing."""


class NoSailorAppError(InitError):
    """The application name (``app``/``SAILOR_APP``) is missing."""


class NoSailorAccessKeyError(InitError):
    """The access key (``access_key``/``SAILOR_ACCESS_KEY``) is missing."""


class NoSailorSecretKeyError(InitError):
    """The secret key (``secret_key``/``SAILOR_SECRET_KEY``) is missing."""


# --- Runtime errors ---------------------------------------------------------


class ResourceNotLoadedError(SailorError):
    """A resource was requested before it was successfully loaded."""


class ConfigsNotLoadedError(ResourceNotLoadedError):
    """``get()`` was called but the config resource is not loaded."""


class SecretsNotLoadedError(ResourceNotLoadedError):
    """``get_secret()`` was called but the secret resource is not loaded."""


class MiscNotLoadedError(ResourceNotLoadedError):
    """``get_misc(name)`` was called but that misc resource is not loaded."""


class FetchError(SailorError):
    """A resource could not be fetched from its source."""


class FetchFallbackFailedError(FetchError):
    """The primary fetch failed and no usable fallback was available."""


class DecryptionError(SailorError):
    """A secret record could not be decrypted."""
