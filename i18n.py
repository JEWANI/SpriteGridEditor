"""
i18n.py
--------
문자열 번역. UI 문자열은 코드에 하드코딩하지 않고 ``tr("btn.close")`` 식으로 호출한다.

- 언어 파일: locales/ko.json, locales/en.json (번들 리소스, config_manager.resource_path)
- 폴백: 현재 언어에 없으면 ko → 그래도 없으면 **키 문자열 그대로** 표시(개발 중 누락 발견용).
- ``Translatable`` 믹스인: 위젯의 문구 setter를 키와 함께 등록해 두면 언어를 바꿀 때
  ``retranslate_all()`` 한 번으로 메뉴·툴바·패널 문구가 즉시 갱신된다.
"""

from __future__ import annotations

import json
import os
from typing import Callable

from config_manager import resource_path

SUPPORTED_LANGUAGES = ("ko", "en")
LANGUAGE_NATIVE_NAMES = {"ko": "한국어", "en": "English"}

_tables: dict[str, dict[str, str]] = {}
_current = "ko"


def _load_table(lang: str) -> dict[str, str]:
    if lang in _tables:
        return _tables[lang]
    path = resource_path(os.path.join("locales", f"{lang}.json"))
    try:
        with open(path, "r", encoding="utf-8") as f:
            table = json.load(f)
    except (OSError, json.JSONDecodeError):
        table = {}
    _tables[lang] = table
    return table


def set_language(lang: str) -> None:
    global _current
    _current = lang if lang in SUPPORTED_LANGUAGES else "ko"
    _load_table(_current)
    _load_table("ko")


def current_language() -> str:
    return _current


def tr(key: str, **kwargs) -> str:
    text = _load_table(_current).get(key)
    if text is None:
        text = _load_table("ko").get(key)
    if text is None:
        return key
    if kwargs:
        try:
            return text.format(**kwargs)
        except (KeyError, IndexError, ValueError):
            return text
    return text


class Translatable:
    """문구 setter를 등록해 두고 언어 변경 시 일괄 갱신하는 믹스인."""

    def _t(self, setter: Callable[[str], None], key: str, **kwargs) -> None:
        items = self.__dict__.setdefault("_tr_items", [])
        items.append((setter, key, kwargs))
        setter(tr(key, **kwargs))

    def retranslate_registered(self) -> None:
        for setter, key, kwargs in self.__dict__.get("_tr_items", []):
            setter(tr(key, **kwargs))
