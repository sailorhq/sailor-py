"""Shared test helpers, including a Go-compatible secret encryptor."""

from __future__ import annotations

import base64
import json
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from sailor.crypto import NONCE_SIZE, derive_kek

# Deterministic 12-byte nonce for reproducible fixtures.
_TEST_NONCE = bytes(range(NONCE_SIZE))


def _seal(plaintext: bytes, key: bytes, nonce: bytes = _TEST_NONCE) -> str:
    """base64( nonce || AES-256-GCM(plaintext) ) — matches Go vault Seal format."""
    ct = AESGCM(key).encrypt(nonce, plaintext, None)
    return base64.standard_b64encode(nonce + ct).decode()


def make_secret_record(value: str, dek: bytes, kek: bytes) -> dict[str, str]:
    """Produce a SecretRecord dict the client can decrypt."""
    return {
        "encrypted_secret": _seal(value.encode(), dek),
        "encrypted_dek": _seal(dek, kek),
    }


def encrypted_secrets_payload(values: dict[str, str], *, secret_key: str, access_key: str) -> bytes:
    """Full server-shaped secrets response for a dict of plaintext values."""
    kek = derive_kek(secret_key, access_key)
    dek = os.urandom(32)
    records = {name: make_secret_record(val, dek, kek) for name, val in values.items()}
    return json.dumps(records).encode()
