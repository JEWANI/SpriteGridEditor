"""
color_widgets.py
------------------
옷 색 4개(주/보조/포인트/패치) 입력 위젯 — 우측 패널 `옷 색 미리보기`와 `프리셋 4색 비교` 창이 같이 쓴다.

- 색은 `#RRGGBB` 직접 입력 또는 붙여넣기 (기획서 §4.3-4: 팔레트 CSV는 읽지 않는다).
- [붙여넣기]: 클립보드 글에서 `#RRGGBB`/`RRGGBB`를 앞에서부터 최대 4개 찾아 채운다
  (구글시트 `OutfitPalettes`/`SquadPalettes` 한 줄을 복사해 붙여도 된다).
- 색 칸을 누르면 색 선택 창.
"""

from __future__ import annotations

import re

from PySide6.QtCore import Signal
from PySide6.QtGui import QColor, QGuiApplication
from PySide6.QtWidgets import (
    QColorDialog, QGridLayout, QLabel, QLineEdit, QPushButton, QWidget,
)

import mask_convert as mc
from i18n import tr

HEX_RE = re.compile(r"#?\b([0-9A-Fa-f]{6})\b")
REGION_KEYS = ("mask.idx1", "mask.idx2", "mask.idx3", "mask.idx4")


def find_hex_colors(text: str, limit: int = 4) -> list[tuple[int, int, int]]:
    """글에서 #RRGGBB / RRGGBB 를 순서대로 최대 limit개."""
    out = []
    for m in HEX_RE.finditer(text):
        c = mc.parse_hex(m.group(1))
        if c is not None:
            out.append(c)
        if len(out) >= limit:
            break
    return out


def _text_color(c: tuple[int, int, int]) -> str:
    return "#000000" if (c[0] * 299 + c[1] * 587 + c[2] * 114) / 1000 > 140 else "#ffffff"


class FourColorEdit(QWidget):
    """주/보조/포인트/패치 4색 입력. colorsChanged는 유효한 색이 바뀔 때마다."""

    colorsChanged = Signal()

    def __init__(self, colors: list[tuple[int, int, int]], parent=None, compact: bool = False):
        super().__init__(parent)
        self._colors = [tuple(c) for c in (list(colors) + [(255, 255, 255)] * 4)[:4]]
        grid = QGridLayout(self)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(4)
        self.edits: list[QLineEdit] = []
        self.swatches: list[QPushButton] = []
        for i in range(4):
            lb = QLabel(tr(REGION_KEYS[i]))
            sw = QPushButton()
            sw.setFixedSize(26, 22)
            sw.setToolTip(tr("colors.pick_tip"))
            sw.clicked.connect(lambda _c=False, i=i: self._pick(i))
            ed = QLineEdit(mc.to_hex(self._colors[i]))
            ed.setMaxLength(7)
            ed.setMinimumWidth(72)
            ed.editingFinished.connect(lambda i=i: self._on_edit(i))
            if compact:
                grid.addWidget(lb, i // 2, (i % 2) * 3)
                grid.addWidget(sw, i // 2, (i % 2) * 3 + 1)
                grid.addWidget(ed, i // 2, (i % 2) * 3 + 2)
            else:
                grid.addWidget(lb, i, 0)
                grid.addWidget(sw, i, 1)
                grid.addWidget(ed, i, 2)
            self.edits.append(ed)
            self.swatches.append(sw)
        self.btn_paste = QPushButton(tr("colors.paste"))
        self.btn_paste.setToolTip(tr("colors.paste_tip"))
        self.btn_paste.clicked.connect(self.paste_from_clipboard)
        if compact:
            grid.addWidget(self.btn_paste, 2, 0, 1, 6)
        else:
            grid.addWidget(self.btn_paste, 4, 0, 1, 3)
        self._refresh_swatches()

    def colors(self) -> list[tuple[int, int, int]]:
        return list(self._colors)

    def set_colors(self, colors: list[tuple[int, int, int]], emit: bool = True) -> None:
        for i, c in enumerate(colors[:4]):
            self._colors[i] = tuple(c)
            self.edits[i].setText(mc.to_hex(self._colors[i]))
        self._refresh_swatches()
        if emit:
            self.colorsChanged.emit()

    def paste_from_clipboard(self) -> int:
        found = find_hex_colors(QGuiApplication.clipboard().text() or "")
        if found:
            self.set_colors(found + self._colors[len(found):])
        return len(found)

    def _on_edit(self, i: int) -> None:
        c = mc.parse_hex(self.edits[i].text())
        if c is None:
            self.edits[i].setText(mc.to_hex(self._colors[i]))  # 잘못된 값 → 되돌림
            return
        if c != self._colors[i]:
            self._colors[i] = c
            self.edits[i].setText(mc.to_hex(c))
            self._refresh_swatches()
            self.colorsChanged.emit()

    def _pick(self, i: int) -> None:
        col = QColorDialog.getColor(QColor(*self._colors[i]), self, tr(REGION_KEYS[i]))
        if col.isValid():
            self._colors[i] = (col.red(), col.green(), col.blue())
            self.edits[i].setText(mc.to_hex(self._colors[i]))
            self._refresh_swatches()
            self.colorsChanged.emit()

    def _refresh_swatches(self) -> None:
        for i, sw in enumerate(self.swatches):
            c = self._colors[i]
            sw.setStyleSheet(f"background-color: {mc.to_hex(c)}; border: 1px solid {_text_color(c)};")


def colors_to_config(colors: list[tuple[int, int, int]]) -> list[str]:
    return [mc.to_hex(c) for c in colors]


def colors_from_config(values, default: list[tuple[int, int, int]]) -> list[tuple[int, int, int]]:
    out = []
    for i in range(4):
        c = mc.parse_hex(values[i]) if isinstance(values, list) and i < len(values) and isinstance(values[i], str) else None
        out.append(c if c is not None else default[i])
    return out
