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


_qt_translator = None  # Qt 기본 문구(메시지 상자 Save/Cancel, 색 선택 창 등) 번역기


def _qt_translation_dirs() -> list[str]:
    """qtbase_*.qm 을 찾을 폴더: exe 번들(translations/) → Qt 설치 경로 → PySide6 패키지."""
    dirs = [resource_path("translations")]
    try:
        from PySide6.QtCore import QLibraryInfo
        dirs.append(QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath))
    except Exception:
        pass
    try:
        import PySide6
        dirs.append(os.path.join(os.path.dirname(PySide6.__file__), "translations"))
    except Exception:
        pass
    return dirs


def install_qt_translator(lang: str) -> bool:
    """Qt 표준 버튼·대화상자 문구를 lang 으로 (영어는 Qt 기본값이라 번역기를 뺀다).

    QMessageBox 의 Save/Discard/Cancel, QDialogButtonBox 의 OK/Cancel, QColorDialog 등이
    한국어 화면에서 영어로 나오던 문제 (v0.4.1). QApplication 이 없으면 아무것도 하지 않는다.
    """
    global _qt_translator
    try:
        from PySide6.QtCore import QCoreApplication, QTranslator
    except Exception:
        return False
    app = QCoreApplication.instance()
    if app is None:
        return False
    if _qt_translator is not None:
        app.removeTranslator(_qt_translator)
        _qt_translator = None
    if lang == "en":
        return True
    tr_obj = QTranslator(app)
    for d in _qt_translation_dirs():
        if d and tr_obj.load(f"qtbase_{lang}", d):
            app.installTranslator(tr_obj)
            _qt_translator = tr_obj
            return True
    return False


def set_language(lang: str) -> None:
    global _current
    _current = lang if lang in SUPPORTED_LANGUAGES else "ko"
    _load_table(_current)
    _load_table("ko")
    install_qt_translator(_current)


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
