"""
convert_dialog.py
-------------------
`도구 → 컬러→회색+마스크 변환…` (기획서 §4.3). 열려 있는 컬러 옷 시트를 **회색 원본 + 인덱스 마스크**로 바꾼다.

- 미리보기 4개: 원본 / 회색 / 마스크(의사색) / 색 미리보기(회색 × 프리셋 색, Unity 셰이더와 같은 식)
- 회색: 밝기 정규화(0.42~1.0, 중앙값 0.72) — 기본 켬, 이미 회색이고 규칙 안이면 자동으로 꺼 두고 안내
- 분할 모드
  · 색 기반(기본): 영역 수 N(1~4) → [분할] = 시트 전체 Lab k-means. 칩마다 대표색·비율·인덱스(0~4) 지정.
    칩의 색 버튼을 누른 뒤 원본 미리보기를 클릭하면 그 색이 대표색이 되고 곧바로 다시 분할된다(스포이트).
  · 수평 분할(보조): 셀 안 y 기준선 위/아래에 인덱스 지정 (한 색짜리 옷)
  · 마스크 없음: 회색만 (고정색 옷)
- 잡티 정리(고립 1~2픽셀 흡수), 알파 0 → 인덱스 0
결과는 `result_gray` (RGBA) / `result_index` (H,W 또는 None) 로 돌려주고, 실제 적용(실행 취소 가능)은 메인 창이 한다.
"""

from __future__ import annotations

import numpy as np
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QButtonGroup, QCheckBox, QComboBox, QDialog, QFormLayout, QGridLayout, QGroupBox, QHBoxLayout,
    QLabel, QPushButton, QRadioButton, QSpinBox, QVBoxLayout, QWidget,
)

import mask_convert as mc
from config_manager import ConfigManager
from i18n import tr
from import_dialog import PreviewView
from sheet_model import GridSpec

MODE_COLOR, MODE_HSPLIT, MODE_NONE = "color", "hsplit", "none"


def index_combo(value: int) -> QComboBox:
    cb = QComboBox()
    for i in range(5):
        cb.addItem(tr(f"mask.idx{i}"), i)
    cb.setCurrentIndex(max(0, min(4, value)))
    return cb


def swatch_style(c: tuple[int, int, int]) -> str:
    text = "#000000" if (c[0] * 299 + c[1] * 587 + c[2] * 114) / 1000 > 140 else "#ffffff"
    return f"background-color: {mc.to_hex(c)}; color: {text};"


def blend_overlay(gray: np.ndarray, index: np.ndarray, alpha: float = 0.6) -> np.ndarray:
    """회색 위에 마스크 의사색을 섞은 미리보기 RGBA."""
    out = gray.copy()
    col = mc.pseudo_color(index, gray[..., 3] > 0)
    sel = col[..., 3] > 0
    out[sel, :3] = np.round(gray[sel, :3] * (1 - alpha) + col[sel, :3] * alpha).astype(np.uint8)
    return out


