"""
batch_dialog.py
-----------------
`도구 → 폴더 일괄 변환…` 대화상자 (기획서 §9 4단계 — 배치 변환). 변환 자체는 batch_convert.

입력 폴더의 PNG 목록(체크로 고르기) → 변환 설정 → `변환 시작` → 결과(성공·경고·건너뜀)를 표에 표시.
결과 줄을 더블클릭하면 그 파일을 메인 창에서 열어 마스크 보정을 이어 간다 (옷 여러 벌 연속 처리).
"""

from __future__ import annotations

import os

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication, QButtonGroup, QCheckBox, QComboBox, QDialog, QFileDialog, QFormLayout, QGroupBox,
    QHBoxLayout, QLabel, QLineEdit, QMessageBox, QProgressBar, QPushButton, QRadioButton, QSpinBox,
    QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

import batch_convert as bc
import export_utils
import mask_convert as mc
from config_manager import ConfigManager
from convert_dialog import index_combo
from i18n import tr
from sheet_model import pick_preset_for_file
from ui_utils import ask_create_folder, open_folder

COL_FILE, COL_SPEC, COL_RESULT = 0, 1, 2


class BatchConvertDialog(QDialog):
    def __init__(self, parent, config: ConfigManager, presets: list[dict], start_dir: str = ""):
        super().__init__(parent)
        self.config = config
        self.presets = presets
        self.open_path = ""  # 더블클릭으로 열 파일 (메인 창이 연다)
        self.results: list[bc.BatchResult] = []
        self.setWindowTitle(tr("dlg.batch.title"))
        self.resize(1080, 700)

        root = QVBoxLayout(self)
        form = QFormLayout()
        self.ed_in = QLineEdit(config.get("batch_convert.in_dir", "") or start_dir)
        b_in = QPushButton(tr("btn.browse"))
        b_in.clicked.connect(lambda: self._browse(self.ed_in))
        r1 = QHBoxLayout()
        r1.addWidget(self.ed_in, 1)
        r1.addWidget(b_in)
        form.addRow(tr("dlg.batch.in_dir"), r1)

        out_default, _note = export_utils.resolve_save_dir(config.get("save_dir", ""))
        self.ed_out = QLineEdit(config.get("batch_convert.out_dir", "") or out_default)
        b_out = QPushButton(tr("btn.browse"))
        b_out.clicked.connect(lambda: self._browse(self.ed_out))
        b_open = QPushButton(tr("btn.open_folder"))
        b_open.clicked.connect(lambda: open_folder(self.ed_out.text().strip()))
        r2 = QHBoxLayout()
        r2.addWidget(self.ed_out, 1)
        r2.addWidget(b_out)
        r2.addWidget(b_open)
        form.addRow(tr("dlg.batch.out_dir"), r2)
        root.addLayout(form)

        mid = QHBoxLayout()
        root.addLayout(mid, 1)

        # 파일 목록
        left = QVBoxLayout()
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels([tr("dlg.batch.col_file"), tr("dlg.batch.col_spec"), tr("dlg.batch.col_result")])
        self.tree.setRootIsDecorated(False)
        self.tree.setColumnWidth(COL_FILE, 200)
        self.tree.setColumnWidth(COL_SPEC, 70)
        self.tree.itemDoubleClicked.connect(self._on_double)
        left.addWidget(self.tree, 1)
        sel = QHBoxLayout()
        b_all = QPushButton(tr("dlg.batch.check_all"))
        b_none = QPushButton(tr("dlg.batch.check_none"))
        b_all.clicked.connect(lambda: self._check_all(True))
        b_none.clicked.connect(lambda: self._check_all(False))
        self.lb_count = QLabel()
        self.lb_count.setObjectName("DimLabel")
        for w in (b_all, b_none):
            sel.addWidget(w)
        sel.addWidget(self.lb_count, 1)
        left.addLayout(sel)
        mid.addLayout(left, 1)

        # 설정
        panel = QWidget()
        panel.setFixedWidth(400)
        right = QVBoxLayout(panel)
        right.setContentsMargins(0, 0, 0, 0)
        mid.addWidget(panel)

        g_gray = QGroupBox(tr("dlg.convert.group_gray"))
        fg = QFormLayout(g_gray)
        self.cb_norm = QComboBox()
        for v in (bc.NORM_AUTO, bc.NORM_ON, bc.NORM_OFF):
            self.cb_norm.addItem(tr(f"dlg.batch.norm_{v}"), v)
        self.cb_norm.setCurrentIndex(max(0, self.cb_norm.findData(config.get("batch_convert.normalize", bc.NORM_AUTO))))
        fg.addRow(tr("dlg.batch.normalize"), self.cb_norm)
        right.addWidget(g_gray)

        g_mask = QGroupBox(tr("dlg.convert.group_mask"))
        lm = QVBoxLayout(g_mask)
        self.rb_color = QRadioButton(tr("dlg.convert.mode_color"))
        self.rb_hsplit = QRadioButton(tr("dlg.convert.mode_hsplit"))
        self.rb_none = QRadioButton(tr("dlg.convert.mode_none"))
        self.mode_group = QButtonGroup(self)
        fc = QFormLayout()
        fc.setContentsMargins(18, 0, 0, 0)
        self.sp_k = QSpinBox()
        self.sp_k.setRange(1, mc.REGION_COUNT)
        self.sp_k.setValue(int(config.get("convert.regions", 2)))
        fc.addRow(tr("dlg.convert.regions"), self.sp_k)
        self.sp_lw = QSpinBox()
        self.sp_lw.setRange(0, 100)
        self.sp_lw.setSuffix(" %")
        self.sp_lw.setValue(int(config.get("convert.l_weight", 50)))
        fc.addRow(tr("dlg.convert.l_weight"), self.sp_lw)
        fh = QFormLayout()
        fh.setContentsMargins(18, 0, 0, 0)
        self.sp_y = QSpinBox()
        self.sp_y.setRange(0, 1024)
        self.sp_y.setSuffix(" px")
        self.sp_y.setSpecialValueText(tr("dlg.batch.hsplit_half"))
        self.sp_y.setValue(int(config.get("convert.hsplit_y", 0) or 0))
        fh.addRow(tr("dlg.batch.hsplit_y"), self.sp_y)
        self.cb_upper = index_combo(1)
        self.cb_lower = index_combo(2)
        fh.addRow(tr("dlg.convert.hsplit_upper"), self.cb_upper)
        fh.addRow(tr("dlg.convert.hsplit_lower"), self.cb_lower)
        for rb, sub in ((self.rb_color, fc), (self.rb_hsplit, fh), (self.rb_none, None)):
            self.mode_group.addButton(rb)
            lm.addWidget(rb)
            if sub is not None:
                lm.addLayout(sub)
        mode = config.get("convert.mode", bc.MODE_COLOR)
        {bc.MODE_HSPLIT: self.rb_hsplit, bc.MODE_NONE: self.rb_none}.get(mode, self.rb_color).setChecked(True)
        self.chk_speck = QCheckBox(tr("dlg.convert.despeckle"))
        self.chk_speck.setChecked(bool(config.get("convert.despeckle", True)))
        lm.addWidget(self.chk_speck)
        right.addWidget(g_mask)

        self.chk_meta = QCheckBox(tr("settings.meta_copy"))
        self.chk_meta.setChecked(bool(config.get("unity_meta_copy", True)))
        self.chk_meta.setToolTip(tr("settings.meta_copy_tip"))
        right.addWidget(self.chk_meta)
        note = QLabel(tr("dlg.batch.note"))
        note.setWordWrap(True)
        note.setObjectName("DimLabel")
        right.addWidget(note)
        right.addStretch(1)

        self.progress = QProgressBar()
        self.progress.setVisible(False)
        root.addWidget(self.progress)
        self.lb_summary = QLabel()
        self.lb_summary.setWordWrap(True)
        root.addWidget(self.lb_summary)

        btns = QHBoxLayout()
        btns.addStretch(1)
        self.btn_run = QPushButton(tr("dlg.batch.run"))
        self.btn_run.setDefault(True)
        self.btn_run.clicked.connect(self.run)
        b_close = QPushButton(tr("btn.close"))
        b_close.clicked.connect(self.reject)
        btns.addWidget(self.btn_run)
        btns.addWidget(b_close)
        root.addLayout(btns)

        self.ed_in.editingFinished.connect(self.refresh_list)
        self.tree.itemChanged.connect(lambda *_: self._update_count())
        self.refresh_list()

    # ------------------------------------------------------------------ #
    def _browse(self, edit: QLineEdit) -> None:
        start = edit.text().strip() or os.path.expanduser("~")
        folder = QFileDialog.getExistingDirectory(self, tr("dlg.save.pick_folder"), start)
        if folder:
            edit.setText(os.path.normpath(folder))
            if edit is self.ed_in:
                self.refresh_list()

    def refresh_list(self) -> None:
        self.tree.blockSignals(True)
        self.tree.clear()
        for path in bc.list_sources(self.ed_in.text().strip()):
            item = QTreeWidgetItem([os.path.basename(path), "", ""])
            item.setData(COL_FILE, Qt.UserRole, path)
            size = bc.unity_meta.png_size(path)
            preset = pick_preset_for_file(self.presets, *size, os.path.basename(path)) if size else None
            if preset is None:
                item.setText(COL_SPEC, f"{size[0]}×{size[1]}" if size else "?")
                item.setText(COL_RESULT, tr("dlg.batch.no_preset"))
                item.setFlags(item.flags() & ~Qt.ItemIsUserCheckable)
            else:
                item.setText(COL_SPEC, preset["id"] if preset.get("builtin") else (preset.get("name") or "?"))
                item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
                item.setCheckState(COL_FILE, Qt.Checked)
            self.tree.addTopLevelItem(item)
        self.tree.blockSignals(False)
        self._update_count()

    def _items(self) -> list[QTreeWidgetItem]:
        return [self.tree.topLevelItem(i) for i in range(self.tree.topLevelItemCount())]

    def _checked(self) -> list[QTreeWidgetItem]:
        return [it for it in self._items()
                if it.flags() & Qt.ItemIsUserCheckable and it.checkState(COL_FILE) == Qt.Checked]

    def _check_all(self, on: bool) -> None:
        for it in self._items():
            if it.flags() & Qt.ItemIsUserCheckable:
                it.setCheckState(COL_FILE, Qt.Checked if on else Qt.Unchecked)

    def _update_count(self) -> None:
        self.lb_count.setText(tr("dlg.batch.count", n=len(self._checked()), total=self.tree.topLevelItemCount()))
        self.btn_run.setEnabled(bool(self._checked()))

    def options(self) -> bc.BatchOptions:
        cfg = self.config
        mode = bc.MODE_HSPLIT if self.rb_hsplit.isChecked() else bc.MODE_NONE if self.rb_none.isChecked() else bc.MODE_COLOR
        return bc.BatchOptions(
            out_dir=os.path.normpath(self.ed_out.text().strip()), mode=mode, regions=self.sp_k.value(),
            l_weight=self.sp_lw.value(), hsplit_y=self.sp_y.value(), upper=self.cb_upper.currentData(),
            lower=self.cb_lower.currentData(), despeckle=self.chk_speck.isChecked(),
            normalize=self.cb_norm.currentData(),
            alpha_enabled=bool(cfg.get("alpha_threshold_enabled", True)),
            alpha_threshold=int(cfg.get("alpha_threshold", export_utils.DEFAULT_ALPHA_THRESHOLD)),
            transparent_rgb=cfg.get("transparent_rgb", export_utils.TRANSPARENT_BLACK),
            missing_warn_pct=float(cfg.get("mask_missing_warn_pct", 10)),
            meta_copy=self.chk_meta.isChecked())

    def run(self, confirm: bool = True) -> list[bc.BatchResult]:
        items = self._checked()
        in_dir = self.ed_in.text().strip()
        out_dir = self.ed_out.text().strip()
        if not items or not out_dir:
            return []
        if bc.same_folder(in_dir, out_dir):
            QMessageBox.warning(self, tr("dlg.batch.title"), tr("dlg.batch.same_folder"))
            return []
        if confirm and not ask_create_folder(self, os.path.normpath(out_dir)):
            return []
        opts = self.options()
        cfg = self.config
        cfg.set("batch_convert.in_dir", os.path.normpath(in_dir))
        cfg.set("batch_convert.out_dir", opts.out_dir)
        cfg.set("batch_convert.normalize", opts.normalize)

        self.results = []
        self.progress.setRange(0, len(items))
        self.progress.setValue(0)
        self.progress.setVisible(True)
        self.btn_run.setEnabled(False)
        for n, it in enumerate(items, 1):
            it.setText(COL_RESULT, tr("dlg.batch.working"))
            QApplication.processEvents()
            res = bc.convert_file(it.data(COL_FILE, Qt.UserRole), self.presets, opts)
            self.results.append(res)
            self._show_result(it, res)
            self.progress.setValue(n)
            QApplication.processEvents()
        self.btn_run.setEnabled(True)
        counts = {s: sum(1 for r in self.results if r.status == s) for s in (bc.ST_OK, bc.ST_WARN, bc.ST_SKIP, bc.ST_ERROR)}
        self.lb_summary.setText(tr("dlg.batch.summary", ok=counts[bc.ST_OK], warn=counts[bc.ST_WARN],
                                   skip=counts[bc.ST_SKIP], error=counts[bc.ST_ERROR], folder=opts.out_dir))
        cfg.push_recent("recent_save_dirs", opts.out_dir)
        return self.results

    def _show_result(self, it: QTreeWidgetItem, res: bc.BatchResult) -> None:
        notes = [tr(n.key, **n.fmt) for n in res.notes]
        head = tr(f"dlg.batch.st_{res.status}")
        if res.mask_path:
            head += " · " + tr("dlg.batch.with_mask")
        it.setText(COL_RESULT, head + ((" — " + notes[0]) if notes else ""))
        it.setToolTip(COL_RESULT, "\n".join([head] + notes))
        it.setData(COL_RESULT, Qt.UserRole, res.out_path if res.status in (bc.ST_OK, bc.ST_WARN) else "")

    def _on_double(self, it: QTreeWidgetItem, _col: int) -> None:
        path = it.data(COL_RESULT, Qt.UserRole)
        if path and os.path.exists(path):
            self.open_path = path
            self.accept()
