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

import html
import os
import time
from dataclasses import dataclass

import numpy as np
from PIL import Image
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction, QActionGroup, QColor, QImage, QKeySequence, QPixmap
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QGridLayout, QGroupBox, QHBoxLayout, QLabel, QListWidget,
    QListWidgetItem, QMainWindow, QMenu, QMessageBox, QPushButton, QScrollArea, QSizePolicy, QSlider, QSpinBox, QSplitter, QStatusBar,
    QToolBar, QToolButton, QVBoxLayout, QWidget,
)

import cell_ops
import export_utils
import import_utils
import mask_convert as mc
import mask_tools
import pair_mode
import reference_layers
from about_dialog import AboutDialog
from app_info import APP_NAME, APP_VERSION
from autosave import AutoSaver, Snapshot
from canvas_widget import (
    BACKGROUND_MODES, BG_CHECKER, ZOOM_STEPS, CanvasWidget,
)
from config_manager import ConfigManager
from layer_model import (
    KIND_BODY, KIND_EXTRA, KIND_PAIR, MAX_EXTRA_LAYERS, MAX_REFERENCE_LAYERS, PARTS, RefLayer, apply_part_order,
    can_add_extra, part_is_above, sort_for_paint,
)
from help_dialog import KIND_CHANGELOG, KIND_HELP, HelpDialog
from color_widgets import FourColorEdit, colors_from_config, colors_to_config
from compare_dialog import DEFAULT_SLOTS, CompareDialog
from convert_dialog import ConvertDialog
from history import Edit, make_edit
from i18n import Translatable, set_language, tr
from import_dialog import ImportDialog
from stats_dialog import StatsDialog
from preset_dialog import SheetSpecDialog, grid_text, preset_list_label
from save_dialog import SaveDialog
from settings_dialog import SettingsDialog
from sheet_model import (
    GENDER_COMMON, GENDER_FEMALE, GENDER_MALE, CellRect, GridSpec, SheetModel, all_presets,
    detect_gender, find_matching_presets, make_user_preset, pick_preset_for_file, preset_spec, rect_label,
)
from theme import canvas_colors, get_stylesheet
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
    mask: np.ndarray | None = None  # 마스크가 있는 시트에서 복사했으면 같은 영역의 인덱스 마스크


