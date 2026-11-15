"""
settings_dialog.py
---------------------
"설정 → 환경설정" (기획서 §6.4 환경설정 항목 표). 모든 값은 바꾸는 즉시 config.json에 저장된다.

탭: 일반(언어·테마·그리드) / 저장 / 시트(규격 프리셋 관리) / 참조(Base Body 폴더) / 자동 저장.
언어를 바꾸면 이 창과 메인 창 문구가 즉시 다시 번역된다 (Translatable).
"""

from __future__ import annotations

import os

from PySide6.QtCore import Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox, QColorDialog, QComboBox, QDialog, QFileDialog, QFormLayout, QHBoxLayout, QLabel,
    QPushButton, QSpinBox, QTabWidget, QVBoxLayout, QWidget, QLineEdit,
)

import export_utils
from config_manager import ConfigManager
from i18n import LANGUAGE_NATIVE_NAMES, SUPPORTED_LANGUAGES, Translatable, set_language, tr
from preset_dialog import PresetManagerDialog
from theme import THEME_LABEL_KEYS
from ui_utils import ask_create_folder, open_folder


class SettingsDialog(QDialog, Translatable):
    languageChanged = Signal(str)
    themeChanged = Signal(str)
    settingsChanged = Signal()  # 저장/그리드/자동 저장/참조 등 그 밖의 변경

    def __init__(self, config: ConfigManager, parent=None):
        super().__init__(parent)
        self.config = config
        self.setMinimumWidth(560)
        self._t(self.setWindowTitle, "dlg.settings.title")
        self._build_ui()

    # ------------------------------------------------------------------ #
    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs)

        self.tabs.addTab(self._build_general(), "")
        self.tabs.addTab(self._build_save(), "")
        self.tabs.addTab(self._build_sheet(), "")
        self.tabs.addTab(self._build_reference(), "")
        self.tabs.addTab(self._build_autosave(), "")
        for i, key in enumerate(("settings.tab.general", "settings.tab.save", "settings.tab.sheet",
                                 "settings.tab.reference", "settings.tab.autosave")):
            self._t(lambda s, i=i: self.tabs.setTabText(i, s), key)

        row = QHBoxLayout()
        row.addStretch(1)
        close = QPushButton()
        self._t(close.setText, "btn.close")
        close.clicked.connect(self.accept)
        row.addWidget(close)
        layout.addLayout(row)

    def _label(self, key: str) -> QLabel:
        lab = QLabel()
        self._t(lab.setText, key)
        return lab

    # -- 일반 ---------------------------------------------------------------
    def _build_general(self) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)

        self.cb_lang = QComboBox()
        for code in SUPPORTED_LANGUAGES:
            self.cb_lang.addItem(LANGUAGE_NATIVE_NAMES[code], userData=code)
        self.cb_lang.setCurrentIndex(max(0, self.cb_lang.findData(self.config.get("language", "ko"))))
        self.cb_lang.currentIndexChanged.connect(self._on_language)
        form.addRow(self._label("settings.language"), self.cb_lang)

        self.cb_theme = QComboBox()
        self._fill_theme_combo()
        self.cb_theme.currentIndexChanged.connect(self._on_theme)
        form.addRow(self._label("settings.theme"), self.cb_theme)

        self.btn_grid_color = QPushButton()
        self.btn_grid_color.clicked.connect(self._pick_grid_color)
        self._paint_grid_color_button()
        form.addRow(self._label("settings.grid_color"), self.btn_grid_color)

        self.sp_grid_width = QSpinBox()
        self.sp_grid_width.setRange(1, 3)
        self.sp_grid_width.setValue(int(self.config.get("grid.width", 1)))
        self.sp_grid_width.valueChanged.connect(self._on_grid_width)
        form.addRow(self._label("settings.grid_width"), self.sp_grid_width)
        return w

    def _fill_theme_combo(self) -> None:
        current = self.config.get("app_theme", "dark")
        self.cb_theme.blockSignals(True)
        self.cb_theme.clear()
        for key, label_key in THEME_LABEL_KEYS.items():
            self.cb_theme.addItem(tr(label_key), userData=key)
        self.cb_theme.setCurrentIndex(max(0, self.cb_theme.findData(current)))
        self.cb_theme.blockSignals(False)

    def _on_language(self, _i: int) -> None:
        lang = self.cb_lang.currentData()
        self.config.set("language", lang)
        set_language(lang)
        self.retranslate_registered()
        self._fill_theme_combo()
        self._fill_rgb_combo()
        self.languageChanged.emit(lang)

    def _on_theme(self, _i: int) -> None:
        theme = self.cb_theme.currentData()
        self.config.set("app_theme", theme)
        self.themeChanged.emit(theme)

    def _paint_grid_color_button(self) -> None:
        color = self.config.get("grid.color", "#00e5ff")
        self.btn_grid_color.setText(color)
        self.btn_grid_color.setStyleSheet(
            f"QPushButton {{ border-left: 18px solid {color}; text-align: left; }}")

    def _pick_grid_color(self) -> None:
        c = QColorDialog.getColor(QColor(self.config.get("grid.color", "#00e5ff")), self)
        if c.isValid():
            self.config.set("grid.color", c.name())
            self._paint_grid_color_button()
            self.settingsChanged.emit()

    def _on_grid_width(self, value: int) -> None:
        self.config.set("grid.width", value)
        self.settingsChanged.emit()

    # -- 저장 ---------------------------------------------------------------
    def _build_save(self) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)

        self.cb_save_dir = QComboBox()
        self.cb_save_dir.setEditable(True)
        self._fill_save_dir_combo()
        self.cb_save_dir.lineEdit().editingFinished.connect(self._commit_save_dir)
        self.cb_save_dir.activated.connect(lambda _i: self._commit_save_dir())
        btn_browse = QPushButton()
        self._t(btn_browse.setText, "btn.browse")
        btn_browse.clicked.connect(self._browse_save_dir)
        btn_default = QPushButton()
        self._t(btn_default.setText, "settings.save_dir_default")
        btn_default.clicked.connect(self._reset_save_dir)
        btn_open = QPushButton()
        self._t(btn_open.setText, "btn.open_folder")
        btn_open.clicked.connect(self._open_save_dir)
        row = QHBoxLayout()
        row.addWidget(self.cb_save_dir, stretch=1)
        for b in (btn_browse, btn_default, btn_open):
            row.addWidget(b)
        form.addRow(self._label("settings.save_dir"), row)

        self.lb_save_dir_hint = QLabel()
        self.lb_save_dir_hint.setObjectName("DimLabel")
        self.lb_save_dir_hint.setWordWrap(True)
        self._t(self.lb_save_dir_hint.setText, "settings.save_dir_hint")
        form.addRow(self.lb_save_dir_hint)

        self.cb_ask = QCheckBox()
        self._t(self.cb_ask.setText, "settings.ask_each_time")
        self.cb_ask.setChecked(bool(self.config.get("ask_save_dir_each_time", False)))
        self.cb_ask.toggled.connect(lambda v: self.config.set("ask_save_dir_each_time", bool(v)))
        form.addRow(self.cb_ask)

        self.cb_alpha = QCheckBox()
        self._t(self.cb_alpha.setText, "settings.alpha_enabled")
        self.cb_alpha.setChecked(bool(self.config.get("alpha_threshold_enabled", True)))
        self.cb_alpha.toggled.connect(self._on_alpha_enabled)
        form.addRow(self.cb_alpha)

        self.sp_alpha = QSpinBox()
        self.sp_alpha.setRange(1, 255)
        self.sp_alpha.setValue(int(self.config.get("alpha_threshold", export_utils.DEFAULT_ALPHA_THRESHOLD)))
        self.sp_alpha.valueChanged.connect(self._on_alpha_value)
        form.addRow(self._label("settings.alpha_value"), self.sp_alpha)

        self.cb_rgb = QComboBox()
        self._fill_rgb_combo()
        self.cb_rgb.currentIndexChanged.connect(self._on_rgb)
        form.addRow(self._label("settings.transparent_rgb"), self.cb_rgb)

        self.cb_meta = QCheckBox()
        self._t(self.cb_meta.setText, "settings.meta_copy")
        self._t(self.cb_meta.setToolTip, "settings.meta_copy_tip")
        self.cb_meta.setChecked(bool(self.config.get("unity_meta_copy", True)))
        self.cb_meta.toggled.connect(lambda v: self.config.set("unity_meta_copy", bool(v)))
        form.addRow(self.cb_meta)
        return w

    def _fill_save_dir_combo(self) -> None:
        self.cb_save_dir.blockSignals(True)
        self.cb_save_dir.clear()
        self.cb_save_dir.addItems(list(self.config.get("recent_save_dirs", [])))
        self.cb_save_dir.setCurrentText(self.config.get("save_dir", ""))
        self.cb_save_dir.lineEdit().setPlaceholderText(tr("settings.save_dir_placeholder"))
        self.cb_save_dir.blockSignals(False)

    def _commit_save_dir(self) -> None:
        path = self.cb_save_dir.currentText().strip()
        if not path:
            self.config.set("save_dir", "")
        else:
            path = os.path.normpath(path)
            if not ask_create_folder(self, path):
                self.cb_save_dir.setCurrentText(self.config.get("save_dir", ""))
                return
            self.config.set("save_dir", path)
            self.config.push_recent("recent_save_dirs", path)
        self._fill_save_dir_combo()
        self.settingsChanged.emit()

    def _browse_save_dir(self) -> None:
        start = self.config.get("save_dir", "") or os.path.expanduser("~")
        folder = QFileDialog.getExistingDirectory(self, tr("dlg.save.pick_folder"), start)
        if folder:
            self.cb_save_dir.setCurrentText(os.path.normpath(folder))
            self._commit_save_dir()

    def _reset_save_dir(self) -> None:
        self.cb_save_dir.setCurrentText("")
        self._commit_save_dir()

    def _open_save_dir(self) -> None:
        folder, _note = export_utils.resolve_save_dir(self.config.get("save_dir", ""))
        open_folder(folder)

    def _on_alpha_enabled(self, v: bool) -> None:
        self.config.set("alpha_threshold_enabled", bool(v))
        self.settingsChanged.emit()

    def _on_alpha_value(self, v: int) -> None:
        self.config.set("alpha_threshold", int(v))
        self.settingsChanged.emit()

    def _fill_rgb_combo(self) -> None:
        current = self.config.get("transparent_rgb", export_utils.TRANSPARENT_BLACK)
        self.cb_rgb.blockSignals(True)
        self.cb_rgb.clear()
        self.cb_rgb.addItem(tr("settings.rgb_black"), userData=export_utils.TRANSPARENT_BLACK)
        self.cb_rgb.addItem(tr("settings.rgb_extend"), userData=export_utils.TRANSPARENT_EXTEND)
        self.cb_rgb.setCurrentIndex(max(0, self.cb_rgb.findData(current)))
        self.cb_rgb.blockSignals(False)

    def _on_rgb(self, _i: int) -> None:
        self.config.set("transparent_rgb", self.cb_rgb.currentData())
        self.settingsChanged.emit()

    # -- 시트 ---------------------------------------------------------------
    def _build_sheet(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        info = QLabel()
        info.setWordWrap(True)
        info.setObjectName("DimLabel")
        self._t(info.setText, "settings.presets_info")
        layout.addWidget(info)
        btn = QPushButton()
        self._t(btn.setText, "settings.presets_manage")
        btn.clicked.connect(self._open_presets)
        layout.addWidget(btn)
        layout.addStretch(1)
        return w

    def _open_presets(self) -> None:
        dlg = PresetManagerDialog(self.config, self)
        dlg.presetsChanged.connect(self.settingsChanged)
        dlg.exec()

    # -- 참조 ---------------------------------------------------------------
    def _build_reference(self) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)
        self.ed_body_dir = QLineEdit(self.config.get("body_dir", ""))
        self.ed_body_dir.editingFinished.connect(self._commit_body_dir)
        btn = QPushButton()
        self._t(btn.setText, "btn.browse")
        btn.clicked.connect(self._browse_body_dir)
        row = QHBoxLayout()
        row.addWidget(self.ed_body_dir, stretch=1)
        row.addWidget(btn)
        form.addRow(self._label("settings.body_dir"), row)
        hint = QLabel()
        hint.setWordWrap(True)
        hint.setObjectName("DimLabel")
        self._t(hint.setText, "settings.body_dir_hint")
        form.addRow(hint)
        # 3단계: 파츠(Eyes/Hair/…) 참조를 찾을 Allies 폴더, 쌍 작업 겹쳐보기 불투명도
        self.ed_allies_dir = QLineEdit(self.config.get("allies_dir", ""))
        self.ed_allies_dir.editingFinished.connect(self._commit_allies_dir)
        btn2 = QPushButton()
        self._t(btn2.setText, "btn.browse")
        btn2.clicked.connect(self._browse_allies_dir)
        row2 = QHBoxLayout()
        row2.addWidget(self.ed_allies_dir, stretch=1)
        row2.addWidget(btn2)
        form.addRow(self._label("settings.allies_dir"), row2)
        hint2 = QLabel()
        hint2.setWordWrap(True)
        hint2.setObjectName("DimLabel")
        self._t(hint2.setText, "settings.allies_dir_hint")
        form.addRow(hint2)
        self.sp_pair_opacity = QSpinBox()
        self.sp_pair_opacity.setRange(5, 100)
        self.sp_pair_opacity.setSuffix(" %")
        self.sp_pair_opacity.setValue(int(self.config.get("pair.overlay_opacity", 40)))
        self.sp_pair_opacity.valueChanged.connect(lambda v: self.config.set("pair.overlay_opacity", int(v)))
        form.addRow(self._label("settings.pair_opacity"), self.sp_pair_opacity)

        # 4단계: 셀 편집 창 — 이웃 셀 반투명 표시·불투명도·기본 줌 (§6.4 추가 8)
        self.cb_cell_neighbors = QCheckBox()
        self._t(self.cb_cell_neighbors.setText, "settings.cell_neighbors")
        self.cb_cell_neighbors.setChecked(bool(self.config.get("cell_edit.neighbors", True)))
        self.cb_cell_neighbors.toggled.connect(lambda v: self.config.set("cell_edit.neighbors", bool(v)))
        form.addRow(self._label("settings.cell_edit"), self.cb_cell_neighbors)
        self.sp_cell_opacity = QSpinBox()
        self.sp_cell_opacity.setRange(10, 90)
        self.sp_cell_opacity.setSuffix(" %")
        self.sp_cell_opacity.setValue(int(self.config.get("cell_edit.neighbor_opacity", 40)))
        self.sp_cell_opacity.valueChanged.connect(lambda v: self.config.set("cell_edit.neighbor_opacity", int(v)))
        form.addRow(self._label("settings.cell_neighbor_opacity"), self.sp_cell_opacity)
        self.cb_cell_zoom = QComboBox()
        for z in (400, 600, 800, 1000, 1200, 1600):
            self.cb_cell_zoom.addItem(f"{z}%", z)
        cur = int(self.config.get("cell_edit.zoom", 800))
        self.cb_cell_zoom.setCurrentIndex(max(0, self.cb_cell_zoom.findData(cur)))
        self.cb_cell_zoom.activated.connect(
            lambda _i: self.config.set("cell_edit.zoom", int(self.cb_cell_zoom.currentData())))
        form.addRow(self._label("settings.cell_zoom"), self.cb_cell_zoom)
        return w

    def _commit_allies_dir(self) -> None:
        self.config.set("allies_dir", self.ed_allies_dir.text().strip())
        self.settingsChanged.emit()

    def _browse_allies_dir(self) -> None:
        start = self.ed_allies_dir.text().strip() or os.path.expanduser("~")
        folder = QFileDialog.getExistingDirectory(self, tr("settings.allies_dir"), start)
        if folder:
            self.ed_allies_dir.setText(os.path.normpath(folder))
            self._commit_allies_dir()

    def _commit_body_dir(self) -> None:
        self.config.set("body_dir", self.ed_body_dir.text().strip())
        self.settingsChanged.emit()

    def _browse_body_dir(self) -> None:
        start = self.ed_body_dir.text().strip() or os.path.expanduser("~")
        folder = QFileDialog.getExistingDirectory(self, tr("settings.body_dir"), start)
        if folder:
            self.ed_body_dir.setText(os.path.normpath(folder))
            self._commit_body_dir()

    # -- 자동 저장 ------------------------------------------------------------
    def _build_autosave(self) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)
        self.cb_auto = QCheckBox()
        self._t(self.cb_auto.setText, "settings.autosave_enabled")
        self.cb_auto.setChecked(bool(self.config.get("autosave.enabled", True)))
        self.cb_auto.toggled.connect(lambda v: self._set_autosave("enabled", bool(v)))
        form.addRow(self.cb_auto)

        self.sp_interval = QSpinBox()
        self.sp_interval.setRange(1, 60)
        self.sp_interval.setValue(int(self.config.get("autosave.interval_min", 10)))
        self.sp_interval.valueChanged.connect(lambda v: self._set_autosave("interval_min", int(v)))
        form.addRow(self._label("settings.autosave_interval"), self.sp_interval)

        self.sp_keep = QSpinBox()
        self.sp_keep.setRange(1, 20)
        self.sp_keep.setValue(int(self.config.get("autosave.keep", 2)))
        self.sp_keep.valueChanged.connect(lambda v: self._set_autosave("keep", int(v)))
        form.addRow(self._label("settings.autosave_keep"), self.sp_keep)
        return w

    def _set_autosave(self, key: str, value) -> None:
        self.config.set(f"autosave.{key}", value)
        self.settingsChanged.emit()
