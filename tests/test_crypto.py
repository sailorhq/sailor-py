"""Crypto round-trip tests against the Go vault format."""

from __future__ import annotations

import os

import pytest

from sailor.crypto import (
    decrypt_dek,
    decrypt_secret_record,
    decrypt_secrets,
    decrypt_with_dek,
    derive_kek,
)
from sailor.errors import DecryptionError

from .conftest import encrypted_secrets_payload, make_secret_record

SECRET_KEY = "super-secret-key"
ACCESS_KEY = "access-key-123"


def test_derive_kek_is_deterministic_and_32_bytes():
    a = derive_kek(SECRET_KEY, ACCESS_KEY)
    b = derive_kek(SECRET_KEY, ACCESS_KEY)
    assert a == b
    assert len(a) == 32


def test_derive_kek_varies_with_inputs():
    assert derive_kek(SECRET_KEY, ACCESS_KEY) != derive_kek("other", ACCESS_KEY)
    assert derive_kek(SECRET_KEY, ACCESS_KEY) != derive_kek(SECRET_KEY, "other-salt")


def test_dek_envelope_round_trip():
    kek = derive_kek(SECRET_KEY, ACCESS_KEY)
    dek = os.urandom(32)
    record = make_secret_record("hunter2", dek, kek)
    recovered_dek = decrypt_dek(record["encrypted_dek"], kek)
    assert recovered_dek == dek
    assert decrypt_with_dek(record["encrypted_secret"], recovered_dek) == "hunter2"


def test_decrypt_secret_record():
    kek = derive_kek(SECRET_KEY, ACCESS_KEY)
    dek = os.urandom(32)
    record = make_secret_record("pa$$w0rd", dek, kek)
    from sailor.crypto import SecretRecord

    assert decrypt_secret_record(SecretRecord.from_dict(record), kek) == "pa$$w0rd"


def test_decrypt_secrets_map():
    payload = encrypted_secrets_payload(
        {"api_key": "abc", "db_pass": "xyz"},
        secret_key=SECRET_KEY,
        access_key=ACCESS_KEY,
    )
    import json

    out = decrypt_secrets(json.loads(payload), secret_key=SECRET_KEY, access_key=ACCESS_KEY)
    assert out == {"api_key": "abc", "db_pass": "xyz"}


def test_wrong_key_raises_decryption_error():
    kek = derive_kek(SECRET_KEY, ACCESS_KEY)
    dek = os.urandom(32)
    record = make_secret_record("secret", dek, kek)
    wrong_kek = derive_kek("wrong", ACCESS_KEY)
    with pytest.raises(DecryptionError):
        decrypt_dek(record["encrypted_dek"], wrong_kek)


def test_malformed_base64_raises():
    with pytest.raises(DecryptionError):
        decrypt_dek("not!base64!!", b"0" * 32)


def test_short_ciphertext_raises():
    import base64

    tiny = base64.standard_b64encode(b"abc").decode()
    with pytest.raises(DecryptionError):
        decrypt_dek(tiny, b"0" * 32)