MASK_VIEW_OFF, MASK_VIEW_OVERLAY, MASK_VIEW_ONLY = "off", "overlay", "only"
MASK_VIEW_MODES = (MASK_VIEW_OFF, MASK_VIEW_OVERLAY, MASK_VIEW_ONLY)
DEFAULT_PREVIEW_COLORS = [mc.parse_hex(h) for h in DEFAULT_SLOTS[3]["colors"]]  # 네이비 (색 차이가 잘 보임)


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
        # 구버전 설정 이전: 배경 "Base Body 오버레이" → Body 레이어 표시로
        if self.config.get("background_mode", BG_CHECKER) == "body":
            self.config.set("background_mode", BG_CHECKER)
            self.config.set("body_visible", True)
        self.body_layer = RefLayer(
            name="", kind=KIND_BODY, visible=bool(self.config.get("body_visible", False)),
            opacity=int(self.config.get("body_opacity", 60)),
            above=bool(self.config.get("body_above", False)), uid=0)
        self.body_layer.clamp()
        self.extra_layers: list[RefLayer] = []
        self._layer_uid = 0
        self.edit_part = "Outfit"  # 편집 중인 시트가 어느 파츠인지 (§7 합성 순서의 기준)
        # 3단계: 마스크 도구 / F·M 쌍 작업
        self.paint_tool: str | None = None
        self.paint_index = 1
        self._stroke: dict | None = None
        self._mv_display = None
        self._mv_overlay = None
        self.pair: pair_mode.PairReference | None = None
        self.pair_layer: RefLayer | None = None
        self._pair_syncing = False
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

        self.a_toggle_body = a("act.toggle_body", self._toggle_body, "Shift+B")
        self.a_toggle_edit = a("act.toggle_edit", self._toggle_edit_visible, "Shift+E")
        self.a_add_ref = a("act.add_ref", self._add_reference_dialog, "Ctrl+Shift+R")
        self.a_open_save_dir = a("act.open_save_dir", self._open_save_dir)
        self.a_pick_body = a("act.pick_body", self._pick_body)
        self.a_spec_custom = a("act.spec_custom", self._custom_spec_dialog)
        self.a_import_adjust = a("act.import_adjust", self.adjust_current_image, "Ctrl+Shift+I")
        # 2단계: 회색+마스크 / Nudge / 미리보기·비교·통계
        self.a_convert = a("act.convert", self.convert_to_gray_mask, "Ctrl+Shift+G")
        self.a_remove_mask = a("act.remove_mask", self.remove_mask)
        self.a_compare = a("act.compare", self.open_compare, "Ctrl+Shift+P")
        self.a_stats = a("act.stats", self.open_stats, "Ctrl+Shift+T")
        self.a_mask_view = a("act.mask_view_cycle", self._cycle_mask_view, "Alt+M")
        self.a_missing = a("act.mask_missing", self._toggle_missing, checkable=True)
        self.a_tint_view = a("act.tint_view", self._toggle_tint_view, "Alt+T", checkable=True)
        self.nudge_actions: list[QAction] = []
        for key, dx, dy, sc in (("act.nudge_left", -1, 0, "Alt+Left"), ("act.nudge_right", 1, 0, "Alt+Right"),
                                ("act.nudge_up", 0, -1, "Alt+Up"), ("act.nudge_down", 0, 1, "Alt+Down")):
            self.nudge_actions.append(a(key, lambda dx=dx, dy=dy: self.nudge(dx, dy), sc))
        for key, dx, dy, sc in (("act.nudge_left5", -5, 0, "Alt+Shift+Left"),
                                ("act.nudge_right5", 5, 0, "Alt+Shift+Right"),
                                ("act.nudge_up5", 0, -5, "Alt+Shift+Up"),
                                ("act.nudge_down5", 0, 5, "Alt+Shift+Down")):
            self.nudge_actions.append(a(key, lambda dx=dx, dy=dy: self.nudge(dx, dy), sc))
        # 3단계: 마스크 보정 도구 / 셀 간 마스크 복사 / 쌍 작업 / 참조 파츠
        self.tool_actions: dict[str | None, QAction] = {}
        self.tool_group = QActionGroup(self)
        self.tool_group.setExclusive(True)
        for tool, key, sc in ((None, "tool.select", "V"), (mask_tools.TOOL_PENCIL, "tool.pencil", "P"),
                              (mask_tools.TOOL_FILL, "tool.fill", "F"), (mask_tools.TOOL_RECT, "tool.rect", "R"),
                              (mask_tools.TOOL_ERASER, "tool.eraser", "E")):
            act = a(key, lambda tool=tool: self._set_paint_tool(tool), sc)
            act.setCheckable(True)
            self.tool_group.addAction(act)
            self.tool_actions[tool] = act
        self.tool_actions[None].setChecked(True)
        self.index_actions: dict[int, QAction] = {}
        self.index_group = QActionGroup(self)
        self.index_group.setExclusive(True)
        for n in (1, 2, 3, 4):
            act = a(f"tool.index{n}", lambda n=n: self._set_paint_index(n), str(n))
            act.setCheckable(True)
            self.index_group.addAction(act)
            self.index_actions[n] = act
        self.index_actions[1].setChecked(True)
        self.a_mask_replace = a("act.mask_replace", self.replace_region)
        self.a_mask_copy_target = a("act.mask_copy_target", self.mask_copy_to_target, "Ctrl+Alt+M")
        self.a_mask_copy_row = a("act.mask_copy_row", self.mask_copy_to_row)
        self.a_pair_start = a("act.pair_start", self.start_pair)
        self.a_pair_end = a("act.pair_end", self.end_pair)
        self.a_part_auto = a("act.part_auto", self.auto_load_parts)
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
        self.m_edit.addSeparator()
        self.m_nudge = self.m_edit.addMenu("")
        self._t(self.m_nudge.setTitle, "menu.nudge")
        for i, act in enumerate(self.nudge_actions):
            if i == 4:
                self.m_nudge.addSeparator()
            self.m_nudge.addAction(act)

        self.m_view = mb.addMenu("")
        self._t(self.m_view.setTitle, "menu.view")
        self.m_view.addAction(self.a_grid)
        self.m_view.addAction(self.a_zoom_in)
        self.m_view.addAction(self.a_zoom_out)
        self.m_view.addSeparator()
        self.m_view.addAction(self.a_toggle_body)
        self.m_view.addAction(self.a_toggle_edit)
        self.m_view.addAction(self.a_add_ref)
        self.m_view.addSeparator()
        self.m_bg = self.m_view.addMenu("")
        self._t(self.m_bg.setTitle, "act.bg_menu")
        for mode in BACKGROUND_MODES:
            self.m_bg.addAction(self.bg_actions[mode])
        self.m_bg.addSeparator()
        self.m_bg.addAction(self.a_bg_cycle)
        self.m_view.addSeparator()
        for act in (self.a_mask_view, self.a_missing, self.a_tint_view, None, self.a_compare, self.a_stats):
            self.m_view.addSeparator() if act is None else self.m_view.addAction(act)

        self.m_tools = mb.addMenu("")
        self._t(self.m_tools.setTitle, "menu.tools")
        self.m_tools.addAction(self.a_import_adjust)
        self.m_tools.addAction(self.a_convert)
        self.m_tools.addAction(self.a_remove_mask)
        self.m_tools.addSeparator()
        self.m_maskedit = self.m_tools.addMenu("")
        self._t(self.m_maskedit.setTitle, "menu.maskedit")
        for tool in (None, mask_tools.TOOL_PENCIL, mask_tools.TOOL_FILL, mask_tools.TOOL_RECT,
                     mask_tools.TOOL_ERASER):
            self.m_maskedit.addAction(self.tool_actions[tool])
        self.m_maskedit.addSeparator()
        for n in (1, 2, 3, 4):
            self.m_maskedit.addAction(self.index_actions[n])
        self.m_maskedit.addSeparator()
        for act in (self.a_mask_replace, self.a_mask_copy_target, self.a_mask_copy_row):
            self.m_maskedit.addAction(act)
        self.m_tools.addAction(self.a_pair_start)
        self.m_tools.addAction(self.a_pair_end)
        self.m_tools.addAction(self.a_part_auto)
        self.m_tools.addSeparator()
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
        left_scroll = QScrollArea()  # 2단계 패널이 늘어 작은 화면에서도 잘리지 않게 스크롤
        left_scroll.setWidgetResizable(True)
        left_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        left_scroll.setFrameShape(QScrollArea.NoFrame)
        left_scroll.setWidget(self._build_left_panel())
        left_scroll.setMinimumWidth(250)
        splitter.addWidget(left_scroll)
        self.lb_layerbar = QLabel()
        self.lb_layerbar.setObjectName("LayerBar")
        self.lb_layerbar.setTextFormat(Qt.RichText)
        self.lb_layerbar.setWordWrap(True)
        center = QWidget()
        clay = QVBoxLayout(center)
        clay.setContentsMargins(0, 0, 0, 0)
        clay.setSpacing(0)
        clay.addWidget(self.lb_layerbar)
        # 3단계: F/M 쌍 작업 — 읽기 전용 참조 캔버스를 왼쪽에 나란히 둔다 (평소엔 숨김)
        self.pair_canvas = CanvasWidget()
        self.pair_canvas.set_readonly(True)
        self.pair_canvas.zoomChanged.connect(self._on_pair_zoom)
        self.pair_box = QWidget()
        pbl = QVBoxLayout(self.pair_box)
        pbl.setContentsMargins(0, 0, 0, 0)
        pbl.setSpacing(0)
        self.lb_pair_title = QLabel()
        self.lb_pair_title.setObjectName("LayerBar")
        pbl.addWidget(self.lb_pair_title)
        pbl.addWidget(self.pair_canvas, stretch=1)
        self.pair_box.hide()
        self.canvas_split = QSplitter(Qt.Horizontal)
        self.canvas_split.addWidget(self.pair_box)
        self.canvas_split.addWidget(self.canvas)
        self.canvas_split.setStretchFactor(0, 1)
        self.canvas_split.setStretchFactor(1, 1)
        self.canvas_split.setCollapsible(1, False)
        for sb_a, sb_b, kind in ((self.canvas.horizontalScrollBar(), self.pair_canvas.horizontalScrollBar(), "h"),
                                 (self.canvas.verticalScrollBar(), self.pair_canvas.verticalScrollBar(), "v")):
            sb_a.valueChanged.connect(lambda v, sb=sb_b: self._mirror_scroll(sb, v))
            sb_b.valueChanged.connect(lambda v, sb=sb_a: self._mirror_scroll(sb, v))
        self.canvas.paintPressed.connect(self._on_paint_pressed)
        self.canvas.paintMoved.connect(self._on_paint_moved)
        self.canvas.paintReleased.connect(self._on_paint_released)
        clay.addWidget(self.canvas_split, stretch=1)
        splitter.addWidget(center)
        splitter.addWidget(self._build_right_panel())
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setStretchFactor(2, 0)
        splitter.setSizes([260, 980, 250])
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
        panel.setMinimumWidth(230)
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
        layout.addWidget(self._build_outfit_group())
        layout.addWidget(self._build_maskedit_group())
        layout.addWidget(self._build_pair_group())

        layout.addStretch(1)
        return panel

    def _build_outfit_group(self) -> QGroupBox:
        """옷 색 · 마스크 (2단계): 변환 / 마스크 보기 / 색 미리보기 / 비교·통계."""
        gb, lay = self._group("panel.outfit")
        self.lb_mask_state = QLabel()
        self.lb_mask_state.setWordWrap(True)
        self.lb_mask_state.setObjectName("DimLabel")
        lay.addWidget(self.lb_mask_state)
        btn_conv = QToolButton()
        btn_conv.setDefaultAction(self.a_convert)
        btn_conv.setToolButtonStyle(Qt.ToolButtonTextOnly)
        btn_conv.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        lay.addWidget(btn_conv)

        row = QHBoxLayout()
        lb = QLabel()
        self._t(lb.setText, "panel.mask_view")
        row.addWidget(lb)
        self.cb_mask_view = QComboBox()
        self.cb_mask_view.activated.connect(self._on_mask_view_activated)
        row.addWidget(self.cb_mask_view, 1)
        lay.addLayout(row)
        self.sl_mask_opacity = QSlider(Qt.Horizontal)
        self.sl_mask_opacity.setRange(10, 100)
        self.sl_mask_opacity.setValue(int(self.config.get("mask_view.opacity", 60)))
        self.sl_mask_opacity.valueChanged.connect(self._on_mask_opacity)
        lay.addWidget(self.sl_mask_opacity)
        self.cb_missing = QCheckBox()
        self._t(self.cb_missing.setText, "act.mask_missing")
        self.cb_missing.toggled.connect(self.a_missing.setChecked)
        self.a_missing.toggled.connect(self.cb_missing.setChecked)
        lay.addWidget(self.cb_missing)

        self.cb_tint = QCheckBox()
        self._t(self.cb_tint.setText, "act.tint_view")
        self.cb_tint.toggled.connect(self.a_tint_view.setChecked)
        self.a_tint_view.toggled.connect(self.cb_tint.setChecked)
        lay.addWidget(self.cb_tint)
        self.preview_colors = FourColorEdit(colors_from_config(
            self.config.get("outfit_preview.colors"), DEFAULT_PREVIEW_COLORS))
        self.preview_colors.colorsChanged.connect(self._on_preview_colors)
        lay.addWidget(self.preview_colors)
        grow = QHBoxLayout()
        lb_gain = QLabel()
        self._t(lb_gain.setText, "panel.gain")
        self._t(lb_gain.setToolTip, "panel.gain_tip")
        grow.addWidget(lb_gain)
        self.sp_gain = QDoubleSpinBox()
        self.sp_gain.setRange(0.5, 2.0)
        self.sp_gain.setSingleStep(0.01)
        self.sp_gain.setDecimals(2)
        self.sp_gain.setValue(float(self.config.get("outfit_preview.gain", 1.0)))
        self.sp_gain.valueChanged.connect(self._on_gain)
        grow.addWidget(self.sp_gain, 1)
        lay.addLayout(grow)
        for act in (self.a_compare, self.a_stats):
            b = QToolButton()
            b.setDefaultAction(act)
            b.setToolButtonStyle(Qt.ToolButtonTextOnly)
            b.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            lay.addWidget(b)
        return gb

    # ================================================================== #
    #  3단계: 마스크 경계 보정 / 셀 간 마스크 복사 (§4.3-3)                 #
    # ================================================================== #
    def _build_maskedit_group(self) -> QGroupBox:
        gb, lay = self._group("panel.maskedit")
        hint = QLabel()
        hint.setWordWrap(True)
        hint.setObjectName("DimLabel")
        self._t(hint.setText, "panel.maskedit_hint")
        lay.addWidget(hint)

        grid = QGridLayout()
        for i, tool in enumerate((None, mask_tools.TOOL_PENCIL, mask_tools.TOOL_FILL,
                                  mask_tools.TOOL_RECT, mask_tools.TOOL_ERASER)):
            b = QToolButton()
            b.setDefaultAction(self.tool_actions[tool])
            b.setToolButtonStyle(Qt.ToolButtonTextOnly)
            b.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            grid.addWidget(b, i // 3, i % 3)
        lay.addLayout(grid)

        irow = QHBoxLayout()
        lb = QLabel()
        self._t(lb.setText, "panel.index")
        irow.addWidget(lb)
        for n in (1, 2, 3, 4):
            b = QToolButton()
            b.setDefaultAction(self.index_actions[n])
            b.setToolButtonStyle(Qt.ToolButtonTextOnly)
            irow.addWidget(b)
        lay.addLayout(irow)

        brow = QHBoxLayout()
        lb = QLabel()
        self._t(lb.setText, "panel.brush_size")
        brow.addWidget(lb)
        self.sp_brush = QSpinBox()
        self.sp_brush.setRange(1, mask_tools.MAX_BRUSH)
        self.sp_brush.setValue(int(self.config.get("mask_edit.brush", 1)))
        self.sp_brush.valueChanged.connect(self._on_brush_size)
        brow.addWidget(self.sp_brush, 1)
        lay.addLayout(brow)

        srow = QHBoxLayout()
        lb = QLabel()
        self._t(lb.setText, "panel.scope")
        srow.addWidget(lb)
        self.cb_scope = QComboBox()
        self.cb_scope.activated.connect(self._on_scope_activated)
        srow.addWidget(self.cb_scope, 1)
        lay.addLayout(srow)

        rrow = QHBoxLayout()
        lb = QLabel()
        self._t(lb.setText, "panel.replace")
        rrow.addWidget(lb)
        self.cb_rep_from, self.cb_rep_to = QComboBox(), QComboBox()
        for cb in (self.cb_rep_from, self.cb_rep_to):
            for n in range(mask_tools.MAX_INDEX + 1):
                cb.addItem(str(n), n)
        self.cb_rep_to.setCurrentIndex(1)
        rrow.addWidget(self.cb_rep_from)
        rrow.addWidget(QLabel("→"))
        rrow.addWidget(self.cb_rep_to)
        lay.addLayout(rrow)
        for act in (self.a_mask_replace, self.a_mask_copy_target, self.a_mask_copy_row):
            b = QToolButton()
            b.setDefaultAction(act)
            b.setToolButtonStyle(Qt.ToolButtonTextOnly)
            b.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            lay.addWidget(b)
        return gb

    def _fill_scope_combo(self) -> None:
        cur = self.config.get("mask_edit.scope", mask_tools.SCOPE_CELL)
        self.cb_scope.clear()
        for sc, key in ((mask_tools.SCOPE_CELL, "panel.scope_cell"),
                        (mask_tools.SCOPE_SELECTION, "panel.scope_selection"),
                        (mask_tools.SCOPE_SHEET, "panel.scope_sheet")):
            self.cb_scope.addItem(tr(key), sc)
        self.cb_scope.setCurrentIndex(max(0, self.cb_scope.findData(cur)))

    def _scope(self) -> str:
        return self.cb_scope.currentData() or mask_tools.SCOPE_CELL

    def _on_scope_activated(self, _i: int) -> None:
        self.config.set("mask_edit.scope", self._scope())

    def _on_brush_size(self, v: int) -> None:
        self.config.set("mask_edit.brush", int(v))
        self.canvas.set_brush_size(int(v))

    def _set_paint_index(self, n: int) -> None:
        self.paint_index = int(n)
        act = self.index_actions.get(self.paint_index)
        if act is not None and not act.isChecked():
            act.setChecked(True)
        # 번호를 고르면 연필로 바로 칠할 수 있게 (선택 모드·지우개에서 번호를 눌렀을 때)
        if self.paint_tool in (None, mask_tools.TOOL_ERASER):
            self._set_paint_tool(mask_tools.TOOL_PENCIL)

    def _set_paint_tool(self, tool) -> None:
        self._commit_stroke()
        self.paint_tool = tool
        act = self.tool_actions.get(tool)
        if act is not None and not act.isChecked():
            act.setChecked(True)
        self.canvas.set_paint_tool(tool, self.sp_brush.value())
        if tool is None:
            return
        if self.config.get("mask_view.mode", MASK_VIEW_OFF) == MASK_VIEW_OFF:
            self._set_mask_view(MASK_VIEW_OVERLAY)  # 칠한 결과가 보여야 하므로 오버레이를 자동으로 켠다
        m = self.model
        if m is not None and not m.has_mask:
            self._show_message("status.paint_need_mask", {}, "warn")
        else:
            self._show_message("status.mask_tool", {"tool": tr(f"tool.{tool}"), "index": self.paint_index})

    def _update_maskedit_ui(self) -> None:
        m = self.model
        has = m is not None
        has_mask = bool(has and m.has_mask)
        src = self.canvas.source() if has else None
        for act in self.tool_actions.values():
            act.setEnabled(has)
        for act in self.index_actions.values():
            act.setEnabled(has)
        for w in (self.sp_brush, self.cb_scope, self.cb_rep_from, self.cb_rep_to):
            w.setEnabled(has)
        self.a_mask_replace.setEnabled(has_mask)
        self.a_mask_copy_target.setEnabled(has_mask and src is not None)
        self.a_mask_copy_row.setEnabled(has_mask and src is not None)
        self.a_pair_start.setEnabled(bool(has and pair_mode.can_start(m.gender)))
        self.a_pair_end.setEnabled(self.pair is not None)
        self.a_part_auto.setEnabled(has)
        self.cb_pair_view.setEnabled(self.pair is not None)
        if self.pair is not None:
            self.lb_pair_state.setText(tr("panel.pair_on", name=self.pair.display_name, g=self.pair.gender))
        else:
            self.lb_pair_state.setText(tr("panel.pair_off"))

    # -- 붓질 ---------------------------------------------------------------
    def _paint_label(self, tool: str) -> str:
        return {mask_tools.TOOL_PENCIL: "edit.paint", mask_tools.TOOL_FILL: "edit.fill",
                mask_tools.TOOL_RECT: "edit.rect_fill", mask_tools.TOOL_ERASER: "edit.erase"}[tool]

    def _on_paint_pressed(self, x: int, y: int) -> None:
        self._commit_stroke()
        m, tool = self.model, self.paint_tool
        if m is None or tool is None:
            return
        if not m.has_mask:
            self._show_message("status.paint_need_mask", {}, "warn")
            return
        g = m.grid
        cell = (min(max(0, y // g.cell_h), g.rows - 1), min(max(0, x // g.cell_w), g.cols - 1))
        bounds = mask_tools.scope_bounds(g, self._scope(), cell, self.canvas.source())
        index = 0 if tool == mask_tools.TOOL_ERASER else self.paint_index
        st = {"rec": mask_tools.MaskRecorder(m.arr, m.mask), "bounds": bounds, "index": index,
              "tool": tool, "start": (x, y), "last": (x, y)}
        self._stroke = st
        alpha = m.arr[..., 3]
        if tool in (mask_tools.TOOL_PENCIL, mask_tools.TOOL_ERASER):
            d = mask_tools.stamp(m.mask, alpha, x, y, self.sp_brush.value(), index, bounds)
        elif tool == mask_tools.TOOL_FILL:
            d = mask_tools.flood_fill(m.mask, alpha, x, y, index, bounds)
        else:
            d = None  # 사각형은 놓을 때 한 번에 채운다
        st["rec"].add(d)
        self._patch_mask_view(d)

    def _on_paint_moved(self, x: int, y: int) -> None:
        st, m = self._stroke, self.model
        if st is None or m is None or st["tool"] not in (mask_tools.TOOL_PENCIL, mask_tools.TOOL_ERASER):
            return
        d = mask_tools.stroke_line(m.mask, m.arr[..., 3], st["last"], (x, y), self.sp_brush.value(),
                                   st["index"], st["bounds"])
        st["last"] = (x, y)
        st["rec"].add(d)
        self._patch_mask_view(d)

    def _on_paint_released(self, x: int, y: int) -> None:
        st, m = self._stroke, self.model
        if st is None or m is None:
            return
        if st["tool"] == mask_tools.TOOL_RECT:
            d = mask_tools.fill_rect(m.mask, m.arr[..., 3], st["start"], (x, y), st["index"], st["bounds"])
            st["rec"].add(d)
        self._commit_stroke()

    def _commit_stroke(self) -> None:
        st, self._stroke = self._stroke, None
        if st is None or self.model is None:
            return
        self._commit_mask_edit(st["rec"], self._paint_label(st["tool"]), "status.painted")

    def _commit_mask_edit(self, rec, label: str, status_key: str, fmt: dict | None = None) -> bool:
        edit = rec.finish(label)
        if edit is None:
            self._refresh_mask_view()  # 되돌려진 부분 없이 그림만 맞춘다
            return False
        edit.has_mask_before = edit.has_mask_after = True
        self.model.history.do(self.model, edit)
        self._after_edit(status_key, {"label": tr(label), **(fmt or {})})
        return True

    def replace_region(self) -> None:
        m = self.model
        if m is None or not m.has_mask:
            self._show_message("status.need_mask", {}, "warn")
            return
        src = self.canvas.source()
        cell = (src.r0, src.c0) if src else None
        bounds = mask_tools.scope_bounds(m.grid, self._scope(), cell, src)
        a, b = int(self.cb_rep_from.currentData()), int(self.cb_rep_to.currentData())
        rec = mask_tools.MaskRecorder(m.arr, m.mask)
        d, n = mask_tools.replace_index(m.mask, m.arr[..., 3], a, b, bounds)
        rec.add(d)
        if not self._commit_mask_edit(rec, "edit.replace_index", "status.replaced",
                                      {"src": a, "dst": b, "n": n}):
            self._show_message("status.replace_none", {"src": a}, "warn")

    def _mask_copy(self, anchors: list[tuple[int, int]], block: np.ndarray) -> int:
        m = self.model
        rec = mask_tools.MaskRecorder(m.arr, m.mask)
        for anchor in anchors:
            rec.add(mask_tools.paste_mask_block(m.mask, m.arr[..., 3], m.grid, anchor, block))
        return 1 if self._commit_mask_edit(rec, "edit.mask_copy", "status.mask_copied", {"n": len(anchors)}) else 0

    def mask_copy_to_target(self) -> None:
        """Source 셀의 마스크만 Target 셀로 복사한다 (그림은 그대로). 알파가 없는 곳은 0으로 둔다."""
        m = self.model
        if m is None or not m.has_mask:
            self._show_message("status.need_mask", {}, "warn")
            return
        src, tgt = self.canvas.source(), self.canvas.target()
        if src is None:
            self._show_message("status.no_source", {}, "warn")
            return
        if tgt is None:
            self._show_message("status.mask_copy_need_target", {}, "warn")
            return
        anchor = self._resolve_dest(src.shape)
        if anchor is None:
            return
        block = cell_ops.copy_cells(m.mask, m.grid, src)
        if not self._mask_copy([anchor], block):
            self._show_message("status.mask_copy_none", {}, "warn")

    def mask_copy_to_row(self) -> None:
        """Source 첫 셀의 마스크를 같은 행의 다른 모든 셀로 복사한다."""
        m = self.model
        if m is None or not m.has_mask:
            self._show_message("status.need_mask", {}, "warn")
            return
        src = self.canvas.source()
        if src is None:
            self._show_message("status.no_source", {}, "warn")
            return
        one = CellRect.single(src.r0, src.c0)
        block = cell_ops.copy_cells(m.mask, m.grid, one)
        anchors = [(src.r0, c) for c in range(m.grid.cols) if c != src.c0]
        if not anchors or not self._mask_copy(anchors, block):
            self._show_message("status.mask_copy_none", {}, "warn")

    # ================================================================== #
    #  3단계: F/M 쌍 작업 모드 (§4.5-1) / 파츠 참조 레이어 (§4.1)            #
    # ================================================================== #
    def _build_pair_group(self) -> QGroupBox:
        gb, lay = self._group("panel.pair")
        self.lb_pair_state = QLabel()
        self.lb_pair_state.setWordWrap(True)
        self.lb_pair_state.setObjectName("DimLabel")
        lay.addWidget(self.lb_pair_state)
        row = QHBoxLayout()
        lb = QLabel()
        self._t(lb.setText, "panel.pair_view")
        row.addWidget(lb)
        self.cb_pair_view = QComboBox()
        self.cb_pair_view.activated.connect(self._on_pair_view_activated)
        row.addWidget(self.cb_pair_view, 1)
        lay.addLayout(row)
        for act in (self.a_pair_start, self.a_pair_end):
            b = QToolButton()
            b.setDefaultAction(act)
            b.setToolButtonStyle(Qt.ToolButtonTextOnly)
            b.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            lay.addWidget(b)
        return gb

    def _fill_pair_view_combo(self) -> None:
        cur = self.config.get("pair.view", pair_mode.VIEW_SIDE)
        self.cb_pair_view.clear()
        self.cb_pair_view.addItem(tr("panel.pair_view_side"), pair_mode.VIEW_SIDE)
        self.cb_pair_view.addItem(tr("panel.pair_view_overlay"), pair_mode.VIEW_OVERLAY)
        self.cb_pair_view.setCurrentIndex(max(0, self.cb_pair_view.findData(cur)))

    def _on_pair_view_activated(self, _i: int) -> None:
        self.config.set("pair.view", self.cb_pair_view.currentData() or pair_mode.VIEW_SIDE)
        self._apply_pair_view()

    def _refresh_pair_title(self) -> None:
        if self.pair is not None:
            self.lb_pair_title.setText(tr("layerbar.pair", name=self.pair.display_name))

    def _mirror_scroll(self, dst_bar, value: int) -> None:
        if self.pair is None or self._pair_syncing:
            return
        self._pair_syncing = True
        try:
            dst_bar.setValue(value)
        finally:
            self._pair_syncing = False

    def _on_pair_zoom(self, zoom: int) -> None:
        if self.pair is None or self._pair_syncing:
            return
        self._pair_syncing = True
        try:
            self.canvas.set_zoom(zoom)
        finally:
            self._pair_syncing = False

    def start_pair(self) -> None:
        """현재 시트·마스크를 반대 체형 새 문서로 복제하고, 원본은 읽기 전용 참조로 남긴다."""
        m = self.model
        if m is None:
            return
        other = pair_mode.other_gender(m.gender)
        if other is None:
            self._show_message("status.pair_need_gender", {}, "warn")
            return
        # 반대 체형 Base Body가 없으면 시작 전에 경고하고, 직접 지정 / 그냥 진행 / 취소를 고르게 한다 (기획서 §4.5-1)
        body_pick = ""
        motion = (self._current_preset() or {}).get("body_motion", "")
        if motion:
            found, expected = reference_layers.find_body(self.config.get("body_dir", ""), other, m.skin, motion)
            if found is None:
                choice, body_pick = self._ask_pair_body(expected)
                if choice == "cancel":
                    return
        if not self._confirm_discard():
            return
        base = m.name or "Sheet"
        ref = pair_mode.PairReference(m.arr.copy(), m.mask.copy(), bool(m.has_mask), m.grid, base, m.gender)
        new = SheetModel(m.arr.copy(), m.grid, m.preset_id, pair_mode.counterpart_name(base, other), "")
        new.gender, new.skin = other, m.skin
        if m.has_mask:
            new.set_mask(m.mask.copy())
        new.forced_dirty = True  # 새 문서 — 저장하기 전까지 수정됨
        self._set_model(new, keep_pair=True)
        if body_pick:
            self.body_override = body_pick
            self._refresh_body()
        self._enter_pair(ref)
        if new.has_mask and self.config.get("mask_view.mode", MASK_VIEW_OFF) == MASK_VIEW_OFF:
            self._set_mask_view(MASK_VIEW_OVERLAY)  # 체형이 다르면 마스크 경계가 어긋나므로 바로 확인할 수 있게
        if self.body_layer.image is None:
            self._show_message("status.pair_need_body", {"name": new.display_name}, "warn")
        else:
            self.body_layer.visible = True  # 반대 체형 Body 위에서 위치를 다시 맞추는 작업이므로 바로 보여 준다
            self.config.set("body_visible", True)
            self._push_layers()
            self._show_message("status.pair_started" if not new.has_mask else "status.pair_started_mask",
                               {"ref": ref.display_name, "name": new.display_name})

    def _ask_pair_body(self, expected: str) -> tuple[str, str]:
        """반대 체형 Body가 없을 때 묻는다. 반환: ("pick", 경로) / ("go", "") / ("cancel", "")."""
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Warning)
        box.setWindowTitle(tr("dlg.pair_body.title"))
        box.setText(tr("dlg.pair_body.msg", name=expected))
        b_pick = box.addButton(tr("dlg.pair_body.pick"), QMessageBox.AcceptRole)
        b_go = box.addButton(tr("dlg.pair_body.go"), QMessageBox.DestructiveRole)
        b_cancel = box.addButton(tr("dlg.pair_body.cancel"), QMessageBox.RejectRole)
        box.setDefaultButton(b_pick)
        box.exec()
        clicked = box.clickedButton()
        if clicked is b_pick:
            start = self.config.get("body_dir", "") or os.path.expanduser("~")
            path, _ = QFileDialog.getOpenFileName(self, tr("act.pick_body"), start, "PNG (*.png)")
            return ("pick", path) if path else ("cancel", "")
        if clicked is b_go:
            return "go", ""
        return "cancel", ""

    def _enter_pair(self, ref: pair_mode.PairReference) -> None:
        self.pair = ref
        rm = SheetModel(ref.arr.copy(), ref.grid, "custom", ref.name, "")
        rm.gender = ref.gender
        self.pair_canvas.set_model(rm)
        self.pair_canvas.set_source(None)
        h, w = ref.arr.shape[:2]
        img = QImage(ref.arr.data, w, h, w * 4, QImage.Format_RGBA8888).copy()
        self.pair_layer = RefLayer(name=ref.display_name, image=img, kind=KIND_PAIR, visible=True,
                                   opacity=int(self.config.get("pair.overlay_opacity", 40)),
                                   above=True, uid=-1)
        self._refresh_pair_title()
        self._apply_pair_view()

    def _apply_pair_view(self) -> None:
        side = self.pair is not None and self.config.get("pair.view", pair_mode.VIEW_SIDE) == pair_mode.VIEW_SIDE
        self.pair_box.setVisible(side)
        if side:
            self.canvas_split.setSizes([500, 500])
            self._pair_syncing = True
            try:
                self.pair_canvas.set_zoom(self.canvas.zoom())
                self.pair_canvas.horizontalScrollBar().setValue(self.canvas.horizontalScrollBar().value())
                self.pair_canvas.verticalScrollBar().setValue(self.canvas.verticalScrollBar().value())
            finally:
                self._pair_syncing = False
        self._push_layers()
        self._update_actions()

    def _clear_pair(self) -> None:
        if self.pair is None and self.pair_layer is None:
            return
        self.pair = None
        self.pair_layer = None
        self.pair_box.hide()
        self.pair_canvas.set_model(None)
        self._push_layers()
        self._update_actions()

    def end_pair(self) -> None:
        if self.pair is None:
            return
        self._clear_pair()
        self._show_message("status.pair_ended")

    def _on_edit_part(self, _i: int) -> None:
        self.edit_part = self.cb_edit_part.currentData() or "Outfit"
        apply_part_order(self.extra_layers, self.edit_part)
        self._push_layers()
        self._show_message("status.edit_part", {"part": self.edit_part})

    def auto_load_parts(self) -> None:
        """Allies/<파츠>/ 폴더에서 편집 시트와 체형·모션이 맞는 파츠를 참조 레이어로 불러온다 (Hair·Eyes는 기본값, 그 밖엔 후보가 하나일 때)."""
        m = self.model
        if m is None:
            return
        allies = reference_layers.allies_dir_from(self.config.get("body_dir", ""), self.config.get("allies_dir", ""))
        motion = (self._current_preset() or {}).get("body_motion", "")
        have = {os.path.normcase(os.path.normpath(r.path)) for r in self.extra_layers if r.path}
        loaded = 0
        for part in PARTS:
            if part in ("Body", self.edit_part):
                continue
            path = reference_layers.auto_part_file(allies, part, m.gender, motion, m.skin)
            if not path or os.path.normcase(os.path.normpath(path)) in have:
                continue
            if not can_add_extra(len(self.extra_layers)):
                self._show_message("status.layer_limit", {"max": MAX_REFERENCE_LAYERS}, "warn")
                return
            if self.add_reference_layer(path, part=part):
                loaded += 1
        if loaded:
            self._show_message("status.parts_loaded", {"n": loaded})
        else:
            self._show_message("status.parts_none", {"dir": allies or "-"}, "warn")

    def _build_right_panel(self) -> QWidget:
        panel = QWidget()
        panel.setMinimumWidth(200)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(6, 6, 6, 6)

        layout.addWidget(self._build_layers_group())

        gb, lay = self._group("panel.background")
        self.cb_bg = QComboBox()
        self.cb_bg.activated.connect(self._on_bg_activated)
        lay.addWidget(self.cb_bg)

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
        self.lb_body_thumb.setMinimumHeight(110)
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
        self._fill_mask_view_combo()
        self._fill_scope_combo()
        self._fill_pair_view_combo()
        self._sync_panel_from_config()
        self._refresh_body()
        self._refresh_pair_title()
        self._update_all()

    def _update_all(self) -> None:
        self._rebuild_layer_list()
        self._update_layer_bar()
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
        self.a_import_adjust.setEnabled(has)
        has_mask = bool(has and m.has_mask)
        self.a_convert.setEnabled(has)
        for act in (self.a_remove_mask, self.a_compare, self.a_stats, self.a_mask_view, self.a_missing,
                    self.a_tint_view):
            act.setEnabled(has_mask)
        self.cb_mask_view.setEnabled(has_mask)
        self.sl_mask_opacity.setEnabled(has_mask)
        for act in self.nudge_actions:
            act.setEnabled(has_src)
        if not has:
            self.lb_mask_state.setText("")
        elif has_mask:
            self.lb_mask_state.setText(tr("panel.mask_on", name=mc.mask_path_for(m.display_name) if m.name else "_M"))
        else:
            self.lb_mask_state.setText(tr("panel.mask_off"))
        self.cb_preset.setEnabled(has)
        self.cb_gender.setEnabled(has)
        self.cb_skin.setEnabled(has)
        self._update_maskedit_ui()

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
        self.pair_canvas.set_theme(theme)
        if hasattr(self, "list_layers"):
            self._rebuild_layer_list()

    def _apply_view_settings(self) -> None:
        self.canvas.set_grid_style(self.config.get("grid.color", "#00e5ff"),
                                   int(self.config.get("grid.width", 1)))
        self.canvas.set_show_grid(self.a_grid.isChecked())
        mode = self.config.get("background_mode", BG_CHECKER)
        self.canvas.set_background(mode if mode in BACKGROUND_MODES else BG_CHECKER)
        self.canvas.set_reference_tint(bool(self.config.get("reference_tint", True)))
        pc = self.pair_canvas
        pc.set_grid_style(self.config.get("grid.color", "#00e5ff"), int(self.config.get("grid.width", 1)))
        pc.set_show_grid(self.a_grid.isChecked())
        pc.set_background(mode if mode in BACKGROUND_MODES else BG_CHECKER)

    def _on_grid_toggled(self, checked: bool) -> None:
        self.canvas.set_show_grid(checked)
        self.pair_canvas.set_show_grid(checked)
        self.config.set("grid.show", bool(checked))

    def _set_background(self, mode: str) -> None:
        self.canvas.set_background(mode)
        self.pair_canvas.set_background(mode)
        self.config.set("background_mode", mode)
        self._fill_bg_combo()

    def _cycle_background(self) -> None:
        modes = list(BACKGROUND_MODES)
        nxt = modes[(modes.index(self.canvas.background()) + 1) % len(modes)]
        self._set_background(nxt)

    def _on_bg_activated(self, _i: int) -> None:
        self._set_background(self.cb_bg.currentData())

    def _on_zoom_activated(self, _i: int) -> None:
        self.canvas.set_zoom(self.cb_zoom.currentData())

    def _on_canvas_zoom(self, zoom: int) -> None:
        self.cb_zoom.blockSignals(True)
        self.cb_zoom.setCurrentIndex(max(0, self.cb_zoom.findData(zoom)))
        self.cb_zoom.blockSignals(False)
        self.config.set("zoom_percent", int(zoom))
        self._update_status_labels()
        if self.pair is not None and not self._pair_syncing:
            self._pair_syncing = True
            try:
                self.pair_canvas.set_zoom(zoom)
            finally:
                self._pair_syncing = False

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
    #  레이어: 편집 레이어(저장 대상) vs 참조 레이어(읽기 전용)               #
    # ================================================================== #
    def _build_layers_group(self) -> QGroupBox:
        gb, lay = self._group("panel.layers")
        self.lb_layer_count = QLabel()
        self.lb_layer_count.setObjectName("DimLabel")
        self.lb_layer_count.setWordWrap(True)
        lay.addWidget(self.lb_layer_count)

        self.list_layers = QListWidget()
        self.list_layers.setMinimumHeight(150)
        self.list_layers.itemChanged.connect(self._on_layer_item_changed)
        self.list_layers.currentRowChanged.connect(self._on_layer_selected)
        lay.addWidget(self.list_layers)

        self.lb_layer_opacity = QLabel()
        self._t(self.lb_layer_opacity.setText, "layer.opacity")
        lay.addWidget(self.lb_layer_opacity)
        row = QHBoxLayout()
        self.sl_layer_opacity = QSlider(Qt.Horizontal)
        self.sl_layer_opacity.setRange(0, 100)
        self.sl_layer_opacity.valueChanged.connect(self._on_layer_opacity)
        self.lb_layer_opacity_val = QLabel("0%")
        row.addWidget(self.sl_layer_opacity, stretch=1)
        row.addWidget(self.lb_layer_opacity_val)
        lay.addLayout(row)

        self.cb_layer_pos = QComboBox()
        self.cb_layer_pos.activated.connect(self._on_layer_pos)
        lay.addWidget(self.cb_layer_pos)

        brow = QHBoxLayout()
        self.btn_layer_add = QPushButton()
        self._t(self.btn_layer_add.setText, "layer.add")
        self.btn_layer_add.clicked.connect(self._add_reference_dialog)
        self.btn_layer_remove = QPushButton()
        self._t(self.btn_layer_remove.setText, "layer.remove")
        self.btn_layer_remove.clicked.connect(self._remove_selected_layer)
        brow.addWidget(self.btn_layer_add)
        brow.addWidget(self.btn_layer_remove)
        lay.addLayout(brow)

        prow = QHBoxLayout()
        lb_part = QLabel()
        self._t(lb_part.setText, "panel.edit_part")
        prow.addWidget(lb_part)
        self.cb_edit_part = QComboBox()
        for part in PARTS:
            self.cb_edit_part.addItem(part, part)
        self.cb_edit_part.setCurrentIndex(self.cb_edit_part.findData(self.edit_part))
        self.cb_edit_part.activated.connect(self._on_edit_part)
        prow.addWidget(self.cb_edit_part, 1)
        lay.addLayout(prow)
        btn_parts = QToolButton()
        btn_parts.setDefaultAction(self.a_part_auto)
        btn_parts.setToolButtonStyle(Qt.ToolButtonTextOnly)
        btn_parts.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        lay.addWidget(btn_parts)

        self.cb_ref_tint = QCheckBox()
        self._t(self.cb_ref_tint.setText, "layer.tint")
        self.cb_ref_tint.setChecked(bool(self.config.get("reference_tint", True)))
        self.cb_ref_tint.toggled.connect(self._on_ref_tint)
        lay.addWidget(self.cb_ref_tint)

        hint = QLabel()
        hint.setWordWrap(True)
        hint.setObjectName("DimLabel")
        self._t(hint.setText, "layer.hint", max=MAX_REFERENCE_LAYERS)
        lay.addWidget(hint)
        return gb

    def _pair_overlay_layer(self) -> RefLayer | None:
        if self.pair is not None and self.pair_layer is not None \
                and self.config.get("pair.view", pair_mode.VIEW_SIDE) == pair_mode.VIEW_OVERLAY:
            return self.pair_layer
        return None

    def _all_refs(self) -> list[RefLayer]:
        refs = [self.body_layer] if self.body_layer.image is not None else []
        refs = sort_for_paint(refs + self.extra_layers)
        pl = self._pair_overlay_layer()
        return refs + ([pl] if pl is not None else [])

    def _push_layers(self) -> None:
        """레이어 상태를 캔버스·목록·레이어 바에 반영."""
        self.canvas.set_refs(self._all_refs())
        self._rebuild_layer_list()
        self._update_layer_bar()

    def _layer_by_uid(self, uid) -> RefLayer | None:
        if uid == 0:
            return self.body_layer
        if self.pair_layer is not None and uid == self.pair_layer.uid:
            return self.pair_layer
        return next((r for r in self.extra_layers if r.uid == uid), None)

    def _rebuild_layer_list(self, select=None) -> None:
        keep = select if select is not None else self._selected_layer_key()
        lst = self.list_layers
        lst.blockSignals(True)
        lst.clear()
        refs = sort_for_paint([self.body_layer] + self.extra_layers)
        if self._pair_overlay_layer() is not None:
            refs.append(self.pair_layer)
        above = [r for r in refs if r.above and (r.image is not None)]
        below = [r for r in refs if not r.above]
        order: list = [("ref", r) for r in reversed(above)] + [("edit", None)] + \
                     [("ref", r) for r in reversed(below)]
        accent = QColor(canvas_colors(self.config.get("app_theme", "dark"))["edit_outline"])
        for kind, r in order:
            it = QListWidgetItem()
            if kind == "edit":
                m = self.model
                name = m.display_name if m else tr("layer.no_sheet")
                it.setText(tr("layer.row_edit", name=name))
                f = it.font()
                f.setBold(True)
                it.setFont(f)
                bg = QColor(accent)
                bg.setAlpha(60)
                it.setBackground(bg)
                it.setData(Qt.UserRole, "edit")
                it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsUserCheckable)
                it.setCheckState(Qt.Checked if self.canvas.edit_visible() else Qt.Unchecked)
            else:
                pos = tr("layer.pos_above") if r.above else tr("layer.pos_below")
                name = r.name if r.image is not None else tr("panel.body_none")
                it.setText(tr("layer.row_ref", pos=pos, name=name))
                it.setData(Qt.UserRole, r.uid)
                if r.image is None:
                    it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
                else:
                    it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsUserCheckable)
                    it.setCheckState(Qt.Checked if r.visible else Qt.Unchecked)
            lst.addItem(it)
            if keep is not None and it.data(Qt.UserRole) == keep:
                lst.setCurrentItem(it)
        if lst.currentRow() < 0:
            for i in range(lst.count()):
                if lst.item(i).data(Qt.UserRole) == "edit":
                    lst.setCurrentRow(i)
        lst.blockSignals(False)
        n_extra = len(self.extra_layers)
        n_ref = n_extra + (1 if self.body_layer.image is not None else 0)
        self.lb_layer_count.setText(tr("layer.count", n=n_ref, max=MAX_REFERENCE_LAYERS,
                                       extra=n_extra, extra_max=MAX_EXTRA_LAYERS))
        self.btn_layer_add.setEnabled(can_add_extra(n_extra))
        self._sync_layer_controls()

    def _selected_layer_key(self):
        it = self.list_layers.currentItem() if hasattr(self, "list_layers") else None
        return it.data(Qt.UserRole) if it is not None else None

    def _sync_layer_controls(self) -> None:
        key = self._selected_layer_key()
        layer = self._layer_by_uid(key) if key not in (None, "edit") else None
        is_ref = layer is not None and layer.image is not None
        self.sl_layer_opacity.blockSignals(True)
        self.sl_layer_opacity.setValue(layer.opacity if is_ref else 100)
        self.sl_layer_opacity.blockSignals(False)
        self.lb_layer_opacity_val.setText(f"{self.sl_layer_opacity.value()}%")
        for w in (self.sl_layer_opacity, self.lb_layer_opacity, self.lb_layer_opacity_val,
                  self.cb_layer_pos):
            w.setEnabled(is_ref)
        self.cb_layer_pos.blockSignals(True)
        self.cb_layer_pos.clear()
        self.cb_layer_pos.addItem(tr("layer.pos_above_full"), userData=True)
        self.cb_layer_pos.addItem(tr("layer.pos_below_full"), userData=False)
        self.cb_layer_pos.setCurrentIndex(0 if (layer is not None and layer.above) else 1)
        self.cb_layer_pos.blockSignals(False)
        self.btn_layer_remove.setEnabled(layer is not None and layer.kind == KIND_EXTRA)

    def _on_layer_selected(self, _row: int) -> None:
        self._sync_layer_controls()

    def _on_layer_item_changed(self, item: QListWidgetItem) -> None:
        key = item.data(Qt.UserRole)
        on = item.checkState() == Qt.Checked
        if key == "edit":
            self.canvas.set_edit_visible(on)
        else:
            layer = self._layer_by_uid(key)
            if layer is None:
                return
            layer.visible = on
            if layer.kind == KIND_BODY:
                self.config.set("body_visible", bool(on))
            self.canvas.set_refs(self._all_refs())
        self._update_layer_bar()

    def _on_layer_opacity(self, value: int) -> None:
        self.lb_layer_opacity_val.setText(f"{value}%")
        key = self._selected_layer_key()
        layer = self._layer_by_uid(key) if key not in (None, "edit") else None
        if layer is None:
            return
        layer.opacity = int(value)
        if layer.kind == KIND_BODY:
            self.config.set("body_opacity", int(value))
        self.canvas.set_refs(self._all_refs())

    def _on_layer_pos(self, _i: int) -> None:
        key = self._selected_layer_key()
        layer = self._layer_by_uid(key) if key not in (None, "edit") else None
        if layer is None:
            return
        layer.above = bool(self.cb_layer_pos.currentData())
        if layer.kind == KIND_BODY:
            self.config.set("body_above", layer.above)
        self.canvas.set_refs(self._all_refs())
        self._rebuild_layer_list(select=key)

    def _on_ref_tint(self, on: bool) -> None:
        self.config.set("reference_tint", bool(on))
        self.canvas.set_reference_tint(bool(on))

    def _toggle_body(self) -> None:
        if self.body_layer.image is None:
            self._show_message("status.body_hint_pick", {}, "warn")
            return
        self.body_layer.visible = not self.body_layer.visible
        self.config.set("body_visible", bool(self.body_layer.visible))
        self._push_layers()

    def _toggle_edit_visible(self) -> None:
        self.canvas.set_edit_visible(not self.canvas.edit_visible())
        self._rebuild_layer_list()
        self._update_layer_bar()

    def _add_reference_dialog(self) -> None:
        if not can_add_extra(len(self.extra_layers)):
            self._show_message("status.layer_limit", {"max": MAX_REFERENCE_LAYERS}, "warn")
            return
        start = self.config.get("last_open_dir", "") or os.path.expanduser("~")
        paths, _ = QFileDialog.getOpenFileNames(self, tr("layer.add_title"), start, "PNG (*.png)")
        for path in paths:
            self.add_reference_layer(path)

    def add_reference_layer(self, path: str, part: str = "") -> bool:
        """PNG를 참조 레이어로 추가. 한도(Body 포함 6개)를 넘으면 False."""
        if not can_add_extra(len(self.extra_layers)):
            self._show_message("status.layer_limit", {"max": MAX_REFERENCE_LAYERS}, "warn")
            return False
        try:
            arr = export_utils.load_png_rgba(path)
        except Exception:
            self._show_message("status.body_load_failed", {"name": os.path.basename(path)}, "error")
            return False
        h, w = arr.shape[:2]
        img = QImage(arr.data, w, h, w * 4, QImage.Format_RGBA8888).copy()
        self._layer_uid += 1
        label = f"{part}: {os.path.basename(path)}" if part else os.path.basename(path)
        above = part_is_above(part, self.edit_part) if part else True
        layer = RefLayer(name=label, image=img, kind=KIND_EXTRA, path=path,
                         visible=True, opacity=50, above=above, uid=self._layer_uid, part=part)
        self.extra_layers.append(layer)
        self._push_layers()
        self._rebuild_layer_list(select=layer.uid)
        m = self.model
        if m is not None and (w, h) != (m.width, m.height):
            self._show_message("status.body_size_mismatch", {"bw": w, "bh": h, "w": m.width, "h": m.height}, "warn")
        else:
            self._show_message("status.layer_added", {"name": layer.name, "n": len(self._all_refs()),
                                                      "max": MAX_REFERENCE_LAYERS}, "info")
        return True

    def _remove_selected_layer(self) -> None:
        key = self._selected_layer_key()
        layer = self._layer_by_uid(key) if key not in (None, "edit") else None
        if layer is None or layer.kind != KIND_EXTRA:
            return
        self.extra_layers.remove(layer)
        self._push_layers()

    def _update_layer_bar(self) -> None:
        """캔버스 위 한 줄: 지금 편집·저장되는 레이어와, 참조(읽기 전용)로 겹친 레이어를 구분해서 보여 준다."""
        if not hasattr(self, "lb_layerbar"):
            return
        m = self.model
        if m is None:
            self.lb_layerbar.setText(html.escape(tr("layerbar.empty")))
            return
        edit = tr("layerbar.edit", name=f"<b>{html.escape(m.display_name)}</b>")
        if not self.canvas.edit_visible():
            edit += " " + tr("layerbar.edit_hidden")
        refs = [r for r in self._all_refs() if r.visible and r.opacity > 0]
        if refs:
            names = ", ".join(html.escape(r.name) for r in refs)
            ref = tr("layerbar.ref", names=names)
        else:
            ref = tr("layerbar.ref_none")
        self.lb_layerbar.setText(f"{edit}<br>{ref}")

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
        self.body_layer.image = self._body_qimage
        self.body_layer.name = name_text
        self._push_layers()
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

    def _needs_import_dialog(self, arr: np.ndarray, presets: list[dict]) -> bool:
        """크기가 규격과 맞고 투명 배경이 있으면 바로 연다 (설정에서 끄면 항상 팝업)."""
        if not self.config.get("import.skip_when_ready", True):
            return True
        h, w = arr.shape[:2]
        ready = bool(find_matching_presets(presets, w, h)) and import_utils.has_transparency(arr)
        return not ready

    def _run_import_dialog(self, arr: np.ndarray, name: str, presets: list[dict]):
        """가져오기 팝업. 반환: (배열, 규격 dict|None, 처리했는지) / 취소하면 None."""
        dlg = ImportDialog(self, arr, name, presets, self.config)
        if not dlg.exec():
            return None
        if dlg.open_raw or dlg.result_arr is None:
            return arr, None, False
        return dlg.result_arr, dlg.result_preset, True

    def open_file(self, path: str) -> bool:
        if not self._confirm_discard():
            return False
        try:
            arr = export_utils.load_png_rgba(path)
        except Exception as e:
            QMessageBox.critical(self, tr("dlg.error.title"), tr("dlg.open.failed", path=path, error=str(e)))
            self.config.remove_recent("recent_files", path)
            return False

        stem = os.path.splitext(os.path.basename(path))[0]
        presets = all_presets(self.config.get("sheet_presets", []))
        chosen, processed = None, False
        if self._needs_import_dialog(arr, presets):
            res = self._run_import_dialog(arr, os.path.basename(path), presets)
            if res is None:
                return False
            arr, chosen, processed = res

        h, w = arr.shape[:2]
        preset = chosen or pick_preset_for_file(presets, w, h, stem)
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
        model.forced_dirty = processed  # 크기·배경을 바꿨으면 원본과 다르므로 수정됨(*)
        if not processed:
            self._load_sibling_mask(model, path)
        self.config.push_recent("recent_files", path)
        self.config.set("last_open_dir", os.path.dirname(path))
        self._set_model(model)
        if model.has_mask:
            self._show_message("status.opened_with_mask",
                               {"name": os.path.basename(path), "mask": os.path.basename(mc.mask_path_for(path))})
        else:
            self._show_message("status.opened_processed" if processed else "status.opened",
                               {"name": os.path.basename(path), "w": w, "h": h})
        if not model.grid_matches_image():
            self._show_message("status.grid_image_mismatch_hint", {}, "warn")
        return True

    def adjust_current_image(self) -> None:
        """도구 > 크기·배경 조정: 열려 있는 시트에 가져오기 팝업을 다시 적용 (실행 취소 기록은 새로 시작)."""
        m = self.model
        if m is None:
            return
        presets = all_presets(self.config.get("sheet_presets", []))
        res = self._run_import_dialog(m.arr, m.display_name, presets)
        if res is None or not res[2]:
            return
        arr, preset, _ = res
        h, w = arr.shape[:2]
        preset = preset or pick_preset_for_file(presets, w, h, m.name)
        spec, pid = (preset_spec(preset), preset["id"]) if preset else (GridSpec(1, 1, w, h), "custom")
        new = SheetModel(arr, spec, pid, m.name, m.source_path)
        new.save_path, new.gender, new.skin = m.save_path, m.gender, m.skin
        new.forced_dirty = True
        self._set_model(new)
        self._show_message("status.adjusted_mask_dropped" if m.has_mask else "status.adjusted", {"w": w, "h": h},
                           "warn" if m.has_mask else "info")

    def _set_model(self, model: SheetModel, keep_pair: bool = False) -> None:
        if not keep_pair:
            self._clear_pair()
        self._stroke = None
        self.model = model
        self.edit_part = reference_layers.detect_part(model.name)
        apply_part_order(self.extra_layers, self.edit_part)
        self.cb_edit_part.setCurrentIndex(max(0, self.cb_edit_part.findData(self.edit_part)))
        self.body_override = ""
        self.autosaver.clear()  # 이전 문서의 자동 저장본은 필요 없다
        self._last_autosave_marker = None
        self.canvas.set_model(model)
        self.canvas.set_source(CellRect.single(0, 0))
        self.cb_skin.setCurrentIndex(max(0, self.cb_skin.findText(model.skin)))
        self._fill_gender_combo()
        self._fill_preset_combo()
        self._refresh_body()
        self._refresh_mask_view()
        self._update_all()

    def _load_sibling_mask(self, model: SheetModel, path: str) -> None:
        """`X.png`를 열 때 같은 폴더에 `X_M.png`가 있고 크기가 같으면 마스크로 함께 불러온다."""
        if mc.is_mask_name(model.name):
            self._show_message("status.opened_mask_file", {}, "warn")
            return
        mpath = mc.mask_path_for(path)
        if not os.path.isfile(mpath):
            return
        try:
            with Image.open(mpath) as im:
                values = np.array(im.convert("L"), dtype=np.uint8)
        except Exception:
            return
        if values.shape != model.mask.shape:
            self._show_message("status.mask_size_skip", {"name": os.path.basename(mpath)}, "warn")
            return
        index, snapped = mc.png_to_index(values)
        model.set_mask(index)
        model.mask_snapped = snapped

    # ================================================================== #
    #  셀 편집                                                            #
    # ================================================================== #
    def _need_model(self) -> bool:
        return self.model is not None

    def _after_edit(self, label_key: str, fmt: dict | None = None, level: str = "info") -> None:
        self._refresh_mask_view()
        self.canvas.refresh()
        self._update_all()
        self._show_message(label_key, fmt, level)

    def _doc_mask(self) -> np.ndarray | None:
        """편집에 같이 넘길 마스크 (마스크가 있는 시트일 때만)."""
        m = self.model
        return m.mask if (m is not None and m.has_mask) else None

    def copy_cells(self) -> bool:
        src = self.canvas.source() if self._need_model() else None
        if src is None:
            self._show_message("status.no_source", {}, "warn")
            return False
        m = self.model
        self.clipboard = ClipboardData(cell_ops.copy_cells(m.arr, m.grid, src), src.shape,
                                       m.grid.cell_w, m.grid.cell_h, m.gender,
                                       cell_ops.copy_cells(m.mask, m.grid, src) if m.has_mask else None)
        self._update_actions()
        self._show_message("status.copied", {"rows": src.rows, "cols": src.cols})
        return True

    def cut_cells(self) -> None:
        if not self.copy_cells():
            return
        m, src = self.model, self.canvas.source()
        m.history.do(m, cell_ops.make_clear_edit(m.arr, m.grid, src, "edit.cut", self._doc_mask()))
        self._after_edit("status.cut", {"rows": src.rows, "cols": src.cols})

    def clear_cells(self) -> None:
        src = self.canvas.source() if self._need_model() else None
        if src is None:
            self._show_message("status.no_source", {}, "warn")
            return
        m = self.model
        m.history.do(m, cell_ops.make_clear_edit(m.arr, m.grid, src, "edit.clear", self._doc_mask()))
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
        if self.pair is None and clip.gender in (GENDER_FEMALE, GENDER_MALE) \
                and m.gender in (GENDER_FEMALE, GENDER_MALE) and clip.gender != m.gender:
            answer = QMessageBox.warning(
                self, tr("dlg.cross_gender.title"),
                tr("dlg.cross_gender.msg", src=clip.gender, dst=m.gender),
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if answer != QMessageBox.Yes:
                return
        anchor = self._resolve_dest(clip.shape)
        if anchor is None:
            return
        block, mblock = clip.block, clip.mask
        label_key, status_key = "edit.paste", "status.pasted"
        if mirror:
            block = cell_ops.mirror_block(block, m.grid)
            mblock = cell_ops.mirror_block(mblock, m.grid) if mblock is not None else None
            label_key, status_key = "edit.paste_mirror", "status.pasted_mirror"
        if flip_v:
            block = cell_ops.flip_block_vertical(block, m.grid)
            mblock = cell_ops.flip_block_vertical(mblock, m.grid) if mblock is not None else None
            label_key, status_key = "edit.paste_flipv", "status.pasted_flipv"
        m.history.do(m, cell_ops.make_paste_edit(m.arr, m.grid, anchor, block, label_key,
                                                 self._doc_mask(), mblock))
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
        mblock = cell_ops.mirror_block(cell_ops.copy_cells(m.mask, m.grid, src), m.grid) if m.has_mask else None
        m.history.do(m, cell_ops.make_paste_edit(m.arr, m.grid, anchor, block, "edit.mirror_apply",
                                                 self._doc_mask(), mblock))
        self._after_edit("status.mirror_warn", {}, "warn")

    # ================================================================== #
    #  2단계: Nudge / 회색+마스크 / 마스크 보기 / 색 미리보기·비교·통계    #
    # ================================================================== #
    def nudge(self, dx: int, dy: int) -> None:
        """Source 셀(들)의 내용을 각 셀 안에서 (dx, dy) 픽셀 이동 (마스크도 같이)."""
        src = self.canvas.source() if self._need_model() else None
        if src is None:
            self._show_message("status.no_source", {}, "warn")
            return
        m = self.model
        m.history.do(m, cell_ops.make_nudge_edit(m.arr, m.grid, src, dx, dy, "edit.nudge", self._doc_mask()))
        self._after_edit("status.nudged", {"dx": dx, "dy": dy, "rows": src.rows, "cols": src.cols})

    def _replace_sheet_edit(self, rgba: np.ndarray, index: np.ndarray | None, label: str) -> Edit:
        """시트 전체(+마스크)를 바꾸는 실행 취소 가능한 편집."""
        m = self.model
        new_mask = index if index is not None else np.zeros_like(m.mask)
        edit = make_edit(m.arr, 0, 0, rgba, label, m.mask, new_mask)
        edit.has_mask_before = m.has_mask
        edit.has_mask_after = index is not None
        return edit

    def convert_to_gray_mask(self) -> None:
        m = self.model
        if m is None:
            return
        if not m.grid_matches_image():
            self._show_message("status.grid_image_mismatch_hint", {}, "warn")
            return
        dlg = ConvertDialog(self, m.arr.copy(), m.grid, self.config, self.preview_colors.colors(),
                            self.sp_gain.value())
        if not dlg.exec() or dlg.result_gray is None:
            return
        m.history.do(m, self._replace_sheet_edit(dlg.result_gray, dlg.result_index, "edit.convert"))
        m.mask_snapped = 0
        if dlg.result_index is not None and self.config.get("mask_view.mode", MASK_VIEW_OFF) == MASK_VIEW_OFF:
            self._set_mask_view(MASK_VIEW_OVERLAY)  # 결과를 바로 확인할 수 있게 오버레이를 켠다
        self._after_edit("status.converted" if dlg.result_index is not None else "status.converted_gray")

    def remove_mask(self) -> None:
        m = self.model
        if m is None or not m.has_mask:
            return
        edit = make_edit(m.arr, 0, 0, m.arr.copy(), "edit.remove_mask", m.mask, np.zeros_like(m.mask))
        edit.has_mask_before, edit.has_mask_after = True, False
        m.history.do(m, edit)
        self._after_edit("status.mask_removed")

    # -- 마스크 보기 ------------------------------------------------------
    def _fill_mask_view_combo(self) -> None:
        self.cb_mask_view.clear()
        for mode in MASK_VIEW_MODES:
            self.cb_mask_view.addItem(tr(f"maskview.{mode}"), mode)
        cur = self.config.get("mask_view.mode", MASK_VIEW_OFF)
        self.cb_mask_view.setCurrentIndex(max(0, self.cb_mask_view.findData(cur)))
        self.a_missing.blockSignals(True)
        self.a_missing.setChecked(bool(self.config.get("mask_view.missing", False)))
        self.a_missing.blockSignals(False)
        self.cb_missing.setChecked(self.a_missing.isChecked())
        self.a_tint_view.blockSignals(True)
        self.a_tint_view.setChecked(bool(self.config.get("outfit_preview.enabled", False)))
        self.a_tint_view.blockSignals(False)
        self.cb_tint.setChecked(self.a_tint_view.isChecked())

    def _set_mask_view(self, mode: str) -> None:
        self.config.set("mask_view.mode", mode)
        self.cb_mask_view.setCurrentIndex(max(0, self.cb_mask_view.findData(mode)))
        self._refresh_mask_view()

    def _on_mask_view_activated(self, _i: int) -> None:
        self._set_mask_view(self.cb_mask_view.currentData() or MASK_VIEW_OFF)

    def _cycle_mask_view(self) -> None:
        cur = self.config.get("mask_view.mode", MASK_VIEW_OFF)
        i = MASK_VIEW_MODES.index(cur) if cur in MASK_VIEW_MODES else 0
        self._set_mask_view(MASK_VIEW_MODES[(i + 1) % len(MASK_VIEW_MODES)])
        self._show_message("status.mask_view", {"mode": tr(f"maskview.{self.config.get('mask_view.mode')}")})

    def _on_mask_opacity(self, v: int) -> None:
        self.config.set("mask_view.opacity", int(v))
        self._refresh_mask_view()

    def _toggle_missing(self, on: bool) -> None:
        self.config.set("mask_view.missing", bool(on))
        self._refresh_mask_view()

    def _toggle_tint_view(self, on: bool) -> None:
        self.config.set("outfit_preview.enabled", bool(on))
        self._refresh_mask_view()

    def _on_preview_colors(self) -> None:
        self.config.set("outfit_preview.colors", colors_to_config(self.preview_colors.colors()))
        self._refresh_mask_view()

    def _on_gain(self, v: float) -> None:
        self.config.set("outfit_preview.gain", round(float(v), 2))
        self._refresh_mask_view()

    def _build_mask_layers(self, arr: np.ndarray, mask: np.ndarray):
        """(표시 그림, 오버레이) — 시트 전체든 일부 사각형이든 같은 식으로 만든다 (붓질 중 부분 갱신용)."""
        opaque = arr[..., 3] > 0
        mode = self.config.get("mask_view.mode", MASK_VIEW_OFF)
        display = overlay = None
        if mode == MASK_VIEW_ONLY:
            display = mc.pseudo_color(mask, opaque)
        elif self.a_tint_view.isChecked():
            display = mc.tint(arr, mask, self.preview_colors.colors(), self.sp_gain.value())
        if mode == MASK_VIEW_OVERLAY:
            overlay = mc.pseudo_color(mask, opaque)
        if self.a_missing.isChecked():
            miss = mc.missing_overlay(mask, opaque)
            overlay = miss if overlay is None else np.where(miss[..., 3:] > 0, miss, overlay)
        return display, overlay

    def _refresh_mask_view(self) -> None:
        """캔버스 표시 전용 그림(색 미리보기 / 마스크만 / 오버레이 / 누락 강조)을 다시 만든다."""
        if not hasattr(self, "canvas"):
            return
        m = self.model
        if m is None or not m.has_mask:
            self._mv_display = self._mv_overlay = None
            self.canvas.set_mask_view(None, None)
            return
        display, overlay = self._build_mask_layers(m.arr, m.mask)
        mode = self.config.get("mask_view.mode", MASK_VIEW_OFF)
        opacity = 100 if (overlay is not None and mode != MASK_VIEW_OVERLAY) else self.sl_mask_opacity.value()
        self._mv_display = None if display is None else np.ascontiguousarray(display)
        self._mv_overlay = None if overlay is None else np.ascontiguousarray(overlay)
        self.canvas.set_mask_view(self._mv_display, self._mv_overlay, opacity)

    def _patch_mask_view(self, rect) -> None:
        """붓질로 바뀐 사각형만 표시 그림에 반영 (전체를 다시 만들지 않는다). 그림 메모리를 직접 고친다."""
        m = self.model
        if rect is None or m is None or (self._mv_display is None and self._mv_overlay is None):
            return
        y0, y1, x0, x1 = rect
        d, o = self._build_mask_layers(m.arr[y0:y1, x0:x1], m.mask[y0:y1, x0:x1])
        if d is not None and self._mv_display is not None:
            self._mv_display[y0:y1, x0:x1] = d
        if o is not None and self._mv_overlay is not None:
            self._mv_overlay[y0:y1, x0:x1] = o
        self.canvas.viewport().update()

    # -- 비교 / 통계 ------------------------------------------------------
    def _compare_scope(self) -> tuple[np.ndarray, np.ndarray, str]:
        """비교에 쓸 영역: Source 셀(들), 없으면 시트 전체."""
        m = self.model
        src = self.canvas.source()
        if src is not None:
            return (cell_ops.copy_cells(m.arr, m.grid, src), cell_ops.copy_cells(m.mask, m.grid, src),
                    rect_label(src))
        return m.arr.copy(), m.mask.copy(), tr("compare.whole_sheet")

    def open_compare(self) -> None:
        m = self.model
        if m is None or not m.has_mask:
            self._show_message("status.need_mask", {}, "warn")
            return
        gray, index, scope = self._compare_scope()
        CompareDialog(self, gray, index, self.sp_gain.value(), self.config, scope).exec()

    def open_stats(self) -> None:
        m = self.model
        if m is None or not m.has_mask:
            self._show_message("status.need_mask", {}, "warn")
            return
        st = mc.mask_stats(m.mask, m.arr[..., 3], m.grid)
        StatsDialog(self, st, on_highlight=lambda: self.a_missing.setChecked(True)).exec()

    def undo(self) -> None:
        if self.model is None:
            return
        edit = self.model.history.undo(self.model)
        if edit:
            self._after_edit("status.undone", {"label": tr(edit.label)})

    def redo(self) -> None:
        if self.model is None:
            return
        edit = self.model.history.redo(self.model)
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

        # 마스크 저장 전 검증 (§4.3-5) — 알파 임계값을 적용한 최종 알파 기준
        mask_index, mask_notes = None, []
        if m.has_mask:
            mask_index, issues = mc.validate_mask(out, m.mask, m.mask_snapped,
                                                  float(self.config.get("mask_missing_warn_pct", 10)))
            errors = [i for i in issues if i.level == "error"]
            if errors:
                QMessageBox.critical(self, tr("dlg.error.title"),
                                     "\n".join(tr(i.key, **i.fmt) for i in errors))
                return False
            warns = [i for i in issues if i.level == "warn"]
            mask_notes = [tr(i.key, **i.fmt) for i in issues if i.level == "info"]
            if warns:
                st = mc.mask_stats(mask_index, out[..., 3], m.grid)
                lines = ["• " + tr(i.key, **i.fmt) for i in warns]
                if st.inconsistent:
                    lines.append("• " + tr("validate.mask_inconsistent", n=len(st.inconsistent)))
                answer = QMessageBox.warning(
                    self, tr("dlg.mask_check.title"), tr("dlg.mask_check.msg", items="\n".join(lines)),
                    QMessageBox.Save | QMessageBox.Cancel, QMessageBox.Cancel)
                if answer != QMessageBox.Save:
                    return False

        try:
            backup = export_utils.save_png_rgba(out, path)
            if mask_index is not None:
                export_utils.save_png_l(mc.index_to_png(mask_index), mc.mask_path_for(path))
        except Exception as e:
            QMessageBox.critical(self, tr("dlg.error.title"), tr("dlg.save.failed", path=path, error=str(e)))
            return False
        m.mask_snapped = 0

        m.save_path = path
        m.name = os.path.splitext(os.path.basename(path))[0]
        m.mark_saved()
        folder = os.path.dirname(path)
        self.config.push_recent("recent_save_dirs", folder)
        self.autosaver.clear()
        self._fill_save_dir_combo()
        self._update_all()
        if mask_index is not None:
            self._show_message("status.saved_pair", {"path": path,
                                                     "mask": os.path.basename(mc.mask_path_for(path)),
                                                     "notes": (" · " + " · ".join(mask_notes)) if mask_notes else ""})
        else:
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
            "background": self.canvas.background(), "body_opacity": self.body_layer.opacity,
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
        if self.autosaver.save(m.arr, self._autosave_meta(), keep, m.mask if m.has_mask else None) is not None:
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
        try:
            mask = self.autosaver.load_mask(snap)
        except Exception:
            mask = None
        if mask is not None and mask.shape == model.mask.shape:
            model.set_mask(np.clip(mask, 0, 4))
        self._set_model(model)
        bg = meta.get("background")
        if bg in BACKGROUND_MODES:
            self._set_background(bg)
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
