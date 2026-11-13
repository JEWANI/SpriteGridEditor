"""
import_dialog.py
------------------
이미지 가져오기 팝업 — 열기/끌어다 놓기로 넣은 이미지의 **크기**와 **배경**을 시트로 열기 전에 정리한다.

- 원본 크기·배경을 자동으로 계산해 보여 준다 (투명 / 단색 #RRGGBB / 단색 아님).
- 크기: `자동 조정(비율 유지)`이 기본 — 가로를 바꾸면 세로가 따라 바뀌고, 세로를 바꾸면 가로가 따라 바뀐다.
        `사용자 지정`은 가로·세로를 따로 입력. 시트 규격을 고르면 그 크기에 맞춰 자동 계산한다.
- 시트 맞춤: 결과가 규격 크기와 다르면 투명 여백을 채우거나 넘치는 부분을 잘라 규격 크기로 만든다
             → 그리드와 Base Body가 바로 맞는다.
- 배경: 그대로 / 자동(테두리 색 투명) / 지정 색 투명 (색 버튼 또는 원본 미리보기 클릭으로 고르기),
        허용 오차와 범위(가장자리와 이어진 부분만 / 같은 색 전부).
실제 픽셀 처리는 import_utils(순수 함수)가 한다.
"""

from __future__ import annotations

import numpy as np
from PySide6.QtCore import QPoint, QRect, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtWidgets import (
    QButtonGroup, QCheckBox, QColorDialog, QComboBox, QDialog, QFormLayout, QGroupBox,
    QHBoxLayout, QLabel, QPushButton, QRadioButton, QSizePolicy, QSpinBox, QVBoxLayout, QWidget,
)

import import_utils as iu
from config_manager import ConfigManager
from i18n import tr
from preset_dialog import preset_list_label
from sheet_model import find_matching_presets, preset_spec

MAX_SIZE = 16384
BG_KEEP, BG_AUTO, BG_PICK = "keep", "auto", "pick"


def _spin(lo: int, hi: int, value: int) -> QSpinBox:
    s = QSpinBox()
    s.setRange(lo, hi)
    s.setValue(value)
    s.setKeyboardTracking(False)  # 숫자를 다 친 뒤(엔터/포커스 이동)에 반영
    s.setAccelerated(True)
    return s


