"""Envelope-decryption for Sailor secrets.

A faithful port of the server's ``pkg/vault/crypto.go``. Compatibility notes:

* KEK is derived with **HKDF-SHA256**: ``ikm = secret_key``, ``salt = access_key``,
  ``info = b""`` (nil in Go), output length 32 bytes (AES-256).
* Every ciphertext is ``base64( nonce[12] || AES-256-GCM ciphertext+tag )``.
* Each :class:`SecretRecord` carries an ``encrypted_dek`` (a DEK sealed with the
  KEK) and an ``encrypted_secret`` (the value sealed with that DEK).

The GCM authentication tag is appended to the ciphertext by Go's ``Seal`` and is
verified implicitly by :meth:`AESGCM.decrypt`.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from .errors import DecryptionError

NONCE_SIZE = 12  # GCM nonce, matches Go's NonceSize
DEK_LENGTH = 32  # AES-256


@dataclass(frozen=True)
class SecretRecord:
    """A single encrypted secret, as returned by the Sailor server."""

    encrypted_secret: str
    encrypted_dek: str

    @classmethod
    def from_dict(cls, data: dict[str, str]) -> SecretRecord:
        return cls(
            encrypted_secret=data["encrypted_secret"],
            encrypted_dek=data["encrypted_dek"],
        )


def derive_kek(secret_key: str, access_key: str) -> bytes:
    """Derive the 32-byte Key-Encryption-Key from the connection credentials.

    Mirrors ``vault.DeriveKEK(secretKey, []byte(accessKey))``.
    """
    hkdf = HKDF(
        algorithm=hashes.SHA256(),
        length=DEK_LENGTH,
        salt=access_key.encode(),
        info=None,
    )
    return hkdf.derive(secret_key.encode())


def _aes_gcm_open(ciphertext_b64: str, key: bytes) -> bytes:
    """base64-decode, split the 12-byte nonce prefix, and AES-256-GCM decrypt."""
    try:
        data = base64.standard_b64decode(ciphertext_b64)
    except (ValueError, TypeError) as exc:
        raise DecryptionError("invalid base64 in ciphertext") from exc
    if len(data) < NONCE_SIZE:
        raise DecryptionError("ciphertext too short")
    nonce, ciphertext = data[:NONCE_SIZE], data[NONCE_SIZE:]
    try:
        return AESGCM(key).decrypt(nonce, ciphertext, None)
    except Exception as exc:  # cryptography raises InvalidTag / ValueError
        raise DecryptionError("AES-GCM decryption failed") from exc


def decrypt_dek(encrypted_dek: str, kek: bytes) -> bytes:
    """Decrypt a DEK using the KEK. Mirrors ``vault.DecryptDEK``."""
    return _aes_gcm_open(encrypted_dek, kek)


def decrypt_with_dek(encrypted_secret: str, dek: bytes) -> str:
    """Decrypt a secret value using its DEK. Mirrors ``vault.DecryptWithDEK``."""
    return _aes_gcm_open(encrypted_secret, dek).decode()


def decrypt_secret_record(record: SecretRecord, kek: bytes) -> str:
    """Full envelope decrypt: KEK -> DEK -> plaintext value."""
    dek = decrypt_dek(record.encrypted_dek, kek)
    return decrypt_with_dek(record.encrypted_secret, dek)


def decrypt_secrets(
    encrypted: dict[str, dict[str, str]],
    secret_key: str,
    access_key: str,
) -> dict[str, str]:
    """Decrypt a ``{name: SecretRecord}`` map into ``{name: plaintext}``.

    This is the shape the Sailor server returns for the ``secret`` resource.
    """
    kek = derive_kek(secret_key, access_key)
    out: dict[str, str] = {}
    for name, raw in encrypted.items():
        out[name] = decrypt_secret_record(SecretRecord.from_dict(raw), kek)
    return out
