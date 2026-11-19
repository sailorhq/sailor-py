"""Per-instance lang normalization and pluggable decoders (CONS-RES-5..8)."""

from __future__ import annotations

import json
from typing import Any, Callable

from .errors import UnsupportedLangError

DecodeFunc = Callable[[bytes], Any]


def normalize_lang(lang: str) -> str:
    """Trim, lowercase, map empty to ``json`` and ``yml`` to ``yaml`` (CONS-RES-5)."""
    cleaned = lang.strip().lower()
    if cleaned == "":
        return "json"
    if cleaned == "yml":
        return "yaml"
    return cleaned


def _json_decode(raw: bytes) -> Any:
    return json.loads(raw.decode("utf-8"))


class DecoderSet:
    """Per-consumer decoder registry (CONS-RES-8)."""

    def __init__(self, extra: dict[str, DecodeFunc] | None = None) -> None:
        self._decoders: dict[str, DecodeFunc] = {"json": _json_decode}
        if extra:
            for lang, fn in extra.items():
                self._decoders[normalize_lang(lang)] = fn

    def lookup(self, lang: str) -> DecodeFunc:
        key = normalize_lang(lang)
        fn = self._decoders.get(key)
        if fn is None:
            raise UnsupportedLangError(f"no decoder registered for lang {key!r}")
        return fn
