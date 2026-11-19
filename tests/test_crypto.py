"""Conformance tests C-25 to C-33: KAT vectors, validation, and encryption mismatch."""

from __future__ import annotations

import base64
import json

import pytest

from sailor import (
    ConnectionOption,
    Consumer,
    DecodeFailedError,
    DecryptFailedError,
    ErrorCode,
    FetchDefinition,
    FetchOption,
    InitOption,
    ResourceDefinition,
    defaults,
)
from sailor.crypto import decrypt_resource, derive_kek

from .fakes import Harness

KAT_SECRET_KEY = "server-secret-key"
KAT_ACCESS_KEY = "server-access-key"
KAT_KEK_HEX = "9dbd6d9747e4182c853388a93ee76ad5decb1c284e4d9200256e0cbfc63a343d"
KAT_PAYLOAD = json.dumps(
    {
        "db_password": {
            "encrypted_secret": "lKaQNKO3FIX6i37ZNf+afEEEua9pVPOFAGKaLijlaW2pQklTFHtw278=",
            "encrypted_dek": (
                "Hf08s2skJt9HwGX2pptDX6GUCh/K97qQUhIgnFBk7FaqsvmWStMrlmclgPbYHphZ7m4AeT7w0jQ1zgQS"
            ),
        },
        "empty": {
            "encrypted_secret": "akkTYxk3ISelMKgWRgElDPkZopM70gE5Bi2hqw==",
            "encrypted_dek": (
                "UV6tqt7KQbHhINb4Kne1N9f9FH+DdFhtcLQQnHjWWDuul8lFr/JUXZFERCayB/X1QPscAcK0Q+rhRa5x"
            ),
        },
    }
).encode("utf-8")


def test_c25_known_answer_decryption():
    plain = decrypt_resource(KAT_PAYLOAD, access_key=KAT_ACCESS_KEY, secret_key=KAT_SECRET_KEY)
    assert json.loads(plain) == {"db_password": "correct-horse", "empty": ""}


def test_c26_kek_known_answer():
    assert derive_kek(KAT_SECRET_KEY, KAT_ACCESS_KEY).hex() == KAT_KEK_HEX


def test_c27_wrong_secret_key_no_partial_map():
    with pytest.raises(DecryptFailedError) as exc:
        decrypt_resource(KAT_PAYLOAD, access_key=KAT_ACCESS_KEY, secret_key="wrong-key")
    assert exc.value.code is ErrorCode.DECRYPT_FAILED


def test_c28_flipped_ciphertext_byte():
    obj = json.loads(KAT_PAYLOAD)
    raw = bytearray(base64.b64decode(obj["db_password"]["encrypted_secret"], validate=True))
    raw[-1] ^= 0x01
    obj["db_password"]["encrypted_secret"] = base64.b64encode(bytes(raw)).decode()
    with pytest.raises(DecryptFailedError) as exc:
        decrypt_resource(
            json.dumps(obj).encode(), access_key=KAT_ACCESS_KEY, secret_key=KAT_SECRET_KEY
        )
    assert exc.value.code is ErrorCode.DECRYPT_FAILED
    assert "db_password" in str(exc.value)


def test_c29_short_11_byte_dek_fails_safely():
    obj = json.loads(KAT_PAYLOAD)
    obj["db_password"]["encrypted_dek"] = base64.b64encode(b"12345678901").decode()
    with pytest.raises(DecryptFailedError) as exc:
        decrypt_resource(
            json.dumps(obj).encode(), access_key=KAT_ACCESS_KEY, secret_key=KAT_SECRET_KEY
        )
    assert exc.value.code is ErrorCode.DECRYPT_FAILED


def test_c30_urlsafe_base64_rejected():
    obj = json.loads(KAT_PAYLOAD)
    # Replace + with - to make URL-safe base64
    obj["db_password"]["encrypted_secret"] = obj["db_password"]["encrypted_secret"].replace(
        "+", "-"
    )
    with pytest.raises(DecryptFailedError) as exc:
        decrypt_resource(
            json.dumps(obj).encode(), access_key=KAT_ACCESS_KEY, secret_key=KAT_SECRET_KEY
        )
    assert exc.value.code is ErrorCode.DECRYPT_FAILED


def test_c31_empty_plaintext_28_bytes():
    obj = {"empty": json.loads(KAT_PAYLOAD)["empty"]}
    plain = decrypt_resource(
        json.dumps(obj).encode(), access_key=KAT_ACCESS_KEY, secret_key=KAT_SECRET_KEY
    )
    assert json.loads(plain) == {"empty": ""}


def test_c32_encrypted_header_mismatch_logged_and_declaration_wins():
    h = Harness()
    h.server.set_resource("secret", KAT_PAYLOAD, encrypted="false")
    c = Consumer(
        InitOption(
            connection=ConnectionOption(
                addr="https://sailor.test",
                namespace="ns",
                app="app",
                access_key=KAT_ACCESS_KEY,
                secret_key=KAT_SECRET_KEY,
            ),
            resources=[defaults.secrets_pull_default()],
            http_client=h.http_client(),
            logger=h.logger,
        ),
        _deps=h.deps(),
    )
    c.start()
    try:
        assert c.get("secret", dict) == {"db_password": "correct-horse", "empty": ""}
        mismatches = h.logger.by_name("resource.encryption_mismatch")
        assert len(mismatches) == 1
        assert mismatches[0].fields["declared"] is True
        assert mismatches[0].fields["server"] == "false"
    finally:
        c.close()


def test_c33_non_ascii_utf8_round_trip():
    from .conftest import encrypted_secrets_payload

    payload = encrypted_secrets_payload(
        {"msg": "héllo wörld 🔐"}, secret_key=KAT_SECRET_KEY, access_key=KAT_ACCESS_KEY
    )
    plain = decrypt_resource(payload, access_key=KAT_ACCESS_KEY, secret_key=KAT_SECRET_KEY)
    assert json.loads(plain) == {"msg": "héllo wörld 🔐"}


def test_malformed_encrypted_json_is_decode_failed():
    with pytest.raises(DecodeFailedError) as exc:
        decrypt_resource(b"not-json", access_key=KAT_ACCESS_KEY, secret_key=KAT_SECRET_KEY)
    assert exc.value.code is ErrorCode.DECODE_FAILED


def test_encrypted_ignores_lang_cons_res_9():
    h = Harness()
    h.server.set_resource("secret", KAT_PAYLOAD)
    from sailor import resource

    c = Consumer(
        InitOption(
            connection=ConnectionOption(
                addr="https://sailor.test",
                namespace="ns",
                app="app",
                access_key=KAT_ACCESS_KEY,
                secret_key=KAT_SECRET_KEY,
            ),
            resources=[
                resource(
                    ResourceDefinition(name="secret", lang="yaml", encrypted=True),
                    FetchDefinition(fetch=FetchOption.PULL, once=True),
                )
            ],
            http_client=h.http_client(),
            logger=h.logger,
        ),
        _deps=h.deps(),
    )
    c.start()
    assert c.get("secret", dict) == {"db_password": "correct-horse", "empty": ""}
    c.close()
