"""
main_window.py
-----------------
메인 윈도우 조립 (기획서 §5): 제목줄 / 메뉴바 / 툴바 / 좌측 도구·클립보드 / 캔버스 / 우측 배경 설정 /
하단 상태바. 문서 상태(SheetModel) · 클립보드 · 열기/저장 · 자동 저장/복구 흐름을 여기서 묶는다.

셀 편집 자체는 cell_ops(순수 함수) + history(Undo/Redo)가 하고, 이 파일은 UI 흐름만 맡는다.
모든 UI 문자열은 i18n.tr() 키로 부르고, 문구 setter는 Translatable._t()로 등록해
언어를 바꾸면 즉시 다시 번역된다.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass

import numpy as np
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction, QActionGroup, QImage, QKeySequence, QPixmap
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QFileDialog, QGroupBox, QHBoxLayout, QLabel, QMainWindow,
    QMenu, QMessageBox, QPushButton, QSizePolicy, QSlider, QSpinBox, QSplitter, QStatusBar,
    QToolBar, QToolButton, QVBoxLayout, QWidget,
)

import cell_ops
import export_utils
import reference_layers
from about_dialog import AboutDialog
from app_info import APP_NAME, APP_VERSION
from autosave import AutoSaver, Snapshot
from canvas_widget import (
    BACKGROUND_MODES, BG_BODY, BG_CHECKER, ZOOM_STEPS, CanvasWidget,
)
from config_manager import ConfigManager
from help_dialog import KIND_CHANGELOG, KIND_HELP, HelpDialog
from i18n import Translatable, set_language, tr
from preset_dialog import SheetSpecDialog, grid_text, preset_list_label
from save_dialog import SaveDialog
from settings_dialog import SettingsDialog
from sheet_model import (
    GENDER_COMMON, GENDER_FEMALE, GENDER_MALE, CellRect, GridSpec, SheetModel, all_presets,
    detect_gender, make_user_preset, pick_preset_for_file, preset_spec, rect_label,
)
from theme import get_stylesheet
from ui_utils import open_folder

IMAGE_FILTER_KEY = "dlg.open.filter"
MAX_RECENT_MENU = 10


@dataclass
class ClipboardData:
    """내부 클립보드: 복사한 셀 블록 + 어느 체형/셀 크기에서 왔는지."""

    block: np.ndarray
    shape: tuple[int, int]  # (행 수, 열 수)
    cell_w: int
    cell_h: int
    gender: str


class MainWindow(QMainWindow, Translatable):
    def __init__(self, config: ConfigManager | None = None, autosaver: AutoSaver | None = None):
        super().__init__()
        self.config = config or ConfigManager()
        set_language(self.config.get("language", "ko"))

        self.model: SheetModel | None = None
        self.clipboard: ClipboardData | None = None
        self.body_override: str = ""  # 직접 지정한 Base Body PNG
        self._body_cache: dict[tuple[str, float], QImage] = {}
        self._body_qimage: QImage | None = None
        self._last_msg = ("status.ready", {}, "info")
        self._last_autosave_marker = None

        width, height = self.config.get_window_size()
        self.resize(width, height)
        self.setAcceptDrops(True)

        self.autosaver = autosaver or AutoSaver()
        self._recovery_candidates: list[Snapshot] = self.autosaver.start_session()

        self._build_actions()
        self._build_menu()
        self._build_toolbar()
        self._build_status_bar()  # 캔버스 시그널(줌 변경 등)이 상태바 라벨을 쓰므로 먼저 만든다
        self._build_central_widget()
        self._apply_theme(self.config.get("app_theme", "dark"))
        self._apply_view_settings()

        self.autosave_timer = QTimer(self)
        self.autosave_timer.timeout.connect(self._autosave_tick)
        self._restart_autosave_timer()

        self.retranslate_ui()
        QTimer.singleShot(0, self._offer_recovery)

    # ================================================================== #
    #  UI 구성                                                            #
    # ================================================================== #
    def _action(self, key: str, slot, shortcut=None, checkable: bool = False) -> QAction:
        act = QAction(self)
        self._t(act.setText, key)
        act.setCheckable(checkable)
        if shortcut:
            if isinstance(shortcut, (list, tuple)):
                act.setShortcuts([QKeySequence(s) for s in shortcut])
            else:
                act.setShortcut(QKeySequence(shortcut))
        if checkable:
            act.toggled.connect(slot)
        else:
            act.triggered.connect(lambda _checked=False: slot())
        return act

    def _build_actions(self) -> None:
        a = self._action
        self.a_open = a("act.open", self.open_dialog, "Ctrl+O")
        self.a_save = a("act.save", self.save, "Ctrl+S")
        self.a_save_as = a("act.save_as", lambda: self.save(force_dialog=True), "Ctrl+Shift+S")
        self.a_exit = a("act.exit", self.close, "Ctrl+Q")

        self.a_undo = a("act.undo", self.undo, "Ctrl+Z")
        self.a_redo = a("act.redo", self.redo, ["Ctrl+Y", "Ctrl+Shift+Z"])
        self.a_copy = a("act.copy", self.copy_cells, "Ctrl+C")
        self.a_cut = a("act.cut", self.cut_cells, "Ctrl+X")
        self.a_paste = a("act.paste", lambda: self.paste_cells(), "Ctrl+V")
        self.a_paste_mirror = a("act.paste_mirror", lambda: self.paste_cells(mirror=True), "M")
        self.a_paste_flipv = a("act.paste_flipv", lambda: self.paste_cells(flip_v=True))
        self.a_mirror_apply = a("act.mirror_apply", self.mirror_apply, "Ctrl+M")
        self.a_clear = a("act.clear", self.clear_cells, "Delete")

        self.a_grid = a("act.grid", self._on_grid_toggled, "G", checkable=True)
        self.a_grid.blockSignals(True)  # 캔버스가 아직 없으므로 초기값 설정 시 슬롯 호출 방지
        self.a_grid.setChecked(bool(self.config.get("grid.show", True)))
        self.a_grid.blockSignals(False)
        self.a_zoom_in = a("act.zoom_in", lambda: self.canvas.zoom_step(1), ["Ctrl+=", "Ctrl++"])
        self.a_zoom_out = a("act.zoom_out", lambda: self.canvas.zoom_step(-1), "Ctrl+-")
        self.a_bg_cycle = a("act.bg_cycle", self._cycle_background, "B")
        self.bg_actions: dict[str, QAction] = {}
        self.bg_group = QActionGroup(self)
        self.bg_group.setExclusive(True)
        for mode in BACKGROUND_MODES:
            act = a(f"bg.{mode}", lambda mode=mode: self._set_background(mode))
            act.setCheckable(True)
            self.bg_group.addAction(act)
            self.bg_actions[mode] = act

        self.a_open_save_dir = a("act.open_save_dir", self._open_save_dir)
        self.a_pick_body = a("act.pick_body", self._pick_body)
        self.a_spec_custom = a("act.spec_custom", self._custom_spec_dialog)
        self.a_settings = a("act.settings", self.open_settings, "Ctrl+,")
        self.a_help = a("act.help", lambda: self._show_help(KIND_HELP), "F1")
        self.a_changelog = a("act.changelog", lambda: self._show_help(KIND_CHANGELOG))
        self.a_about = a("act.about", lambda: AboutDialog(self).exec())

    def _build_menu(self) -> None:
        mb = self.menuBar()
        self.m_file = mb.addMenu("")
        self._t(self.m_file.setTitle, "menu.file")
        self.m_file.addAction(self.a_open)
        self.m_file.addAction(self.a_save)
        self.m_file.addAction(self.a_save_as)
        self.m_file.addSeparator()
        self.m_recent = self.m_file.addMenu("")
        self._t(self.m_recent.setTitle, "act.recent")
        self.m_recent.aboutToShow.connect(self._fill_recent_menu)
        self.m_file.addSeparator()
        self.m_file.addAction(self.a_exit)

        self.m_edit = mb.addMenu("")
        self._t(self.m_edit.setTitle, "menu.edit")
        for act in (self.a_undo, self.a_redo, None, self.a_copy, self.a_cut, self.a_paste,
                    self.a_paste_mirror, self.a_paste_flipv, self.a_mirror_apply, None, self.a_clear):
            self.m_edit.addSeparator() if act is None else self.m_edit.addAction(act)

        self.m_view = mb.addMenu("")
        self._t(self.m_view.setTitle, "menu.view")
        self.m_view.addAction(self.a_grid)
        self.m_view.addAction(self.a_zoom_in)
        self.m_view.addAction(self.a_zoom_out)
        self.m_view.addSeparator()
        self.m_bg = self.m_view.addMenu("")
        self._t(self.m_bg.setTitle, "act.bg_menu")
        for mode in BACKGROUND_MODES:
            self.m_bg.addAction(self.bg_actions[mode])
        self.m_bg.addSeparator()
        self.m_bg.addAction(self.a_bg_cycle)

        self.m_tools = mb.addMenu("")
        self._t(self.m_tools.setTitle, "menu.tools")
        self.m_tools.addAction(self.a_spec_custom)
        self.m_tools.addAction(self.a_pick_body)
        self.m_tools.addAction(self.a_open_save_dir)

        self.m_settings = mb.addMenu("")
        self._t(self.m_settings.setTitle, "menu.settings")
        self.m_settings.addAction(self.a_settings)

        self.m_help = mb.addMenu("")
        self._t(self.m_help.setTitle, "menu.help")
        self.m_help.addAction(self.a_help)
        self.m_help.addAction(self.a_changelog)
        self.m_help.addSeparator()
        self.m_help.addAction(self.a_about)

    def _build_toolbar(self) -> None:
        tb = QToolBar()
        tb.setMovable(False)
        tb.setFloatable(False)
        self.addToolBar(tb)
        self.toolbar = tb

        for act in (self.a_open, self.a_save):
            btn = QToolButton()
            btn.setDefaultAction(act)
            btn.setToolButtonStyle(Qt.ToolButtonTextOnly)
            tb.addWidget(btn)
        tb.addSeparator()

        self.lb_preset = QLabel()
        self._t(self.lb_preset.setText, "tb.preset")
        tb.addWidget(self.lb_preset)
        self.cb_preset = QComboBox()
        self.cb_preset.setSizeAdjustPolicy(QComboBox.AdjustToContents)
        self.cb_preset.activated.connect(self._on_preset_activated)
        tb.addWidget(self.cb_preset)

        self.lb_grid = QLabel()
        self.lb_grid.setObjectName("DimLabel")
        tb.addWidget(self.lb_grid)
        tb.addSeparator()

        self.lb_gender = QLabel()
        self._t(self.lb_gender.setText, "tb.gender")
        tb.addWidget(self.lb_gender)
        self.cb_gender = QComboBox()
        self.cb_gender.activated.connect(self._on_gender_activated)
        tb.addWidget(self.cb_gender)

        self.lb_skin = QLabel()
        self._t(self.lb_skin.setText, "tb.skin")
        tb.addWidget(self.lb_skin)
        self.cb_skin = QComboBox()
        self.cb_skin.addItems(reference_layers.SKIN_TONES)
        self.cb_skin.activated.connect(self._on_skin_activated)
        tb.addWidget(self.cb_skin)
        tb.addSeparator()

        self.lb_zoom = QLabel()
        self._t(self.lb_zoom.setText, "tb.zoom")
        tb.addWidget(self.lb_zoom)
        self.cb_zoom = QComboBox()
        for z in ZOOM_STEPS:
            self.cb_zoom.addItem(f"{z}%", userData=z)
        self.cb_zoom.activated.connect(self._on_zoom_activated)
        tb.addWidget(self.cb_zoom)

    def _build_central_widget(self) -> None:
        self.canvas = CanvasWidget()
        self.canvas.selectionChanged.connect(self._on_selection_changed)
        self.canvas.zoomChanged.connect(self._on_canvas_zoom)
        self.canvas.hoverPixel.connect(self._on_hover_pixel)
        self.canvas.contextMenuRequested.connect(self._show_context_menu)
        self.canvas.filesDropped.connect(self._on_files_dropped)
        self.canvas.set_zoom(int(self.config.get("zoom_percent", 200)))
        self.cb_zoom.setCurrentIndex(max(0, self.cb_zoom.findData(self.canvas.zoom())))

        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(self._build_left_panel())
        splitter.addWidget(self.canvas)
        splitter.addWidget(self._build_right_panel())
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setStretchFactor(2, 0)
        splitter.setSizes([200, 1000, 250])
        splitter.setCollapsible(1, False)
        self.setCentralWidget(splitter)

    def _group(self, key: str) -> tuple[QGroupBox, QVBoxLayout]:
        gb = QGroupBox()
        self._t(gb.setTitle, key)
        lay = QVBoxLayout(gb)
        lay.setSpacing(4)
        return gb, lay

    def _build_left_panel(self) -> QWidget:
        panel = QWidget()
        panel.setMinimumWidth(180)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(6, 6, 6, 6)

        gb_tools, lay = self._group("panel.tools")
        hint = QLabel()
        hint.setWordWrap(True)
        hint.setObjectName("DimLabel")
        self._t(hint.setText, "panel.hint")
        lay.addWidget(hint)
        for act in (self.a_copy, self.a_cut, self.a_paste, self.a_paste_mirror,
                    self.a_mirror_apply, self.a_clear):
            btn = QToolButton()
            btn.setDefaultAction(act)
            btn.setToolButtonStyle(Qt.ToolButtonTextOnly)
            btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            lay.addWidget(btn)
        undo_row = QHBoxLayout()
        # 메뉴의 실행 취소/다시 실행 문구에는 작업 이름이 붙어 길어지므로, 패널 버튼은 짧은 고정 문구를 쓴다
        self.btn_undo, self.btn_redo = QPushButton(), QPushButton()
        self._t(self.btn_undo.setText, "btn.undo")
        self._t(self.btn_redo.setText, "btn.redo")
        self.btn_undo.clicked.connect(self.undo)
        self.btn_redo.clicked.connect(self.redo)
        undo_row.addWidget(self.btn_undo)
        undo_row.addWidget(self.btn_redo)
        lay.addLayout(undo_row)
        layout.addWidget(gb_tools)

        gb_export, lay = self._group("panel.export")
        self.cb_alpha = QCheckBox()
        self._t(self.cb_alpha.setText, "panel.alpha")
        self.cb_alpha.toggled.connect(self._on_alpha_panel_enabled)
        self.sp_alpha = QSpinBox()
        self.sp_alpha.setRange(1, 255)
        self.sp_alpha.valueChanged.connect(self._on_alpha_panel_value)
        row = QHBoxLayout()
        row.addWidget(self.cb_alpha)
        row.addWidget(self.sp_alpha)
        lay.addLayout(row)
        lb = QLabel()
        self._t(lb.setText, "panel.save_dir")
        lay.addWidget(lb)
        self.cb_save_dir = QComboBox()
        self.cb_save_dir.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.cb_save_dir.setMinimumContentsLength(12)
        self.cb_save_dir.activated.connect(self._on_save_dir_activated)
        lay.addWidget(self.cb_save_dir)
        btn_open = QPushButton()
        self._t(btn_open.setText, "btn.open_folder")
        btn_open.clicked.connect(self._open_save_dir)
        lay.addWidget(btn_open)
        layout.addWidget(gb_export)

        layout.addStretch(1)
        return panel

    def _build_right_panel(self) -> QWidget:
        panel = QWidget()
        panel.setMinimumWidth(200)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(6, 6, 6, 6)

        gb, lay = self._group("panel.background")
        self.cb_bg = QComboBox()
        self.cb_bg.activated.connect(self._on_bg_activated)
        lay.addWidget(self.cb_bg)

        self.lb_opacity = QLabel()
        self._t(self.lb_opacity.setText, "panel.opacity")
        lay.addWidget(self.lb_opacity)
        row = QHBoxLayout()
        self.sl_opacity = QSlider(Qt.Horizontal)
        self.sl_opacity.setRange(0, 100)
        self.sl_opacity.setValue(int(self.config.get("body_opacity", 60)))
        self.sl_opacity.valueChanged.connect(self._on_opacity_changed)
        self.lb_opacity_val = QLabel(f"{self.sl_opacity.value()}%")
        row.addWidget(self.sl_opacity, stretch=1)
        row.addWidget(self.lb_opacity_val)
        lay.addLayout(row)

        self.cb_show_grid = QCheckBox()
        self._t(self.cb_show_grid.setText, "act.grid")
        self.cb_show_grid.setChecked(self.a_grid.isChecked())
        self.cb_show_grid.toggled.connect(self.a_grid.setChecked)
        self.a_grid.toggled.connect(self.cb_show_grid.setChecked)
        lay.addWidget(self.cb_show_grid)
        layout.addWidget(gb)

        gb2, lay2 = self._group("panel.body")
        self.lb_body_name = QLabel()
        self.lb_body_name.setWordWrap(True)
        lay2.addWidget(self.lb_body_name)
        self.lb_body_warn = QLabel()
        self.lb_body_warn.setWordWrap(True)
        self.lb_body_warn.setObjectName("WarnLabel")
        lay2.addWidget(self.lb_body_warn)
        btn_row = QHBoxLayout()
        btn_pick = QPushButton()
        self._t(btn_pick.setText, "panel.body_pick")
        btn_pick.clicked.connect(self._pick_body)
        btn_auto = QPushButton()
        self._t(btn_auto.setText, "panel.body_auto")
        btn_auto.clicked.connect(self._body_auto)
        btn_row.addWidget(btn_pick)
        btn_row.addWidget(btn_auto)
        lay2.addLayout(btn_row)
        self.lb_body_thumb = QLabel()
        self.lb_body_thumb.setAlignment(Qt.AlignCenter)
        self.lb_body_thumb.setMinimumHeight(170)
        self.lb_body_thumb.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        lay2.addWidget(self.lb_body_thumb, stretch=1)
        layout.addWidget(gb2, stretch=1)
        return panel

    def _build_status_bar(self) -> None:
        sb = QStatusBar()
        self.setStatusBar(sb)
        self.lb_sel = QLabel()
        self.lb_msg = QLabel()
        self.lb_pixel = QLabel()
        self.lb_zoom_status = QLabel()
        self.lb_size = QLabel()
        self.lb_saved = QLabel()
        for lb in (self.lb_sel, self.lb_pixel, self.lb_zoom_status, self.lb_size, self.lb_saved):
            lb.setObjectName("StatusBarInfo")
        sb.addWidget(self.lb_sel)
        sb.addWidget(self.lb_msg, 1)
        for lb in (self.lb_pixel, self.lb_zoom_status, self.lb_size, self.lb_saved):
            lb.setContentsMargins(8, 0, 8, 0)
            sb.addPermanentWidget(lb)

    # ================================================================== #
    #  번역 / 표시 갱신                                                    #
    # ================================================================== #
    def retranslate_ui(self) -> None:
        self.retranslate_registered()
        self.canvas.set_placeholder(tr("canvas.placeholder"))
        self._fill_gender_combo()
        self._fill_bg_combo()
        self._fill_preset_combo()
        self._fill_save_dir_combo()
        self._sync_panel_from_config()
        self._refresh_body()
        self._update_all()

    def _update_all(self) -> None:
        self._update_title()
        self._update_actions()
        self._update_selection_status()
        self._update_grid_label()
        self._update_status_labels()
        self._show_message(*self._last_msg)

    def _update_title(self) -> None:
        title = f"{APP_NAME} v{APP_VERSION}"
        if self.model is not None:
            title += f" - {self.model.display_name}{' *' if self.model.dirty else ''}"
        self.setWindowTitle(title)

    def _update_actions(self) -> None:
        m = self.model
        has = m is not None
        src = self.canvas.source() if has else None
        has_src = src is not None
        has_clip = self.clipboard is not None
        h = m.history if has else None

        def label(base_key: str, edit_label: str | None) -> str:
            # QAction 텍스트에서 '&'는 단축 문자 표시이므로 '&&'로 이스케이프
            return tr(base_key) + (f" ({tr(edit_label).replace('&', '&&')})" if edit_label else "")

        self.a_undo.setText(label("act.undo", h.undo_label() if h else None))
        self.a_redo.setText(label("act.redo", h.redo_label() if h else None))
        self.a_undo.setEnabled(bool(h and h.can_undo))
        self.a_redo.setEnabled(bool(h and h.can_redo))
        self.btn_undo.setEnabled(self.a_undo.isEnabled())
        self.btn_redo.setEnabled(self.a_redo.isEnabled())
        self.btn_undo.setToolTip(self.a_undo.text().replace("&&", "&"))
        self.btn_redo.setToolTip(self.a_redo.text().replace("&&", "&"))
        self.a_save.setEnabled(has)
        self.a_save_as.setEnabled(has)
        self.a_copy.setEnabled(has_src)
        self.a_cut.setEnabled(has_src)
        self.a_clear.setEnabled(has_src)
        self.a_mirror_apply.setEnabled(has_src)
        for act in (self.a_paste, self.a_paste_mirror, self.a_paste_flipv):
            act.setEnabled(has and has_clip)
        self.a_spec_custom.setEnabled(has)
        self.cb_preset.setEnabled(has)
        self.cb_gender.setEnabled(has)
        self.cb_skin.setEnabled(has)

    def _update_grid_label(self) -> None:
        m = self.model
        if m is None:
            self.lb_grid.setText("")
            return
        text = grid_text(m.grid)
        if not m.grid_matches_image():
            text += "  ⚠ " + tr("status.grid_image_mismatch", w=m.width, h=m.height)
            self.lb_grid.setObjectName("WarnLabel")
        else:
            self.lb_grid.setObjectName("DimLabel")
        self.lb_grid.setText(text)
        self.lb_grid.style().unpolish(self.lb_grid)
        self.lb_grid.style().polish(self.lb_grid)

    def _update_selection_status(self) -> None:
        if self.model is None:
            self.lb_sel.setText("")
            return
        s, t = self.canvas.source(), self.canvas.target()
        text = tr("status.source") + " " + (rect_label(s) if s else "-")
        if t:
            text += "  →  " + tr("status.target") + " " + rect_label(t)
        self.lb_sel.setText(text)

    def _update_status_labels(self) -> None:
        self.lb_zoom_status.setText(tr("status.zoom", zoom=self.canvas.zoom()))
        m = self.model
        self.lb_size.setText(tr("status.size", w=m.width, h=m.height) if m else "")
        if m is None:
            self.lb_saved.setText("")
        else:
            self.lb_saved.setText(tr("status.modified") if m.dirty else tr("status.saved"))

    def _show_message(self, key: str, fmt: dict | None = None, level: str = "info") -> None:
        """상태바 가운데 문구. level: info | warn | error."""
        self._last_msg = (key, fmt or {}, level)
        self.lb_msg.setText(tr(key, **(fmt or {})))
        self.lb_msg.setObjectName({"warn": "WarnLabel", "error": "ErrorLabel"}.get(level, "StatusBarInfo"))
        self.lb_msg.style().unpolish(self.lb_msg)
        self.lb_msg.style().polish(self.lb_msg)

    # -- 콤보 채우기 -----------------------------------------------------------
    def _fill_gender_combo(self) -> None:
        cur = self.model.gender if self.model else GENDER_COMMON
        self.cb_gender.clear()
        for g, key in ((GENDER_FEMALE, "gender.F"), (GENDER_MALE, "gender.M"), (GENDER_COMMON, "gender.C")):
            self.cb_gender.addItem(tr(key), userData=g)
        self.cb_gender.setCurrentIndex(max(0, self.cb_gender.findData(cur)))

    def _fill_bg_combo(self) -> None:
        cur = self.canvas.background()
        self.cb_bg.clear()
        for mode in BACKGROUND_MODES:
            self.cb_bg.addItem(tr(f"bg.{mode}"), userData=mode)
        self.cb_bg.setCurrentIndex(max(0, self.cb_bg.findData(cur)))
        self.bg_actions[cur].setChecked(True)
        self.sl_opacity.setEnabled(cur == BG_BODY)
        self.lb_opacity.setEnabled(cur == BG_BODY)

    def _fill_preset_combo(self) -> None:
        presets = all_presets(self.config.get("sheet_presets", []))
        cur_id = self.model.preset_id if self.model else ""
        self.cb_preset.blockSignals(True)
        self.cb_preset.clear()
        for p in presets:
            self.cb_preset.addItem(preset_list_label(p), userData=p["id"])
        if self.model is not None and cur_id == "custom":
            self.cb_preset.addItem(tr("preset.custom_current", grid=grid_text(self.model.grid)),
                                   userData="custom")
        self.cb_preset.addItem(tr("preset.custom_action"), userData="__custom__")
        idx = self.cb_preset.findData(cur_id) if cur_id else -1
        self.cb_preset.setCurrentIndex(idx if idx >= 0 else -1)
        self.cb_preset.blockSignals(False)

    def _fill_save_dir_combo(self) -> None:
        cur = self.config.get("save_dir", "")
        self.cb_save_dir.blockSignals(True)
        self.cb_save_dir.clear()
        self.cb_save_dir.addItem(tr("panel.save_dir_default"), userData="")
        for d in self.config.get("recent_save_dirs", []):
            self.cb_save_dir.addItem(d, userData=d)
        idx = self.cb_save_dir.findData(cur)
        if idx < 0 and cur:
            self.cb_save_dir.addItem(cur, userData=cur)
            idx = self.cb_save_dir.count() - 1
        self.cb_save_dir.setCurrentIndex(max(0, idx))
        self.cb_save_dir.blockSignals(False)

    def _sync_panel_from_config(self) -> None:
        for w in (self.cb_alpha, self.sp_alpha):
            w.blockSignals(True)
        self.cb_alpha.setChecked(bool(self.config.get("alpha_threshold_enabled", True)))
        self.sp_alpha.setValue(int(self.config.get("alpha_threshold", 128)))
        self.sp_alpha.setEnabled(self.cb_alpha.isChecked())
        for w in (self.cb_alpha, self.sp_alpha):
            w.blockSignals(False)

    # ================================================================== #
    #  테마 / 보기 설정                                                    #
    # ================================================================== #
    def _apply_theme(self, theme: str) -> None:
        app = QApplication.instance()
        if app is not None:
            app.setStyleSheet(get_stylesheet(theme))
        self.canvas.set_theme(theme)

    def _apply_view_settings(self) -> None:
        self.canvas.set_grid_style(self.config.get("grid.color", "#00e5ff"),
                                   int(self.config.get("grid.width", 1)))
        self.canvas.set_show_grid(self.a_grid.isChecked())
        mode = self.config.get("background_mode", BG_CHECKER)
        self.canvas.set_background(mode if mode in BACKGROUND_MODES else BG_CHECKER)
        self.canvas.set_body_opacity(self.sl_opacity.value())

    def _on_grid_toggled(self, checked: bool) -> None:
        self.canvas.set_show_grid(checked)
        self.config.set("grid.show", bool(checked))

    def _set_background(self, mode: str) -> None:
        self.canvas.set_background(mode)
        self.config.set("background_mode", mode)
        self._fill_bg_combo()
        if mode == BG_BODY and self._body_qimage is None:
            self._show_message("status.body_hint_pick", {}, "warn")

    def _cycle_background(self) -> None:
        modes = list(BACKGROUND_MODES)
        nxt = modes[(modes.index(self.canvas.background()) + 1) % len(modes)]
        self._set_background(nxt)

    def _on_bg_activated(self, _i: int) -> None:
        self._set_background(self.cb_bg.currentData())

    def _on_opacity_changed(self, value: int) -> None:
        self.lb_opacity_val.setText(f"{value}%")
        self.canvas.set_body_opacity(value)
        self.config.set("body_opacity", int(value))

    def _on_zoom_activated(self, _i: int) -> None:
        self.canvas.set_zoom(self.cb_zoom.currentData())

    def _on_canvas_zoom(self, zoom: int) -> None:
        self.cb_zoom.blockSignals(True)
        self.cb_zoom.setCurrentIndex(max(0, self.cb_zoom.findData(zoom)))
        self.cb_zoom.blockSignals(False)
        self.config.set("zoom_percent", int(zoom))
        self._update_status_labels()

    def _on_selection_changed(self) -> None:
        self._update_selection_status()
        self._update_actions()

    def _on_hover_pixel(self, x: int, y: int) -> None:
        if self.model is None or x < 0:
            self.lb_pixel.setText("")
            return
        r, g, b, a = (int(v) for v in self.model.arr[y, x])
        self.lb_pixel.setText(tr("status.pixel", x=x, y=y, r=r, g=g, b=b, a=a))

    def _show_context_menu(self, global_pos) -> None:
        if self.model is None:
            return
        menu = QMenu(self)
        for act in (self.a_copy, self.a_cut, self.a_paste, self.a_paste_mirror, self.a_paste_flipv,
                    None, self.a_mirror_apply, self.a_clear):
            menu.addSeparator() if act is None else menu.addAction(act)
        menu.exec(global_pos)

    # ================================================================== #
    #  시트 규격 / 체형 / 피부톤                                           #
    # ================================================================== #
    def _current_preset(self) -> dict | None:
        if self.model is None:
            return None
        for p in all_presets(self.config.get("sheet_presets", [])):
            if p["id"] == self.model.preset_id:
                return p
        return None

    def _apply_grid(self, spec: GridSpec, preset_id: str) -> None:
        if self.model is None:
            return
        self.model.grid = spec
        self.model.preset_id = preset_id
        self.canvas.grid_changed()
        self._fill_preset_combo()
        self._refresh_body()
        self._update_all()
        if not self.model.grid_matches_image():
            self._show_message("status.grid_image_mismatch_hint", {}, "warn")

    def _on_preset_activated(self, _i: int) -> None:
        data = self.cb_preset.currentData()
        if self.model is None:
            return
        if data == "__custom__":
            self._custom_spec_dialog()
            return
        if data == "custom":
            return
        for p in all_presets(self.config.get("sheet_presets", [])):
            if p["id"] == data:
                self._apply_grid(preset_spec(p), p["id"])
                return

    def _custom_spec_dialog(self) -> None:
        if self.model is None:
            return
        dlg = SheetSpecDialog(self, spec=self.model.grid)
        if dlg.exec() and dlg.result_spec():
            self._apply_dialog_spec(dlg)
        else:
            self._fill_preset_combo()

    def _apply_dialog_spec(self, dlg: SheetSpecDialog) -> None:
        spec = dlg.result_spec()
        pid = "custom"
        if dlg.save_as_preset():
            preset = make_user_preset(dlg.preset_name(), spec, dlg.body_motion())
            self.config.set("sheet_presets", list(self.config.get("sheet_presets", [])) + [preset])
            pid = preset["id"]
        self._apply_grid(spec, pid)

    def _on_gender_activated(self, _i: int) -> None:
        if self.model is None:
            return
        self.model.gender = self.cb_gender.currentData()
        self._refresh_body()
        self._update_all()

    def _on_skin_activated(self, _i: int) -> None:
        if self.model is None:
            return
        self.model.skin = self.cb_skin.currentText()
        self._refresh_body()

    # ================================================================== #
    #  Base Body 참조 이미지 (§4.1)                                        #
    # ================================================================== #
    def _load_body_qimage(self, path: str) -> QImage | None:
        try:
            key = (path, os.path.getmtime(path))
        except OSError:
            return None
        if key in self._body_cache:
            return self._body_cache[key]
        try:
            arr = export_utils.load_png_rgba(path)
        except Exception:  # 깨진 PNG 등 — 참조가 없는 것으로 취급
            return None
        h, w = arr.shape[:2]
        img = QImage(arr.data, w, h, w * 4, QImage.Format_RGBA8888).copy()
        self._body_cache = {key: img}  # 한 장만 캐시 (H2 Body는 7MB)
        return img

    def _refresh_body(self) -> None:
        self._body_qimage = None
        name_text, warn_text = tr("panel.body_none"), ""
        path: str | None = None
        m = self.model
        if m is not None:
            if self.body_override:
                path = self.body_override if os.path.isfile(self.body_override) else None
                if path is None:
                    warn_text = tr("status.body_missing", name=os.path.basename(self.body_override))
            else:
                preset = self._current_preset()
                motion = (preset or {}).get("body_motion", "")
                path, expected = reference_layers.find_body(
                    self.config.get("body_dir", ""), m.gender, m.skin, motion)
                if not motion:
                    warn_text = tr("status.body_no_motion")
                elif path is None:
                    warn_text = tr("status.body_missing", name=expected)
                elif reference_layers.uses_common_fallback(m.gender):
                    warn_text = tr("status.body_common_fallback")
            if path:
                img = self._load_body_qimage(path)
                if img is not None:
                    self._body_qimage = img
                    name_text = os.path.basename(path)
                    if (img.width(), img.height()) != (m.width, m.height):
                        warn_text = tr("status.body_size_mismatch", bw=img.width(), bh=img.height(),
                                       w=m.width, h=m.height)
                else:
                    warn_text = tr("status.body_load_failed", name=os.path.basename(path))
        self.lb_body_name.setText(name_text)
        self.lb_body_warn.setText(warn_text)
        self.canvas.set_body_image(self._body_qimage)
        if self._body_qimage is not None:
            pm = QPixmap.fromImage(self._body_qimage)
            size = self.lb_body_thumb.size()
            self.lb_body_thumb.setPixmap(pm.scaled(max(64, size.width() - 8), max(64, size.height() - 8),
                                                   Qt.KeepAspectRatio, Qt.FastTransformation))
        else:
            self.lb_body_thumb.clear()

    def _pick_body(self) -> None:
        start = self.config.get("body_dir", "") or os.path.expanduser("~")
        path, _ = QFileDialog.getOpenFileName(self, tr("act.pick_body"), start, "PNG (*.png)")
        if path:
            self.body_override = path
            self._refresh_body()

    def _body_auto(self) -> None:
        self.body_override = ""
        self._refresh_body()

    # ================================================================== #
    #  열기                                                               #
    # ================================================================== #
    def open_dialog(self) -> None:
        start = self.config.get("last_open_dir", "") or os.path.expanduser("~")
        path, _ = QFileDialog.getOpenFileName(self, tr("dlg.open.title"), start, tr(IMAGE_FILTER_KEY))
        if path:
            self.open_file(path)

    def _fill_recent_menu(self) -> None:
        self.m_recent.clear()
        items = [p for p in self.config.get("recent_files", [])][:MAX_RECENT_MENU]
        if not items:
            act = self.m_recent.addAction(tr("act.recent_none"))
            act.setEnabled(False)
            return
        for p in items:
            act = self.m_recent.addAction(p)
            act.triggered.connect(lambda _c=False, p=p: self.open_file(p))

    def _on_files_dropped(self, paths: list) -> None:
        self.open_file(paths[0])

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:
        paths = [u.toLocalFile() for u in event.mimeData().urls() if u.isLocalFile()]
        if paths:
            self.open_file(paths[0])

    def open_file(self, path: str) -> bool:
        if not self._confirm_discard():
            return False
        try:
            arr = export_utils.load_png_rgba(path)
        except Exception as e:
            QMessageBox.critical(self, tr("dlg.error.title"), tr("dlg.open.failed", path=path, error=str(e)))
            self.config.remove_recent("recent_files", path)
            return False

        h, w = arr.shape[:2]
        stem = os.path.splitext(os.path.basename(path))[0]
        presets = all_presets(self.config.get("sheet_presets", []))
        preset = pick_preset_for_file(presets, w, h, stem)
        if preset is not None:
            spec, pid = preset_spec(preset), preset["id"]
        else:
            dlg = SheetSpecDialog(self, sheet_size=(w, h))
            if dlg.exec() and dlg.result_spec():
                spec = dlg.result_spec()
                pid = "custom"
                if dlg.save_as_preset():
                    new_preset = make_user_preset(dlg.preset_name(), spec, dlg.body_motion())
                    self.config.set("sheet_presets",
                                    list(self.config.get("sheet_presets", [])) + [new_preset])
                    pid = new_preset["id"]
            else:
                spec, pid = GridSpec(1, 1, w, h), "custom"  # 취소하면 통짜 1×1 로 연다

        model = SheetModel(arr, spec, pid, stem, path)
        model.gender = detect_gender(stem) or GENDER_COMMON
        self.config.push_recent("recent_files", path)
        self.config.set("last_open_dir", os.path.dirname(path))
        self._set_model(model)
        self._show_message("status.opened", {"name": os.path.basename(path)})
        if not model.grid_matches_image():
            self._show_message("status.grid_image_mismatch_hint", {}, "warn")
        return True

    def _set_model(self, model: SheetModel) -> None:
        self.model = model
        self.body_override = ""
        self.autosaver.clear()  # 이전 문서의 자동 저장본은 필요 없다
        self._last_autosave_marker = None
        self.canvas.set_model(model)
        self.canvas.set_source(CellRect.single(0, 0))
        self.cb_skin.setCurrentIndex(max(0, self.cb_skin.findText(model.skin)))
        self._fill_gender_combo()
        self._fill_preset_combo()
        self._refresh_body()
        self._update_all()

    # ================================================================== #
    #  셀 편집                                                            #
    # ================================================================== #
    def _need_model(self) -> bool:
        return self.model is not None

    def _after_edit(self, label_key: str, fmt: dict | None = None, level: str = "info") -> None:
        self.canvas.refresh()
        self._update_all()
        self._show_message(label_key, fmt, level)

    def copy_cells(self) -> bool:
        src = self.canvas.source() if self._need_model() else None
        if src is None:
            self._show_message("status.no_source", {}, "warn")
            return False
        m = self.model
        self.clipboard = ClipboardData(cell_ops.copy_cells(m.arr, m.grid, src), src.shape,
                                       m.grid.cell_w, m.grid.cell_h, m.gender)
        self._update_actions()
        self._show_message("status.copied", {"rows": src.rows, "cols": src.cols})
        return True

    def cut_cells(self) -> None:
        if not self.copy_cells():
            return
        m, src = self.model, self.canvas.source()
        m.history.do(m.arr, cell_ops.make_clear_edit(m.arr, m.grid, src, "edit.cut"))
        self._after_edit("status.cut", {"rows": src.rows, "cols": src.cols})

    def clear_cells(self) -> None:
        src = self.canvas.source() if self._need_model() else None
        if src is None:
            self._show_message("status.no_source", {}, "warn")
            return
        m = self.model
        m.history.do(m.arr, cell_ops.make_clear_edit(m.arr, m.grid, src, "edit.clear"))
        self._after_edit("status.cleared", {"rows": src.rows, "cols": src.cols})

    def _resolve_dest(self, shape: tuple[int, int]) -> tuple[int, int] | None:
        """붙여넣을 위치(왼쪽 위 셀). Target이 있으면 그곳, 없으면 Source 왼쪽 위.
        Target을 범위로 지정했다면 클립보드와 **모양이 같을 때만** 허용한다 (§4.2)."""
        m = self.model
        t, s = self.canvas.target(), self.canvas.source()
        if t is not None:
            if t.shape != (1, 1) and t.shape != shape:
                self._show_message("status.shape_mismatch",
                                   {"tr": t.rows, "tc": t.cols, "sr": shape[0], "sc": shape[1]}, "warn")
                return None
            anchor = (t.r0, t.c0)
        elif s is not None:
            anchor = (s.r0, s.c0)
        else:
            self._show_message("status.no_source", {}, "warn")
            return None
        if not cell_ops.rect_fits(m.grid, anchor[0], anchor[1], shape):
            self._show_message("status.out_of_range", {}, "warn")
            return None
        return anchor

    def paste_cells(self, mirror: bool = False, flip_v: bool = False) -> None:
        if not self._need_model():
            return
        clip = self.clipboard
        if clip is None:
            self._show_message("status.no_clipboard", {}, "warn")
            return
        m = self.model
        if (clip.cell_w, clip.cell_h) != (m.grid.cell_w, m.grid.cell_h):
            self._show_message("status.cell_size_mismatch",
                               {"cw": clip.cell_w, "ch": clip.cell_h,
                                "mw": m.grid.cell_w, "mh": m.grid.cell_h}, "warn")
            return
        # 다른 체형으로 복사 금지 — 경고 (§3.1, CHR-ART.5 §5). 체형 공용은 제외.
        if clip.gender in (GENDER_FEMALE, GENDER_MALE) and m.gender in (GENDER_FEMALE, GENDER_MALE) \
                and clip.gender != m.gender:
            answer = QMessageBox.warning(
                self, tr("dlg.cross_gender.title"),
                tr("dlg.cross_gender.msg", src=clip.gender, dst=m.gender),
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if answer != QMessageBox.Yes:
                return
        anchor = self._resolve_dest(clip.shape)
        if anchor is None:
            return
        block = clip.block
        label_key, status_key = "edit.paste", "status.pasted"
        if mirror:
            block = cell_ops.mirror_block(block, m.grid)
            label_key, status_key = "edit.paste_mirror", "status.pasted_mirror"
        if flip_v:
            block = cell_ops.flip_block_vertical(block, m.grid)
            label_key, status_key = "edit.paste_flipv", "status.pasted_flipv"
        m.history.do(m.arr, cell_ops.make_paste_edit(m.arr, m.grid, anchor, block, label_key))
        # 미러는 비대칭 요소(가르마·무기 든 손)가 어긋날 수 있다 → 경고 문구 (§4.2)
        if mirror:
            self._after_edit("status.mirror_warn", {}, "warn")
        else:
            self._after_edit(status_key, {"cell": f"R{anchor[0] + 1}C{anchor[1] + 1}"})

    def mirror_apply(self) -> None:
        """Mirror & Apply: Source를 좌우 반전해 Target에 바로 덮어쓴다."""
        src = self.canvas.source() if self._need_model() else None
        if src is None:
            self._show_message("status.no_source", {}, "warn")
            return
        if self.canvas.target() is None:
            self._show_message("status.need_target", {}, "warn")
            return
        m = self.model
        anchor = self._resolve_dest(src.shape)
        if anchor is None:
            return
        block = cell_ops.mirror_block(cell_ops.copy_cells(m.arr, m.grid, src), m.grid)
        m.history.do(m.arr, cell_ops.make_paste_edit(m.arr, m.grid, anchor, block, "edit.mirror_apply"))
        self._after_edit("status.mirror_warn", {}, "warn")

    def undo(self) -> None:
        if self.model is None:
            return
        edit = self.model.history.undo(self.model.arr)
        if edit:
            self._after_edit("status.undone", {"label": tr(edit.label)})

    def redo(self) -> None:
        if self.model is None:
            return
        edit = self.model.history.redo(self.model.arr)
        if edit:
            self._after_edit("status.redone", {"label": tr(edit.label)})

    # ================================================================== #
    #  저장                                                               #
    # ================================================================== #
    def _on_alpha_panel_enabled(self, v: bool) -> None:
        self.config.set("alpha_threshold_enabled", bool(v))
        self.sp_alpha.setEnabled(bool(v))

    def _on_alpha_panel_value(self, v: int) -> None:
        self.config.set("alpha_threshold", int(v))

    def _on_save_dir_activated(self, _i: int) -> None:
        path = self.cb_save_dir.currentData() or ""
        self.config.set("save_dir", path)
        if path:
            self.config.push_recent("recent_save_dirs", path)
        self._fill_save_dir_combo()

    def _open_save_dir(self) -> None:
        folder, _note = export_utils.resolve_save_dir(self.config.get("save_dir", ""))
        open_folder(folder)

    def save(self, force_dialog: bool = False) -> bool:
        """저장. 처음이거나 '다른 이름으로'/'저장할 때마다 폴더 묻기'면 저장 대화상자를 연다."""
        m = self.model
        if m is None:
            return False

        issues = export_utils.validate_sheet(m.arr, m.grid)
        errors = [i for i in issues if i.level == "error"]
        if errors:
            QMessageBox.critical(self, tr("dlg.error.title"),
                                 "\n".join(tr(i.key, **i.fmt) for i in errors))
            return False

        ask = force_dialog or not m.save_path or bool(self.config.get("ask_save_dir_each_time", False))
        if ask:
            prefix_hint = ((self._current_preset() or {}).get("body_motion")
                           or (m.name.split("_")[0] if m.name else "H1WK") or "H1WK")
            dlg = SaveDialog(self.config, m, prefix_hint, self)
            if not dlg.exec():
                return False
            path = dlg.result_path()
            # 저장 대화상자에서 고른 폴더를 저장 폴더로 기억 (기본 output/ 이면 "" = 기본값 유지)
            chosen = os.path.dirname(path)
            is_default = os.path.normcase(os.path.normpath(chosen)) == os.path.normcase(
                os.path.normpath(export_utils.default_output_dir()))
            self.config.set("save_dir", "" if is_default else chosen)
        else:
            path = m.save_path

        out = export_utils.prepare_export(
            m.arr, bool(self.config.get("alpha_threshold_enabled", True)),
            int(self.config.get("alpha_threshold", export_utils.DEFAULT_ALPHA_THRESHOLD)),
            self.config.get("transparent_rgb", export_utils.TRANSPARENT_BLACK))
        try:
            backup = export_utils.save_png_rgba(out, path)
        except Exception as e:
            QMessageBox.critical(self, tr("dlg.error.title"), tr("dlg.save.failed", path=path, error=str(e)))
            return False

        m.save_path = path
        m.name = os.path.splitext(os.path.basename(path))[0]
        m.mark_saved()
        folder = os.path.dirname(path)
        self.config.push_recent("recent_save_dirs", folder)
        self.autosaver.clear()
        self._fill_save_dir_combo()
        self._update_all()
        self._show_message("status.saved_to" if not backup else "status.saved_to_backup",
                           {"path": path, "backup": os.path.basename(backup) if backup else ""})
        return True

    def _confirm_discard(self) -> bool:
        """수정된 문서가 있으면 저장/버리기/취소 확인. 계속 진행해도 되면 True."""
        m = self.model
        if m is None or not m.dirty:
            return True
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Question)
        box.setWindowTitle(tr("dlg.confirm_discard.title"))
        box.setText(tr("dlg.confirm_discard.msg", name=m.display_name))
        b_save = box.addButton(tr("btn.save"), QMessageBox.AcceptRole)
        b_discard = box.addButton(tr("btn.discard"), QMessageBox.DestructiveRole)
        box.addButton(tr("btn.cancel"), QMessageBox.RejectRole)
        box.exec()
        clicked = box.clickedButton()
        if clicked is b_save:
            return self.save()
        return clicked is b_discard

    # ================================================================== #
    #  자동 저장 / 복구 (§4.5-5)                                           #
    # ================================================================== #
    def _restart_autosave_timer(self) -> None:
        self.autosave_timer.stop()
        if self.config.get("autosave.enabled", True):
            minutes = max(1, min(60, int(self.config.get("autosave.interval_min", 10))))
            self.autosave_timer.start(minutes * 60 * 1000)

    def _autosave_meta(self) -> dict:
        m = self.model
        return {
            "name": m.name, "source_path": m.source_path, "preset_id": m.preset_id,
            "grid": {"cols": m.grid.cols, "rows": m.grid.rows,
                     "cell_w": m.grid.cell_w, "cell_h": m.grid.cell_h},
            "gender": m.gender, "skin": m.skin,
            "background": self.canvas.background(), "body_opacity": self.sl_opacity.value(),
            "saved_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }

    def _autosave_tick(self) -> None:
        m = self.model
        if m is None or not self.config.get("autosave.enabled", True) or not m.dirty:
            return
        marker = (id(m), m.history.revision, m.forced_dirty)
        if marker == self._last_autosave_marker:
            return
        keep = int(self.config.get("autosave.keep", 2))
        if self.autosaver.save(m.arr, self._autosave_meta(), keep) is not None:
            self._last_autosave_marker = marker
            self._show_message("status.autosaved", {"time": time.strftime("%H:%M")})

    def _offer_recovery(self) -> None:
        cands = self._recovery_candidates
        self._recovery_candidates = []
        if not cands:
            return
        snap = cands[0]
        meta = snap.meta
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Question)
        box.setWindowTitle(tr("dlg.recover.title"))
        box.setText(tr("dlg.recover.msg", name=meta.get("name") or snap.stamp,
                       time=meta.get("saved_at", "?")))
        b_recover = box.addButton(tr("btn.recover"), QMessageBox.AcceptRole)
        box.addButton(tr("btn.ignore"), QMessageBox.RejectRole)
        box.exec()
        if box.clickedButton() is b_recover:
            self._recover(snap)
        self.autosaver.discard_candidates(cands)

    def _recover(self, snap: Snapshot) -> None:
        try:
            arr = self.autosaver.load(snap)
        except Exception:
            QMessageBox.critical(self, tr("dlg.error.title"), tr("dlg.recover.failed"))
            return
        h, w = arr.shape[:2]
        g = snap.meta.get("grid") or {}
        try:
            spec = GridSpec(int(g["cols"]), int(g["rows"]), int(g["cell_w"]), int(g["cell_h"]))
        except (KeyError, TypeError, ValueError):
            spec = GridSpec(1, 1, w, h)
        meta = snap.meta
        model = SheetModel(arr, spec, meta.get("preset_id", "custom"), meta.get("name", ""),
                           meta.get("source_path", ""))
        model.gender = meta.get("gender", GENDER_COMMON)
        model.skin = meta.get("skin", "T1")
        model.forced_dirty = True  # 저장하기 전까지는 수정됨
        self._set_model(model)
        bg = meta.get("background")
        if bg in BACKGROUND_MODES:
            self._set_background(bg)
        if "body_opacity" in meta:
            self.sl_opacity.setValue(int(meta["body_opacity"]))
        self._autosave_tick()  # 복구본을 곧바로 새 자동 저장본으로 남겨 둔다 (또 비정상 종료돼도 안전)
        self._show_message("status.recovered", {"name": model.name})

    # ================================================================== #
    #  설정 / 도움말                                                       #
    # ================================================================== #
    def open_settings(self) -> None:
        dlg = SettingsDialog(self.config, self)
        dlg.languageChanged.connect(lambda _l: self.retranslate_ui())
        dlg.themeChanged.connect(self._apply_theme)
        dlg.settingsChanged.connect(self._on_settings_changed)
        dlg.exec()

    def _on_settings_changed(self) -> None:
        self._apply_view_settings()
        self._restart_autosave_timer()
        self._sync_panel_from_config()
        self._fill_preset_combo()
        self._fill_save_dir_combo()
        self._refresh_body()

    def _show_help(self, kind: str) -> None:
        HelpDialog(kind, self).exec()

    # ================================================================== #
    def closeEvent(self, event) -> None:
        if not self._confirm_discard():
            event.ignore()
            return
        self.config.set_window_size(self.width(), self.height())
        self.autosaver.end_session()
        super().closeEvent(event)
