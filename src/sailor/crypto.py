"""Envelope decryption for Sailor encrypted resources (CONS-CRYPT-*)."""

from __future__ import annotations

import base64
import binascii
import json
from dataclasses import dataclass

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from .errors import DecodeFailedError, DecryptFailedError

NONCE_SIZE = 12
TAG_SIZE = 16
DEK_LENGTH = 32
ENCRYPTED_DEK_SIZE = NONCE_SIZE + DEK_LENGTH + TAG_SIZE  # 60 bytes
MIN_SECRET_SIZE = NONCE_SIZE + TAG_SIZE  # 28 bytes (empty plaintext)


@dataclass(frozen=True)
class SecretRecord:
    """A single encrypted secret record returned by the Sailor server."""

    encrypted_secret: str
    encrypted_dek: str

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> SecretRecord:
        sec = data.get("encrypted_secret")
        dek = data.get("encrypted_dek")
        if not isinstance(sec, str) or not isinstance(dek, str):
            raise DecodeFailedError("secret record must contain string fields")
        return cls(encrypted_secret=sec, encrypted_dek=dek)


def derive_kek(secret_key: str, access_key: str) -> bytes:
    """Derive the 32-byte KEK via HKDF-SHA256 (CONS-CRYPT-3)."""
    hkdf = HKDF(
        algorithm=hashes.SHA256(),
        length=DEK_LENGTH,
        salt=access_key.encode("utf-8"),
        info=None,
    )
    return hkdf.derive(secret_key.encode("utf-8"))


def _b64_decode_strict(encoded: str, *, key: str, field_name: str) -> bytes:
    """Decode strict standard base64 with ``=`` padding (CONS-CRYPT-1)."""
    if not isinstance(encoded, str):
        raise DecodeFailedError(f"key {key!r}: {field_name} must be a string")
    try:
        raw = encoded.encode("ascii")
        return base64.b64decode(raw, validate=True)
    except (UnicodeEncodeError, binascii.Error, ValueError) as exc:
        raise DecryptFailedError(
            f"key {key!r}: {field_name} is not valid standard base64", cause=exc
        ) from exc


def _gcm_open(data: bytes, key_bytes: bytes, *, key: str, what: str) -> bytes:
    if len(data) < MIN_SECRET_SIZE:
        raise DecryptFailedError(
            f"key {key!r}: {what} length {len(data)} is below minimum {MIN_SECRET_SIZE}"
        )
    nonce, ciphertext = data[:NONCE_SIZE], data[NONCE_SIZE:]
    try:
        return AESGCM(key_bytes).decrypt(nonce, ciphertext, None)
    except Exception as exc:
        raise DecryptFailedError(
            f"key {key!r}: AES-GCM decryption failed for {what}", cause=exc
        ) from exc


def decrypt_dek(encrypted_dek: str, kek: bytes, *, key: str = "") -> bytes:
    """Unwrap a 32-byte DEK using the KEK (CONS-CRYPT-6, CONS-CRYPT-8)."""
    data = _b64_decode_strict(encrypted_dek, key=key, field_name="encrypted_dek")
    if len(data) != ENCRYPTED_DEK_SIZE:
        raise DecryptFailedError(
            f"key {key!r}: encrypted_dek is {len(data)} bytes, want {ENCRYPTED_DEK_SIZE}"
        )
    dek = _gcm_open(data, kek, key=key, what="encrypted_dek")
    if len(dek) != DEK_LENGTH:
        raise DecryptFailedError(
            f"key {key!r}: unwrapped DEK is {len(dek)} bytes, want {DEK_LENGTH}"
        )
    return dek


def decrypt_with_dek(encrypted_secret: str, dek: bytes, *, key: str = "") -> str:
    """Decrypt a secret value using its DEK as UTF-8 without trimming (CONS-CRYPT-10)."""
    data = _b64_decode_strict(encrypted_secret, key=key, field_name="encrypted_secret")
    plaintext = _gcm_open(data, dek, key=key, what="encrypted_secret")
    try:
        return plaintext.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise DecryptFailedError(f"key {key!r}: plaintext is not valid UTF-8", cause=exc) from exc


def decrypt_secret_record(record: SecretRecord, kek: bytes, *, key: str = "") -> str:
    """Full envelope decrypt: KEK -> DEK -> UTF-8 plaintext value."""
    dek = decrypt_dek(record.encrypted_dek, kek, key=key)
    return decrypt_with_dek(record.encrypted_secret, dek, key=key)


def decrypt_secrets(
    encrypted: dict[str, dict[str, object]],
    secret_key: str,
    access_key: str,
) -> dict[str, str]:
    """Decrypt a ``{name: SecretRecord}`` map into ``{name: plaintext}``."""
    kek = derive_kek(secret_key, access_key)
    out: dict[str, str] = {}
    for key in sorted(encrypted):
        raw_rec = encrypted[key]
        if not isinstance(raw_rec, dict):
            raise DecodeFailedError(f"key {key!r}: encrypted record must be an object")
        record = SecretRecord.from_dict(raw_rec)
        out[key] = decrypt_secret_record(record, kek, key=key)
    return out


def decrypt_resource(body: bytes, *, access_key: str, secret_key: str) -> bytes:
    """Decrypt a raw encrypted resource JSON payload into serialized flat JSON bytes."""
    try:
        parsed = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DecodeFailedError("encrypted resource is not valid JSON", cause=exc) from exc
    if not isinstance(parsed, dict):
        raise DecodeFailedError("encrypted resource must be a JSON object")

    out = decrypt_secrets(parsed, secret_key=secret_key, access_key=access_key)
    return json.dumps(out).encode("utf-8")
