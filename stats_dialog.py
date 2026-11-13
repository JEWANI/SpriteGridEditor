"""
stats_dialog.py
-----------------
`보기 → 마스크 영역 통계…` (기획서 §4.5-4). 보정이 얼마나 남았는지 숫자로 본다.

- 셀별 / 시트 전체: 영역(인덱스 1~4)별 픽셀 수·비율, **알파는 있는데 인덱스 0인 픽셀(영역 누락 의심)** 수
- 셀 간 일관성 검사: 어떤 영역이 다른 셀에는 있는데 이 셀에는 없거나 크게 적으면(중앙값의 30% 미만) 경고 칸 표시
- [누락 픽셀 강조]를 누르면 캔버스에 마젠타로 표시 (우측 패널 체크와 같음)
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout,
)

import mask_convert as mc
from i18n import tr
from sheet_model import cell_label

WARN_BG = QColor(255, 180, 60, 90)


def _fmt(n: int, total: int) -> str:
    return f"{n}  ({n * 100.0 / total:.1f}%)" if total else str(n)


class StatsDialog(QDialog):
    def __init__(self, parent, stats: mc.MaskStats, on_highlight=None):
        super().__init__(parent)
        self.setWindowTitle(tr("stats.title"))
        self.resize(860, 600)
        root = QVBoxLayout(self)

        summary = QLabel(tr("stats.summary", opaque=stats.opaque, miss=stats.missing,
                            pct=f"{stats.missing_ratio * 100:.1f}"))
        summary.setWordWrap(True)
        root.addWidget(summary)

        headers = [tr("stats.col_cell"), tr("stats.col_opaque")] + \
                  [tr(f"mask.idx{r}") for r in range(1, 5)] + [tr("stats.col_missing")]
        rows, cols = stats.cell_counts.shape[:2]
        table = QTableWidget(rows * cols + 1, len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QTableWidget.NoEditTriggers)
        bad = {(r, ri, ci) for r, ri, ci in stats.inconsistent}

        def put(row: int, col: int, text: str, warn: bool = False) -> None:
            item = QTableWidgetItem(text)
            item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter if col else Qt.AlignLeft | Qt.AlignVCenter)
            if warn:
                item.setBackground(QBrush(WARN_BG))
            table.setItem(row, col, item)

        put(0, 0, tr("stats.all"))
        put(0, 1, str(stats.opaque))
        for r in range(1, 5):
            put(0, r + 1, _fmt(int(stats.total[r]), stats.opaque))
        put(0, 6, _fmt(stats.missing, stats.opaque), stats.missing > 0)
        i = 1
        for ri in range(rows):
            for ci in range(cols):
                op = int(stats.cell_opaque[ri, ci])
                put(i, 0, cell_label(ri, ci))
                put(i, 1, str(op))
                for r in range(1, 5):
                    put(i, r + 1, _fmt(int(stats.cell_counts[ri, ci, r]), op), (r, ri, ci) in bad)
                miss = int(stats.cell_counts[ri, ci, 0])
                put(i, 6, _fmt(miss, op), miss > 0)
                i += 1
        table.resizeColumnsToContents()
        root.addWidget(table, 1)

        if stats.inconsistent:
            lines = [tr("stats.inconsistent_line", name=tr(f"mask.idx{r}"), cell=cell_label(ri, ci))
                     for r, ri, ci in stats.inconsistent[:30]]
            if len(stats.inconsistent) > 30:
                lines.append(tr("stats.more", n=len(stats.inconsistent) - 30))
            warn = QLabel(tr("stats.inconsistent_head") + "\n" + "\n".join(lines))
            warn.setObjectName("WarnLabel")
            warn.setWordWrap(True)
            root.addWidget(warn)
        note = QLabel(tr("stats.note"))
        note.setObjectName("DimLabel")
        note.setWordWrap(True)
        root.addWidget(note)

        btns = QHBoxLayout()
        if on_highlight is not None:
            b = QPushButton(tr("stats.highlight"))
            b.clicked.connect(lambda: (on_highlight(), self.accept()))
            btns.addWidget(b)
        btns.addStretch(1)
        close = QPushButton(tr("btn.close"))
        close.clicked.connect(self.accept)
        btns.addWidget(close)
        root.addLayout(btns)
