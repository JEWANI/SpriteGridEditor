"""
save_dialog.py
----------------
저장 대화상자 (기획서 §3.1 파일명 도우미 / §4.4 저장 폴더).

- 저장 폴더: 직접 지정 + 최근 사용 폴더 목록(최대 10개), `폴더 열기`, 없는 경로 입력 시 생성 확인.
- 파일명 도우미: `종류(U/P/G/N/S) + 번호 3자리 + 체형(F/M)` 으로 ``H1WK_{종류}{번호}{체형}`` 조립 (CHR-ART.5 §3.2).
- 파일명 규칙·체형 접미사 불일치는 **경고만** (저장은 허용).
- 같은 이름이 있으면 덮어쓰기 확인 (이전 파일은 `_backup.png` 로 보관됨).
"""

from __future__ import annotations

import os
import re

from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QGroupBox,
    QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton, QSpinBox, QVBoxLayout,
)

import export_utils
from config_manager import ConfigManager
from i18n import tr
from sheet_model import SheetModel
from ui_utils import ask_create_folder, open_folder

_INVALID_CHARS = re.compile(r'[\\/:*?"<>|]')


class SaveDialog(QDialog):
    def __init__(self, config: ConfigManager, model: SheetModel, prefix_hint: str, parent=None):
        super().__init__(parent)
        self.config = config
        self.model = model
        self.setWindowTitle(tr("dlg.save.title"))
        self.setMinimumWidth(520)
        self._result_path = ""

        layout = QVBoxLayout(self)
        form = QFormLayout()

        # 저장 폴더
        default_dir, note = export_utils.resolve_save_dir(config.get("save_dir", ""))
        folders = []
        for f in [default_dir] + list(config.get("recent_save_dirs", [])):
            if f and os.path.normpath(f) not in [os.path.normpath(x) for x in folders]:
                folders.append(f)
        self.cb_folder = QComboBox()
        self.cb_folder.setEditable(True)
        self.cb_folder.addItems(folders)
        self.cb_folder.setCurrentText(default_dir)
        btn_browse = QPushButton(tr("btn.browse"))
        btn_browse.clicked.connect(self._browse)
        btn_open = QPushButton(tr("btn.open_folder"))
        btn_open.clicked.connect(lambda: open_folder(self.cb_folder.currentText().strip()))
        row = QHBoxLayout()
        row.addWidget(self.cb_folder, stretch=1)
        row.addWidget(btn_browse)
        row.addWidget(btn_open)
        form.addRow(tr("dlg.save.folder"), row)

        # 파일명
        self.ed_name = QLineEdit(model.name or "")
        row2 = QHBoxLayout()
        row2.addWidget(self.ed_name, stretch=1)
        row2.addWidget(QLabel(".png"))
        form.addRow(tr("dlg.save.filename"), row2)
        layout.addLayout(form)

        if note:
            lab = QLabel(tr(f"note.{note}"))
            lab.setObjectName("WarnLabel")
            lab.setWordWrap(True)
            layout.addWidget(lab)

        # 파일명 도우미
        self.gb_helper = QGroupBox(tr("dlg.save.helper"))
        self.gb_helper.setCheckable(True)
        parsed = export_utils.parse_name(model.name or "")
        self.gb_helper.setChecked(parsed is None)
        hform = QHBoxLayout()
        self.ed_prefix = QLineEdit(parsed[0] if parsed else prefix_hint)
        self.ed_prefix.setMaximumWidth(110)
        self.cb_kind = QComboBox()
        for k in export_utils.NAME_KINDS:  # 글자만 보이면 뜻을 알 수 없으므로 "U — 속옷"처럼 설명을 붙인다
            self.cb_kind.addItem(tr(f"dlg.save.kind_{k}"), userData=k)
        if parsed and parsed[1] in export_utils.NAME_KINDS:
            self.cb_kind.setCurrentIndex(max(0, self.cb_kind.findData(parsed[1])))
        self.cb_kind.setToolTip(tr("dlg.save.kind_tip"))
        self.sp_num = QSpinBox()
        self.sp_num.setRange(0, 999)
        self.sp_num.setValue(parsed[2] if parsed else 1)
        self.lb_gender = QLabel()
        for w in (QLabel(tr("dlg.save.prefix")), self.ed_prefix, QLabel(tr("dlg.save.kind")),
                  self.cb_kind, QLabel(tr("dlg.save.number")), self.sp_num, self.lb_gender):
            hform.addWidget(w)
        hform.addStretch(1)
        self.lb_helper_desc = QLabel()
        self.lb_helper_desc.setObjectName("DimLabel")
        self.lb_helper_desc.setWordWrap(True)
        hv = QVBoxLayout(self.gb_helper)
        hv.addLayout(hform)
        hv.addWidget(self.lb_helper_desc)
        layout.addWidget(self.gb_helper)

        self.lb_warn = QLabel()
        self.lb_warn.setObjectName("WarnLabel")
        self.lb_warn.setWordWrap(True)
        layout.addWidget(self.lb_warn)

        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.buttons.accepted.connect(self._accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

        self.ed_prefix.textChanged.connect(self._compose)
        self.cb_kind.currentIndexChanged.connect(self._compose)
        self.sp_num.valueChanged.connect(self._compose)
        self.gb_helper.toggled.connect(self._compose)
        self.ed_name.textChanged.connect(self._validate)
        self._compose()

    # -- 이름 조립 / 검증 ------------------------------------------------------
    def _compose(self, *_args) -> None:
        g = self.model.gender
        self.lb_gender.setText(tr("dlg.save.gender_fmt", gender={"F": "F", "M": "M"}.get(g, tr("gender.none"))))
        if self.gb_helper.isChecked():
            name = export_utils.build_name(self.ed_prefix.text().strip() or "H1WK",
                                           self.cb_kind.currentData(), self.sp_num.value(), g)
            self.ed_name.setText(name)
        kind = self.cb_kind.currentData() or ""
        self.lb_helper_desc.setText(tr("dlg.save.helper_desc", prefix=self.ed_prefix.text().strip() or "H1WK",
                                       kind=kind, kind_name=tr(f"dlg.save.kind_{kind}") if kind else "",
                                       number=self.sp_num.value(), gender=tr(f"dlg.save.gender_name_{g if g in ('F', 'M') else 'C'}")))
        self._validate()

    def _clean_name(self) -> str:
        name = self.ed_name.text().strip()
        if name.lower().endswith(".png"):
            name = name[:-4]
        return _INVALID_CHARS.sub("", name).strip()

    def _validate(self, *_args) -> None:
        name = self._clean_name()
        issues = export_utils.validate_name(name, self.model.gender) if name else []
        self.lb_warn.setText("\n".join("⚠ " + tr(i.key, **i.fmt) for i in issues))
        self.buttons.button(QDialogButtonBox.Ok).setEnabled(bool(name))

    # -- 동작 -------------------------------------------------------------------
    def _browse(self) -> None:
        start = self.cb_folder.currentText().strip() or os.path.expanduser("~")
        folder = QFileDialog.getExistingDirectory(self, tr("dlg.save.pick_folder"), start)
        if folder:
            self.cb_folder.setCurrentText(os.path.normpath(folder))

    def _accept(self) -> None:
        folder = self.cb_folder.currentText().strip()
        name = self._clean_name()
        if not folder or not name:
            return
        folder = os.path.normpath(folder)
        if not ask_create_folder(self, folder):
            return
        path = os.path.join(folder, name + ".png")
        if os.path.exists(path) and os.path.normcase(path) != os.path.normcase(self.model.save_path or ""):
            answer = QMessageBox.question(self, tr("dlg.overwrite.title"),
                                          tr("dlg.overwrite.msg", name=name + ".png"),
                                          QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if answer != QMessageBox.Yes:
                return
        self._result_path = path
        self.accept()

    def result_path(self) -> str:
        return self._result_path
