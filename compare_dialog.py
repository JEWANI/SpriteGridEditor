"""
compare_dialog.py
-------------------
`보기 → 프리셋 4색 비교…` (기획서 §4.5-3). 같은 회색 원본(현재 Source 셀, 없으면 시트 전체)을
프리셋 4개(+ 스쿼드색 1개, 선택)의 주/보조/포인트/패치 4색으로 나란히 입혀 본다.

- 각 칸 아래에 영역별 **가장 어두운 음영 / 가장 밝은 곳** 값(HSV V, 0~255)을 표시하고,
  색 밝기 V가 0.30 미만이거나 음영 폭이 너무 좁으면 경고 (CHR-ART.5 §4.4).
- 배치: 1줄 / 2줄. 색과 배치는 config `compare.*` 에 기억한다.
- 색은 직접 입력·붙여넣기만 (팔레트 CSV는 읽지 않음). 최종 표시색은 Unity 정비실에서 확인.
"""

from __future__ import annotations

import numpy as np
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QScrollArea, QVBoxLayout, QWidget,
)

import mask_convert as mc
from color_widgets import FourColorEdit, colors_from_config, colors_to_config
from config_manager import ConfigManager
from i18n import tr
from import_dialog import PreviewView

# 기본값: CHR-ART.5 §6.1 속옷 고정 프리셋 4종 + 스쿼드색 예시
DEFAULT_SLOTS = [
    {"name": "P1", "colors": ["#F4F1EA", "#F4F1EA", "#DAD5C8", "#C9C3B5"]},
    {"name": "P2", "colors": ["#2A2B30", "#2A2B30", "#4A4B53", "#5A5B63"]},
    {"name": "P3", "colors": ["#D8C3A5", "#D8C3A5", "#BFA987", "#A99274"]},
    {"name": "P4", "colors": ["#2F3E63", "#3B4C75", "#1F2A45", "#4A5C8A"]},
]
DEFAULT_SQUAD = {"name": "Squad", "colors": ["#3B5BA5", "#C8C8C8", "#C8102E", "#FFD700"]}


class SlotWidget(QWidget):
    def __init__(self, slot: dict, gray: np.ndarray, index: np.ndarray, gain: float, on_change):
        super().__init__()
        self.gray, self.index, self.gain = gray, index, gain
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        self.ed_name = QLineEdit(slot.get("name", ""))
        self.ed_name.editingFinished.connect(on_change)
        lay.addWidget(self.ed_name)
        self.view = PreviewView()
        self.view.setMinimumSize(170, 220)
        lay.addWidget(self.view, 1)
        self.lb_shade = QLabel()
        self.lb_shade.setWordWrap(True)
        lay.addWidget(self.lb_shade)
        default = [mc.parse_hex(h) for h in DEFAULT_SLOTS[0]["colors"]]
        self.colors = FourColorEdit(colors_from_config(slot.get("colors"), default), compact=True)
        self.colors.colorsChanged.connect(self.refresh)
        self.colors.colorsChanged.connect(on_change)
        lay.addWidget(self.colors)
        self.refresh()

    def to_config(self) -> dict:
        return {"name": self.ed_name.text().strip(), "colors": colors_to_config(self.colors.colors())}

    def refresh(self) -> None:
        cols = self.colors.colors()
        tinted = mc.tint(self.gray, self.index, cols, self.gain)
        self.view.set_array(tinted)
        rep = mc.shade_report(tinted, self.index, cols)
        if not rep:
            self.lb_shade.setText(tr("compare.no_region"))
            self.lb_shade.setObjectName("DimLabel")
        else:
            lines = []
            for s in rep:
                mark = " ⚠" if s.warn else ""
                lines.append(tr("compare.shade", name=tr(f"mask.idx{s.region}"), dark=s.darkest,
                                bright=s.brightest, v=f"{s.color_v:.2f}") + mark)
            self.lb_shade.setText("\n".join(lines))
            self.lb_shade.setObjectName("WarnLabel" if any(s.warn for s in rep) else "")
        self.lb_shade.style().unpolish(self.lb_shade)
        self.lb_shade.style().polish(self.lb_shade)


