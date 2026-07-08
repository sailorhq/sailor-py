"""Fallback resolution tests (local dir + missing config)."""

from __future__ import annotations

import pytest

from sailor.errors import FetchFallbackFailedError
from sailor.fallback import load_fallback
from sailor.options import ResourceKind


def test_local_dir_fallback(tmp_path):
    (tmp_path / "billing-config.sailor.fall").write_bytes(b'{"database_url":"fallback"}')
    data = load_fallback("billing", ResourceKind.CONFIGS, base_url=str(tmp_path))
    assert data == b'{"database_url":"fallback"}'


def test_secret_fallback_filename(tmp_path):
    (tmp_path / "billing-secret.sailor.fall").write_bytes(b"encrypted")
    assert load_fallback("billing", ResourceKind.SECRETS, base_url=str(tmp_path)) == b"encrypted"


def test_no_base_configured_raises(monkeypatch):
    monkeypatch.delenv("SAILOR_FALLBACK_BASE_URL", raising=False)
    with pytest.raises(FetchFallbackFailedError):
        load_fallback("billing", ResourceKind.CONFIGS)


def test_missing_file_raises(tmp_path):
    with pytest.raises(FetchFallbackFailedError):
        load_fallback("billing", ResourceKind.CONFIGS, base_url=str(tmp_path))


def test_base_from_env(tmp_path, monkeypatch):
    (tmp_path / "app-config.sailor.fall").write_bytes(b"ok")
    monkeypatch.setenv("SAILOR_FALLBACK_BASE_URL", str(tmp_path))
    assert load_fallback("app", ResourceKind.CONFIGS) == b"ok"