class PreviewView(QWidget):
    """체크무늬 위에 이미지를 정수 배율 또는 축소(Nearest)로 맞춰 그린다. 클릭하면 이미지 좌표를 알린다."""

    pixelClicked = Signal(int, int)

    def __init__(self, parent=None, pickable: bool = False):
        super().__init__(parent)
        self._img: QImage | None = None
        self._buf: np.ndarray | None = None  # QImage가 참조하는 배열 수명 유지
        self._pickable = pickable
        self.setMinimumSize(260, 300)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        if pickable:
            self.setCursor(Qt.CrossCursor)

    def set_array(self, arr: np.ndarray | None) -> None:
        if arr is None:
            self._img, self._buf = None, None
        else:
            self._buf = np.ascontiguousarray(arr)
            h, w = self._buf.shape[:2]
            self._img = QImage(self._buf.data, w, h, w * 4, QImage.Format_RGBA8888)
        self.update()

    def _target_rect(self) -> QRect:
        if self._img is None:
            return QRect()
        iw, ih = self._img.width(), self._img.height()
        aw, ah = self.width() - 8, self.height() - 8
        scale = min(aw / iw, ah / ih)
        if scale >= 1:
            scale = int(scale)  # 확대는 정수 배율만 (도트 유지)
        w, h = max(1, int(iw * scale)), max(1, int(ih * scale))
        return QRect((self.width() - w) // 2, (self.height() - h) // 2, w, h)

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.fillRect(self.rect(), QColor("#14141c"))
        if self._img is None:
            return
        r = self._target_rect()
        s = 8
        a, b = QColor("#9a9a9a"), QColor("#6e6e6e")
        for yy in range(r.top(), r.bottom() + 1, s):
            for xx in range(r.left(), r.right() + 1, s):
                cell = QRect(xx, yy, min(s, r.right() + 1 - xx), min(s, r.bottom() + 1 - yy))
                p.fillRect(cell, a if ((xx - r.left()) // s + (yy - r.top()) // s) % 2 == 0 else b)
        p.setRenderHint(QPainter.SmoothPixmapTransform, False)
        p.drawImage(r, self._img)
        p.setPen(QColor("#5a5a70"))
        p.drawRect(r.adjusted(0, 0, -1, -1))

    def mousePressEvent(self, e) -> None:
        if not self._pickable or self._img is None or e.button() != Qt.LeftButton:
            return
        r = self._target_rect()
        pos: QPoint = e.position().toPoint()
        if not r.contains(pos):
            return
        x = (pos.x() - r.left()) * self._img.width() // r.width()
        y = (pos.y() - r.top()) * self._img.height() // r.height()
        self.pixelClicked.emit(int(x), int(y))


class ImportDialog(QDialog):
    """결과: `exec()`가 Accepted면 `result_arr`(처리 결과)·`result_preset`(규격 dict 또는 None)·`open_raw`."""

    def __init__(self, parent, arr: np.ndarray, name: str, presets: list[dict], config: ConfigManager):
        super().__init__(parent)
        self.setWindowTitle(tr("dlg.import.title"))
        self.setMinimumSize(980, 640)
        self.src = arr
        self.src_h, self.src_w = arr.shape[:2]
        self.presets = presets
        self.config = config
        self.info = iu.detect_background(arr)
        self.pick_color: tuple[int, int, int] = self.info.color
        self.result_arr: np.ndarray | None = None
        self.result_preset: dict | None = None
        self.open_raw = False
        self._bg_cache_key = None
        self._bg_cache: np.ndarray | None = None
        self._busy = False  # 가로↔세로 자동 계산 중 재귀 방지

        root = QHBoxLayout(self)

        # ---------------- 왼쪽: 미리보기 -------------------------------------
        left = QVBoxLayout()
        previews = QHBoxLayout()
        col_a, col_b = QVBoxLayout(), QVBoxLayout()
        self.lb_src_title = QLabel(tr("dlg.import.preview_src"))
        self.view_src = PreviewView(pickable=True)
        self.view_src.set_array(arr)
        self.view_src.pixelClicked.connect(self._on_pick_pixel)
        self.lb_dst_title = QLabel(tr("dlg.import.preview_dst"))
        self.view_dst = PreviewView()
        col_a.addWidget(self.lb_src_title)
        col_a.addWidget(self.view_src, 1)
        col_b.addWidget(self.lb_dst_title)
        col_b.addWidget(self.view_dst, 1)
        previews.addLayout(col_a, 1)
        previews.addLayout(col_b, 1)
        left.addLayout(previews, 1)
        hint = QLabel(tr("dlg.import.pick_hint"))
        hint.setObjectName("DimLabel")
        left.addWidget(hint)
        root.addLayout(left, 1)

        # ---------------- 오른쪽: 설정 ----------------------------------------
        right = QVBoxLayout()
        panel = QWidget()
        panel.setFixedWidth(440)
        panel.setLayout(right)
        root.addWidget(panel)

        self.lb_src_info = QLabel()
        self.lb_src_info.setWordWrap(True)
        right.addWidget(self.lb_src_info)

        # 크기 ---------------------------------------------------------------
        g_size = QGroupBox(tr("dlg.import.group_size"))
        fs = QFormLayout(g_size)
        self.cb_preset = QComboBox()
        for p in presets:
            spec = preset_spec(p)
            self.cb_preset.addItem(tr("dlg.import.preset_item", label=preset_list_label(p),
                                      w=spec.sheet_w, h=spec.sheet_h), p["id"])
        self.cb_preset.addItem(tr("dlg.import.preset_none"), "")
        fs.addRow(tr("dlg.import.preset"), self.cb_preset)

        mode_row = QHBoxLayout()
        self.rb_auto = QRadioButton(tr("dlg.import.size_auto"))
        self.rb_custom = QRadioButton(tr("dlg.import.size_custom"))
        self.size_group = QButtonGroup(self)
        self.size_group.addButton(self.rb_auto)
        self.size_group.addButton(self.rb_custom)
        mode_row.addWidget(self.rb_auto)
        mode_row.addWidget(self.rb_custom)
        mode_row.addStretch(1)
        fs.addRow(mode_row)

        wh = QHBoxLayout()
        self.sp_w = _spin(1, MAX_SIZE, self.src_w)
        self.sp_h = _spin(1, MAX_SIZE, self.src_h)
        self.sp_w.setSuffix(" px")
        self.sp_h.setSuffix(" px")
        self.lb_link = QLabel()
        wh.addWidget(self.sp_w, 1)
        wh.addWidget(self.lb_link)
        wh.addWidget(self.sp_h, 1)
        fs.addRow(tr("dlg.import.size_wh"), wh)

        quick = QHBoxLayout()
        self.btn_fit = QPushButton(tr("dlg.import.fit_preset"))
        self.btn_orig = QPushButton(tr("dlg.import.orig_size"))
        self.btn_half = QPushButton("50%")
        quick.addWidget(self.btn_fit, 2)
        quick.addWidget(self.btn_orig, 1)
        quick.addWidget(self.btn_half, 1)
        fs.addRow(quick)
        self.lb_scale = QLabel()
        self.lb_scale.setObjectName("DimLabel")
        fs.addRow(self.lb_scale)

        self.cb_resample = QComboBox()
        for key in (iu.RESAMPLE_NEAREST, iu.RESAMPLE_BOX, iu.RESAMPLE_LANCZOS):
            self.cb_resample.addItem(tr(f"dlg.import.resample_{key}"), key)
        fs.addRow(tr("dlg.import.resample"), self.cb_resample)

        self.chk_canvas = QCheckBox()
        self.chk_canvas.setText(tr("dlg.import.fit_canvas", w=0, h=0))
        self.chk_canvas.setToolTip(tr("dlg.import.fit_canvas_tip"))
        fs.addRow(self.chk_canvas)
        self.cb_align = QComboBox()
        for key in (iu.ALIGN_CENTER, iu.ALIGN_BOTTOM, iu.ALIGN_TOP):
            self.cb_align.addItem(tr(f"dlg.import.align_{key}"), key)
        fs.addRow(tr("dlg.import.align"), self.cb_align)
        right.addWidget(g_size)

        # 배경 ---------------------------------------------------------------
        g_bg = QGroupBox(tr("dlg.import.group_bg"))
        fb = QFormLayout(g_bg)
        self.rb_keep = QRadioButton(tr("dlg.import.bg_keep"))
        self.rb_bg_auto = QRadioButton(tr("dlg.import.bg_auto", color=iu.color_hex(self.info.color)))
        self.rb_pick = QRadioButton(tr("dlg.import.bg_pick"))
        self.bg_group = QButtonGroup(self)
        for rb in (self.rb_keep, self.rb_bg_auto, self.rb_pick):
            self.bg_group.addButton(rb)
            fb.addRow(rb)
        pick_row = QHBoxLayout()
        self.btn_color = QPushButton()
        self.btn_color.setFixedWidth(150)
        self.btn_color.clicked.connect(self._choose_color)
        pick_row.addWidget(self.btn_color)
        pick_row.addStretch(1)
        fb.addRow(tr("dlg.import.bg_color"), pick_row)
        self.sp_tol = _spin(0, 255, int(config.get("import.tolerance", 32)))
        fb.addRow(tr("dlg.import.tolerance"), self.sp_tol)
        self.cb_scope = QComboBox()
        for key in (iu.SCOPE_EDGE, iu.SCOPE_ALL):
            self.cb_scope.addItem(tr(f"dlg.import.scope_{key}"), key)
        fb.addRow(tr("dlg.import.scope"), self.cb_scope)
        right.addWidget(g_bg)

        self.lb_result = QLabel()
        self.lb_result.setWordWrap(True)
        right.addWidget(self.lb_result)
        right.addStretch(1)

        self.chk_skip = QCheckBox(tr("dlg.import.skip_ready"))
        self.chk_skip.setChecked(bool(config.get("import.skip_when_ready", True)))
        right.addWidget(self.chk_skip)

        buttons = QHBoxLayout()
        self.btn_apply = QPushButton(tr("dlg.import.apply"))
        self.btn_apply.setDefault(True)
        self.btn_raw = QPushButton(tr("dlg.import.open_raw"))
        self.btn_cancel = QPushButton(tr("dlg.import.cancel"))
        buttons.addWidget(self.btn_apply, 2)
        buttons.addWidget(self.btn_raw, 1)
        buttons.addWidget(self.btn_cancel, 1)
        right.addLayout(buttons)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(120)
        self._timer.timeout.connect(self._refresh_preview)

        # ---------------- 초기값 (시그널 연결 전) ------------------------------
        self._init_values()

        self.cb_preset.currentIndexChanged.connect(self._on_preset_changed)
        self.rb_auto.toggled.connect(self._on_mode_changed)
        self.sp_w.valueChanged.connect(self._on_w_changed)
        self.sp_h.valueChanged.connect(self._on_h_changed)
        self.btn_fit.clicked.connect(self._fit_preset)
        self.btn_orig.clicked.connect(lambda: self._set_size(self.src_w, self.src_h))
        self.btn_half.clicked.connect(lambda: self._set_size(max(1, self.src_w // 2), max(1, self.src_h // 2)))
        for w in (self.cb_resample, self.cb_align, self.cb_scope):
            w.currentIndexChanged.connect(self._schedule)
        self.chk_canvas.toggled.connect(self._schedule)
        self.sp_tol.valueChanged.connect(self._schedule)
        self.bg_group.buttonToggled.connect(lambda *_: self._schedule())
        self.btn_apply.clicked.connect(self._accept_apply)
        self.btn_raw.clicked.connect(self._accept_raw)
        self.btn_cancel.clicked.connect(self.reject)

        self._update_color_button()
        self._update_size_labels()
        self._refresh_preview()

    # ------------------------------------------------------------------ #
    def _init_values(self) -> None:
        cfg = self.config
        # 규격: 크기가 딱 맞는 규격 → 마지막에 고른 규격 → 비율이 가장 비슷한 규격
        exact = find_matching_presets(self.presets, self.src_w, self.src_h)
        pid = exact[0]["id"] if exact else str(cfg.get("import.last_preset", "") or "")
        idx = self.cb_preset.findData(pid) if pid else -1
        if idx < 0:
            ratio = self.src_w / self.src_h
            best = min(self.presets, key=lambda p: abs(preset_spec(p).sheet_w / preset_spec(p).sheet_h - ratio),
                       default=None)
            idx = self.cb_preset.findData(best["id"]) if best else self.cb_preset.count() - 1
        self.cb_preset.setCurrentIndex(max(0, idx))

        (self.rb_custom if cfg.get("import.size_mode", "auto") == "custom" else self.rb_auto).setChecked(True)
        self.cb_resample.setCurrentIndex(max(0, self.cb_resample.findData(cfg.get("import.resample", "nearest"))))
        self.cb_scope.setCurrentIndex(max(0, self.cb_scope.findData(cfg.get("import.scope", "edge"))))
        self.cb_align.setCurrentIndex(max(0, self.cb_align.findData(cfg.get("import.align", "center"))))
        self.chk_canvas.setChecked(bool(cfg.get("import.fit_canvas", True)))

        # 배경: 투명 있음 → 그대로 / 단색 → 자동 / 단색 아님 → 지정 색 (대표색을 미리 골라 둠)
        kind = self.info.kind
        if kind == iu.BG_TRANSPARENT:
            self.rb_keep.setChecked(True)
            self.rb_bg_auto.setEnabled(False)
        elif kind == iu.BG_SINGLE:
            self.rb_bg_auto.setChecked(True)
        else:
            self.rb_pick.setChecked(True)

        self._fit_preset()
        self._update_src_info()

    def _update_src_info(self) -> None:
        kind = self.info.kind
        if kind == iu.BG_TRANSPARENT:
            bg = tr("dlg.import.bg_info_transparent")
        elif kind == iu.BG_SINGLE:
            bg = tr("dlg.import.bg_info_single", color=iu.color_hex(self.info.color))
        else:
            bg = tr("dlg.import.bg_info_multi", color=iu.color_hex(self.info.color),
                    pct=round(self.info.ratio * 100))
        self.lb_src_info.setText(tr("dlg.import.src_info", w=self.src_w, h=self.src_h, bg=bg))

    # -- 크기 -------------------------------------------------------------
    def _preset(self) -> dict | None:
        pid = self.cb_preset.currentData()
        return next((p for p in self.presets if p["id"] == pid), None) if pid else None

    def _auto_mode(self) -> bool:
        return self.rb_auto.isChecked()

    def _set_size(self, w: int, h: int) -> None:
        self._busy = True
        self.sp_w.setValue(int(w))
        self.sp_h.setValue(int(h))
        self._busy = False
        if self._auto_mode():  # 비율 유지 모드에서 직접 넣은 값도 비율에 맞춘다
            self._busy = True
            self.sp_h.setValue(iu.keep_ratio_height(self.src_w, self.src_h, self.sp_w.value()))
            self._busy = False
        self._update_size_labels()
        self._schedule()

    def _fit_preset(self) -> None:
        p = self._preset()
        if p is None:
            self._set_size(self.src_w, self.src_h)
            return
        spec = preset_spec(p)
        if self._auto_mode():
            w, h = iu.fit_inside(self.src_w, self.src_h, spec.sheet_w, spec.sheet_h)
            self._busy = True
            self.sp_w.setValue(w)
            self.sp_h.setValue(h)
            self._busy = False
            self._update_size_labels()
            self._schedule()
        else:
            self._set_size(spec.sheet_w, spec.sheet_h)

    def _on_preset_changed(self, _i: int) -> None:
        self._fit_preset()

    def _on_mode_changed(self, _checked: bool) -> None:
        if self._auto_mode():
            self._on_w_changed(self.sp_w.value())
        self._update_size_labels()
        self._schedule()

    def _on_w_changed(self, w: int) -> None:
        if self._busy:
            return
        if self._auto_mode():
            self._busy = True
            self.sp_h.setValue(iu.keep_ratio_height(self.src_w, self.src_h, w))
            self._busy = False
        self._update_size_labels()
        self._schedule()

    def _on_h_changed(self, h: int) -> None:
        if self._busy:
            return
        if self._auto_mode():
            self._busy = True
            self.sp_w.setValue(iu.keep_ratio_width(self.src_w, self.src_h, h))
            self._busy = False
        self._update_size_labels()
        self._schedule()

    def _update_size_labels(self) -> None:
        self.lb_link.setText("🔗" if self._auto_mode() else "×")
        self.lb_link.setToolTip(tr("dlg.import.link_tip") if self._auto_mode() else "")
        w, h = self.sp_w.value(), self.sp_h.value()
        self.lb_scale.setText(tr("dlg.import.scale", sx=round(w / self.src_w * 100, 1),
                                 sy=round(h / self.src_h * 100, 1)))
        p = self._preset()
        if p is not None:
            spec = preset_spec(p)
            self.chk_canvas.setText(tr("dlg.import.fit_canvas", w=spec.sheet_w, h=spec.sheet_h))
        has_canvas = p is not None
        self.chk_canvas.setEnabled(has_canvas)
        self.cb_align.setEnabled(has_canvas and self.chk_canvas.isChecked())
        self.btn_fit.setEnabled(has_canvas)

    # -- 배경 -------------------------------------------------------------
    def _bg_mode(self) -> str:
        if self.rb_bg_auto.isChecked():
            return BG_AUTO
        if self.rb_pick.isChecked():
            return BG_PICK
        return BG_KEEP

    def _bg_colors(self) -> list[tuple[int, int, int]]:
        mode = self._bg_mode()
        if mode == BG_AUTO:
            return [self.info.color]
        if mode == BG_PICK:
            return [self.pick_color]
        return []

    def _update_color_button(self) -> None:
        c = self.pick_color
        text_color = "#000000" if (c[0] * 299 + c[1] * 587 + c[2] * 114) / 1000 > 140 else "#ffffff"
        self.btn_color.setText(iu.color_hex(c))
        self.btn_color.setStyleSheet(f"background-color: {iu.color_hex(c)}; color: {text_color};")

    def _choose_color(self) -> None:
        col = QColorDialog.getColor(QColor(*self.pick_color), self, tr("dlg.import.bg_color"))
        if col.isValid():
            self.pick_color = (col.red(), col.green(), col.blue())
            self._update_color_button()
            self.rb_pick.setChecked(True)
            self._schedule()

    def _on_pick_pixel(self, x: int, y: int) -> None:
        px = self.src[min(y, self.src_h - 1), min(x, self.src_w - 1)]
        if px[3] == 0:
            return
        self.pick_color = (int(px[0]), int(px[1]), int(px[2]))
        self._update_color_button()
        self.rb_pick.setChecked(True)
        self._schedule()

    # -- 미리보기 / 결과 --------------------------------------------------
    def _schedule(self, *_args) -> None:
        self._update_size_labels()
        self._timer.start()

    def options(self) -> iu.ImportOptions:
        p = self._preset()
        canvas = None
        if p is not None and self.chk_canvas.isChecked():
            spec = preset_spec(p)
            canvas = (spec.sheet_w, spec.sheet_h)
        w, h = self.sp_w.value(), self.sp_h.value()
        same = (w, h) == (self.src_w, self.src_h)
        return iu.ImportOptions(
            bg_colors=self._bg_colors(), tolerance=self.sp_tol.value(), scope=self.cb_scope.currentData(),
            width=0 if same else w, height=0 if same else h, resample=self.cb_resample.currentData(),
            canvas=canvas, align=self.cb_align.currentData())

    def _compute(self) -> np.ndarray:
        opt = self.options()
        key = (tuple(opt.bg_colors), opt.tolerance, opt.scope)
        if key != self._bg_cache_key:  # 배경 처리는 원본 해상도라 무겁다 — 설정이 같으면 재사용
            self._bg_cache = (iu.remove_background(self.src, opt.bg_colors, opt.tolerance, opt.scope)
                              if opt.bg_colors else self.src)
            self._bg_cache_key = key
        opt.bg_colors = []
        return iu.process(self._bg_cache, opt)

    def _refresh_preview(self) -> None:
        out = self._compute()
        self.view_dst.set_array(out)
        h, w = out.shape[:2]
        p = self._preset()
        if p is not None and (w, h) == (preset_spec(p).sheet_w, preset_spec(p).sheet_h):
            spec = preset_spec(p)
            text = tr("dlg.import.result_ok", w=w, h=h, cols=spec.cols, rows=spec.rows,
                      cw=spec.cell_w, ch=spec.cell_h)
            self.lb_result.setObjectName("")
        elif p is not None:
            spec = preset_spec(p)
            text = tr("dlg.import.result_mismatch", w=w, h=h, sw=spec.sheet_w, sh=spec.sheet_h)
            self.lb_result.setObjectName("WarnLabel")
        else:
            text = tr("dlg.import.result_free", w=w, h=h)
            self.lb_result.setObjectName("WarnLabel")
        self.lb_result.setText(text)
        self.lb_result.style().unpolish(self.lb_result)
        self.lb_result.style().polish(self.lb_result)

    def _save_prefs(self) -> None:
        cfg = self.config
        cfg.set("import.last_preset", self.cb_preset.currentData() or "")
        cfg.set("import.size_mode", "auto" if self._auto_mode() else "custom")
        cfg.set("import.resample", self.cb_resample.currentData())
        cfg.set("import.tolerance", self.sp_tol.value())
        cfg.set("import.scope", self.cb_scope.currentData())
        cfg.set("import.fit_canvas", self.chk_canvas.isChecked())
        cfg.set("import.align", self.cb_align.currentData())
        cfg.set("import.skip_when_ready", self.chk_skip.isChecked())

    def _accept_apply(self) -> None:
        self._timer.stop()
        self.result_arr = self._compute()
        p = self._preset()
        h, w = self.result_arr.shape[:2]
        if p is not None and (w, h) == (preset_spec(p).sheet_w, preset_spec(p).sheet_h):
            self.result_preset = p
        self._save_prefs()
        self.accept()

    def _accept_raw(self) -> None:
        self.open_raw = True
        self.config.set("import.skip_when_ready", self.chk_skip.isChecked())
        self.accept()