class CompareDialog(QDialog):
    def __init__(self, parent, gray: np.ndarray, index: np.ndarray, gain: float, config: ConfigManager,
                 scope_text: str):
        super().__init__(parent)
        self.setWindowTitle(tr("compare.title"))
        self.resize(1180, 760)
        self.config = config
        self.gray, self.index, self.gain = gray, index, gain

        root = QVBoxLayout(self)
        top = QHBoxLayout()
        lb_scope = QLabel(tr("compare.scope", scope=scope_text, gain=f"{gain:.2f}"))
        top.addWidget(lb_scope, 1)
        self.chk_squad = QCheckBox(tr("compare.with_squad"))
        self.chk_squad.setChecked(bool(config.get("compare.with_squad", True)))
        top.addWidget(self.chk_squad)
        self.cb_layout = QComboBox()
        self.cb_layout.addItem(tr("compare.layout_row"), "row")
        self.cb_layout.addItem(tr("compare.layout_grid"), "grid")
        self.cb_layout.setCurrentIndex(max(0, self.cb_layout.findData(config.get("compare.layout", "row"))))
        top.addWidget(QLabel(tr("compare.layout")))
        top.addWidget(self.cb_layout)
        btn_reset = QPushButton(tr("compare.reset"))
        top.addWidget(btn_reset)
        root.addLayout(top)

        note = QLabel(tr("compare.note"))
        note.setObjectName("DimLabel")
        note.setWordWrap(True)
        root.addWidget(note)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        root.addWidget(self.scroll, 1)

        btn_close = QPushButton(tr("btn.close"))
        btn_close.clicked.connect(self.accept)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(btn_close)
        root.addLayout(row)

        slots = config.get("compare.slots", None)
        if not isinstance(slots, list) or len(slots) < 4:
            slots = [dict(s) for s in DEFAULT_SLOTS]
        squad = config.get("compare.squad", None)
        if not isinstance(squad, dict):
            squad = dict(DEFAULT_SQUAD)
        self.slot_widgets = [SlotWidget(s, gray, index, gain, self._save) for s in slots[:4]]
        self.squad_widget = SlotWidget(squad, gray, index, gain, self._save)
        self._layout()

        self.chk_squad.toggled.connect(lambda _v: (self._layout(), self._save()))
        self.cb_layout.currentIndexChanged.connect(lambda _i: (self._layout(), self._save()))
        btn_reset.clicked.connect(self._reset)

    def _visible_slots(self) -> list[SlotWidget]:
        return self.slot_widgets + ([self.squad_widget] if self.chk_squad.isChecked() else [])

    def _layout(self) -> None:
        container = QWidget()
        grid = QGridLayout(container)
        slots = self._visible_slots()
        per_row = len(slots) if self.cb_layout.currentData() == "row" else 2 if len(slots) <= 4 else 3
        for w in self.slot_widgets + [self.squad_widget]:
            w.setParent(None)
        for i, w in enumerate(slots):
            grid.addWidget(w, i // per_row, i % per_row)
        self.squad_widget.setVisible(self.chk_squad.isChecked())
        self.scroll.setWidget(container)

    def _save(self) -> None:
        self.config.set("compare.slots", [w.to_config() for w in self.slot_widgets])
        self.config.set("compare.squad", self.squad_widget.to_config())
        self.config.set("compare.with_squad", self.chk_squad.isChecked())
        self.config.set("compare.layout", self.cb_layout.currentData())

    def _reset(self) -> None:
        for w, s in zip(self.slot_widgets, DEFAULT_SLOTS):
            w.ed_name.setText(s["name"])
            w.colors.set_colors([mc.parse_hex(h) for h in s["colors"]])
        self.squad_widget.ed_name.setText(DEFAULT_SQUAD["name"])
        self.squad_widget.colors.set_colors([mc.parse_hex(h) for h in DEFAULT_SQUAD["colors"]])
        self._save()