class ConvertDialog(QDialog):
    def __init__(self, parent, arr: np.ndarray, grid: GridSpec, config: ConfigManager,
                 preview_colors: list[tuple[int, int, int]], gain: float):
        super().__init__(parent)
        self.setWindowTitle(tr("dlg.convert.title"))
        self.setMinimumSize(1180, 760)
        self.src = arr
        self.grid = grid
        self.config = config
        self.preview_colors = preview_colors
        self.gain = gain
        self.opaque = mc.opaque_mask(arr)
        self.stats = mc.brightness_stats(arr)
        self.clusters: mc.Clusters | None = None
        self.mapping: list[int] = []
        self._pick_chip: int | None = None
        self.result_gray: np.ndarray | None = None
        self.result_index: np.ndarray | None = None
        self.result_normalized = False

        root = QHBoxLayout(self)

        # ---------------- 미리보기 2×2 ----------------------------------------
        grid_l = QGridLayout()
        self.views: dict[str, PreviewView] = {}
        for i, key in enumerate(("src", "gray", "mask", "tint")):
            box = QVBoxLayout()
            lb = QLabel(tr(f"dlg.convert.view_{key}"))
            view = PreviewView(pickable=(key == "src"))
            view.setMinimumSize(240, 260)
            box.addWidget(lb)
            box.addWidget(view, 1)
            w = QWidget()
            w.setLayout(box)
            grid_l.addWidget(w, i // 2, i % 2)
            self.views[key] = view
        self.views["src"].set_array(arr)
        self.views["src"].pixelClicked.connect(self._on_pick_pixel)
        left = QVBoxLayout()
        left.addLayout(grid_l, 1)
        self.lb_hint = QLabel(tr("dlg.convert.pick_hint"))
        self.lb_hint.setObjectName("DimLabel")
        left.addWidget(self.lb_hint)
        root.addLayout(left, 1)

        # ---------------- 설정 -------------------------------------------------
        panel = QWidget()
        panel.setFixedWidth(430)
        right = QVBoxLayout(panel)
        right.setContentsMargins(0, 0, 0, 0)
        root.addWidget(panel)

        # 회색
        g_gray = QGroupBox(tr("dlg.convert.group_gray"))
        lg = QVBoxLayout(g_gray)
        self.lb_bright = QLabel()
        self.lb_bright.setWordWrap(True)
        lg.addWidget(self.lb_bright)
        self.chk_norm = QCheckBox(tr("dlg.convert.normalize"))
        self.chk_norm.setChecked(mc.should_normalize(self.stats))
        lg.addWidget(self.chk_norm)
        self.lb_norm_note = QLabel()
        self.lb_norm_note.setWordWrap(True)
        self.lb_norm_note.setObjectName("DimLabel")
        lg.addWidget(self.lb_norm_note)
        right.addWidget(g_gray)

        # 분할
        g_mask = QGroupBox(tr("dlg.convert.group_mask"))
        lm = QVBoxLayout(g_mask)
        self.rb_color = QRadioButton(tr("dlg.convert.mode_color"))
        self.rb_hsplit = QRadioButton(tr("dlg.convert.mode_hsplit"))
        self.rb_none = QRadioButton(tr("dlg.convert.mode_none"))
        self.mode_group = QButtonGroup(self)
        for rb in (self.rb_color, self.rb_hsplit, self.rb_none):
            self.mode_group.addButton(rb)
            lm.addWidget(rb)

        # 색 기반
        self.w_color = QWidget()
        fc = QFormLayout(self.w_color)
        fc.setContentsMargins(18, 0, 0, 0)
        row = QHBoxLayout()
        self.sp_k = QSpinBox()
        self.sp_k.setRange(1, mc.REGION_COUNT)
        self.sp_k.setValue(int(config.get("convert.regions", 2)))
        self.btn_split = QPushButton(tr("dlg.convert.split"))
        row.addWidget(self.sp_k)
        row.addWidget(self.btn_split, 1)
        fc.addRow(tr("dlg.convert.regions"), row)
        self.sp_lw = QSpinBox()
        self.sp_lw.setRange(0, 100)
        self.sp_lw.setSuffix(" %")
        self.sp_lw.setValue(int(config.get("convert.l_weight", 50)))
        self.sp_lw.setToolTip(tr("dlg.convert.l_weight_tip"))
        fc.addRow(tr("dlg.convert.l_weight"), self.sp_lw)
        self.chips_box = QVBoxLayout()
        chips_w = QWidget()
        chips_w.setLayout(self.chips_box)
        fc.addRow(chips_w)
        lm.addWidget(self.w_color)

        # 수평 분할
        self.w_hsplit = QWidget()
        fh = QFormLayout(self.w_hsplit)
        fh.setContentsMargins(18, 0, 0, 0)
        self.sp_y = QSpinBox()
        self.sp_y.setRange(0, grid.cell_h)
        self.sp_y.setSuffix(" px")
        y = int(config.get("convert.hsplit_y", 0) or 0)
        self.sp_y.setValue(y if 0 < y <= grid.cell_h else grid.cell_h // 2)
        fh.addRow(tr("dlg.convert.hsplit_y", ch=grid.cell_h), self.sp_y)
        self.cb_upper = index_combo(1)
        self.cb_lower = index_combo(2)
        fh.addRow(tr("dlg.convert.hsplit_upper"), self.cb_upper)
        fh.addRow(tr("dlg.convert.hsplit_lower"), self.cb_lower)
        lm.addWidget(self.w_hsplit)

        self.chk_speck = QCheckBox(tr("dlg.convert.despeckle"))
        self.chk_speck.setChecked(bool(config.get("convert.despeckle", True)))
        lm.addWidget(self.chk_speck)
        right.addWidget(g_mask)

        self.lb_stats = QLabel()
        self.lb_stats.setWordWrap(True)
        right.addWidget(self.lb_stats)
        colors_note = QLabel(tr("dlg.convert.colors_note"))
        colors_note.setWordWrap(True)
        colors_note.setObjectName("DimLabel")
        right.addWidget(colors_note)
        right.addStretch(1)

        btns = QHBoxLayout()
        self.btn_apply = QPushButton(tr("dlg.convert.apply"))
        self.btn_apply.setDefault(True)
        self.btn_cancel = QPushButton(tr("dlg.import.cancel"))
        btns.addWidget(self.btn_apply, 2)
        btns.addWidget(self.btn_cancel, 1)
        right.addLayout(btns)

        # ---------------- 연결 / 초기값 ---------------------------------------
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(120)
        self._timer.timeout.connect(self._refresh)

        mode = config.get("convert.mode", MODE_COLOR)
        {MODE_HSPLIT: self.rb_hsplit, MODE_NONE: self.rb_none}.get(mode, self.rb_color).setChecked(True)
        self.mode_group.buttonToggled.connect(lambda *_: self._on_mode())
        self.btn_split.clicked.connect(self._run_kmeans)
        self.sp_k.valueChanged.connect(lambda _v: self._run_kmeans())
        self.sp_lw.valueChanged.connect(lambda _v: self._reassign_or_split())
        for w in (self.chk_norm, self.chk_speck):
            w.toggled.connect(self._schedule)
        for w in (self.cb_upper, self.cb_lower):
            w.currentIndexChanged.connect(self._schedule)
        self.sp_y.valueChanged.connect(self._schedule)
        self.btn_apply.clicked.connect(self._accept)
        self.btn_cancel.clicked.connect(self.reject)

        self._update_bright_label()
        self._run_kmeans(refresh=False)
        self._on_mode()

    # ------------------------------------------------------------------ #
    def mode(self) -> str:
        if self.rb_hsplit.isChecked():
            return MODE_HSPLIT
        if self.rb_none.isChecked():
            return MODE_NONE
        return MODE_COLOR

    def _l_weight(self) -> float:
        return self.sp_lw.value() / 100.0

    def _schedule(self, *_a) -> None:
        self._timer.start()

    def _update_bright_label(self) -> None:
        s = self.stats
        kind = tr("dlg.convert.is_gray") if s.is_gray else tr("dlg.convert.is_color")
        self.lb_bright.setText(tr("dlg.convert.bright", p2=f"{s.p2:.2f}", med=f"{s.median:.2f}",
                                  p98=f"{s.p98:.2f}", kind=kind))
        if not mc.should_normalize(s):
            self.lb_norm_note.setText(tr("dlg.convert.norm_auto_off"))
        else:
            self.lb_norm_note.setText(tr("dlg.convert.norm_rule"))

    def _on_mode(self) -> None:
        m = self.mode()
        self.w_color.setVisible(m == MODE_COLOR)
        self.w_hsplit.setVisible(m == MODE_HSPLIT)
        self.chk_speck.setEnabled(m != MODE_NONE)
        self._schedule()

    # -- 색 기반 분할 ------------------------------------------------------
    def _run_kmeans(self, refresh: bool = True) -> None:
        self.clusters = mc.kmeans(self.src, self.sp_k.value(), self._l_weight())
        self.mapping = [min(i + 1, 4) for i in range(self.clusters.k)]
        self._rebuild_chips()
        if refresh:
            self._schedule()

    def _reassign_or_split(self) -> None:
        if self.clusters is not None and self.clusters.k:
            self._reassign(self.clusters.centers_rgb)
        else:
            self._run_kmeans()

    def _reassign(self, reps: list[tuple[int, int, int]]) -> None:
        mapping = list(self.mapping)
        self.clusters = mc.assign_to_colors(self.src, reps, self._l_weight())
        self.mapping = mapping[:self.clusters.k] + [min(i + 1, 4) for i in range(len(mapping), self.clusters.k)]
        self._rebuild_chips()
        self._schedule()

    def _rebuild_chips(self) -> None:
        while self.chips_box.count():
            item = self.chips_box.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        cl = self.clusters
        total = max(1, sum(cl.sizes)) if cl else 1
        for i in range(cl.k if cl else 0):
            row = QWidget()
            h = QHBoxLayout(row)
            h.setContentsMargins(0, 0, 0, 0)
            btn = QPushButton(mc.to_hex(cl.centers_rgb[i]))
            btn.setStyleSheet(swatch_style(cl.centers_rgb[i]))
            btn.setFixedWidth(96)
            btn.setToolTip(tr("dlg.convert.chip_pick_tip"))
            btn.clicked.connect(lambda _c=False, i=i: self._start_pick(i))
            pct = QLabel(f"{cl.sizes[i] * 100 / total:.1f}%")
            pct.setFixedWidth(56)
            combo = index_combo(self.mapping[i])
            combo.currentIndexChanged.connect(lambda _x, i=i, cb=combo: self._on_map(i, cb.currentData()))
            h.addWidget(btn)
            h.addWidget(pct)
            h.addWidget(combo, 1)
            self.chips_box.addWidget(row)

    def _on_map(self, i: int, value: int) -> None:
        if 0 <= i < len(self.mapping):
            self.mapping[i] = int(value)
            self._schedule()

    def _start_pick(self, i: int) -> None:
        self._pick_chip = i
        self.lb_hint.setText(tr("dlg.convert.picking", n=i + 1))
        self.lb_hint.setObjectName("WarnLabel")
        self.lb_hint.style().unpolish(self.lb_hint)
        self.lb_hint.style().polish(self.lb_hint)

    def _on_pick_pixel(self, x: int, y: int) -> None:
        if self._pick_chip is None or self.clusters is None:
            return
        px = self.src[y, x]
        if px[3] == 0:
            return
        reps = list(self.clusters.centers_rgb)
        reps[self._pick_chip] = (int(px[0]), int(px[1]), int(px[2]))
        self._pick_chip = None
        self.lb_hint.setText(tr("dlg.convert.pick_hint"))
        self.lb_hint.setObjectName("DimLabel")
        self.lb_hint.style().unpolish(self.lb_hint)
        self.lb_hint.style().polish(self.lb_hint)
        self.rb_color.setChecked(True)
        self._reassign(reps)

    # -- 계산 / 미리보기 ---------------------------------------------------
    def compute(self) -> tuple[np.ndarray, np.ndarray | None]:
        gray = mc.make_gray_rgba(self.src, self.chk_norm.isChecked())
        m = self.mode()
        if m == MODE_NONE:
            return gray, None
        if m == MODE_HSPLIT:
            index = mc.horizontal_split(self.src, self.grid, self.sp_y.value(),
                                        self.cb_upper.currentData(), self.cb_lower.currentData())
        else:
            if self.clusters is None:
                self._run_kmeans(refresh=False)
            index = mc.labels_to_index(self.clusters.labels, self.mapping)
        if self.chk_speck.isChecked():
            index = mc.despeckle(index, self.opaque)
        index, _n = mc.enforce_alpha(index, self.src[..., 3])
        return gray, index

    def _refresh(self) -> None:
        gray, index = self.compute()
        self.views["gray"].set_array(gray)
        if index is None:
            self.views["mask"].set_array(None)
            self.views["tint"].set_array(mc.tint(gray, np.zeros(gray.shape[:2], np.uint8),
                                                 self.preview_colors, self.gain))
            self.lb_stats.setText(tr("dlg.convert.no_mask"))
            return
        self.views["mask"].set_array(blend_overlay(gray, index))
        self.views["tint"].set_array(mc.tint(gray, index, self.preview_colors, self.gain))
        st = mc.mask_stats(index, self.src[..., 3], self.grid)
        parts = [tr("dlg.convert.stat_region", name=tr(f"mask.idx{r}"), pct=f"{st.ratio(r) * 100:.1f}")
                 for r in range(1, 5) if st.total[r]]
        text = " · ".join(parts) if parts else tr("dlg.convert.stat_empty")
        text += "\n" + tr("dlg.convert.stat_missing", n=st.missing, pct=f"{st.missing_ratio * 100:.1f}")
        if st.inconsistent:
            text += "\n" + tr("dlg.convert.stat_inconsistent", n=len(st.inconsistent))
        self.lb_stats.setText(text)

    def _accept(self) -> None:
        self._timer.stop()
        self.result_gray, self.result_index = self.compute()
        self.result_normalized = self.chk_norm.isChecked()
        cfg = self.config
        cfg.set("convert.mode", self.mode())
        cfg.set("convert.regions", self.sp_k.value())
        cfg.set("convert.l_weight", self.sp_lw.value())
        cfg.set("convert.hsplit_y", self.sp_y.value())
        cfg.set("convert.despeckle", self.chk_speck.isChecked())
        self.accept()
