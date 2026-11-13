"""
preset_dialog.py
------------------
시트 규격 프리셋 관련 다이얼로그 (기획서 §3).

- SheetSpecDialog   : `임의 설정…` / 프리셋 추가·편집. 입력 방식 2가지
                      1) 시트 크기(px) + 열·행 수 → 셀 크기 자동 계산
                      2) 셀 크기(px) + 열·행 수 → 시트 크기 자동 계산
                      시트 크기가 열·행 수로 나누어떨어지지 않으면 경고하고 확정을 막는다.
- PresetManagerDialog: 프리셋 목록 관리 (사용자 프리셋 추가·수정·삭제·복제, 기본 H1·H2는 삭제 불가).
UI에는 열/행을 글자로 표시한다 (`열(가로 칸)` / `행(세로 칸)`).
"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QDialogButtonBox, QFormLayout, QGridLayout, QHBoxLayout, QLabel,
    QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QPushButton, QRadioButton,
    QSpinBox, QVBoxLayout,
)

from config_manager import ConfigManager
from i18n import tr
from sheet_model import (
    GridSpec, all_presets, make_user_preset, preset_spec, spec_from_cell_size,
    spec_from_sheet_size,
)


def preset_display_name(preset: dict) -> str:
    return tr(preset["name_key"]) if preset.get("name_key") else str(preset.get("name", ""))


def preset_list_label(preset: dict) -> str:
    """`H1 걷기 — 3열×4행 (셀 100×135)` 형식 (§3 열·행 표시 확정)."""
    return tr("preset.list_fmt", name=preset_display_name(preset), cols=preset["cols"],
              rows=preset["rows"], cw=preset["cell_w"], ch=preset["cell_h"])


def grid_text(spec: GridSpec) -> str:
    return tr("grid.text", cols=spec.cols, rows=spec.rows, cw=spec.cell_w, ch=spec.cell_h)


def _spin(lo: int, hi: int, value: int) -> QSpinBox:
    s = QSpinBox()
    s.setRange(lo, hi)
    s.setValue(value)
    return s


class SheetSpecDialog(QDialog):
    def __init__(self, parent=None, *, spec: GridSpec | None = None,
                 sheet_size: tuple[int, int] | None = None, name: str = "",
                 body_motion: str = "", editing_preset: bool = False):
        """spec: 초기값(없으면 sheet_size로 추정). editing_preset=True면 프리셋 편집(이름 필수)."""
        super().__init__(parent)
        self.setWindowTitle(tr("dlg.spec.title_edit" if editing_preset else "dlg.spec.title"))
        self.setMinimumWidth(420)
        self._editing_preset = editing_preset

        layout = QVBoxLayout(self)

        self.rb_sheet = QRadioButton(tr("dlg.spec.mode_sheet"))
        self.rb_cell = QRadioButton(tr("dlg.spec.mode_cell"))
        self.rb_sheet.setChecked(True)
        layout.addWidget(self.rb_sheet)
        layout.addWidget(self.rb_cell)

        init_w, init_h = (sheet_size or (300, 540))
        cols, rows = 3, 4
        cell_w, cell_h = 100, 135
        if spec is not None:
            cols, rows, cell_w, cell_h = spec.cols, spec.rows, spec.cell_w, spec.cell_h
            init_w, init_h = spec.sheet_w, spec.sheet_h
        elif sheet_size is not None:
            cols = rows = 1
            cell_w, cell_h = init_w, init_h

        self.sp_sheet_w = _spin(1, 16384, init_w)
        self.sp_sheet_h = _spin(1, 16384, init_h)
        self.sp_cell_w = _spin(1, 4096, cell_w)
        self.sp_cell_h = _spin(1, 4096, cell_h)
        self.sp_cols = _spin(1, 256, cols)
        self.sp_rows = _spin(1, 256, rows)

        grid = QGridLayout()
        grid.addWidget(QLabel(tr("dlg.spec.sheet_size")), 0, 0)
        grid.addWidget(self.sp_sheet_w, 0, 1)
        grid.addWidget(QLabel("×"), 0, 2)
        grid.addWidget(self.sp_sheet_h, 0, 3)
        grid.addWidget(QLabel(tr("dlg.spec.cell_size")), 1, 0)
        grid.addWidget(self.sp_cell_w, 1, 1)
        grid.addWidget(QLabel("×"), 1, 2)
        grid.addWidget(self.sp_cell_h, 1, 3)
        grid.addWidget(QLabel(tr("dlg.spec.cols")), 2, 0)
        grid.addWidget(self.sp_cols, 2, 1)
        grid.addWidget(QLabel(tr("dlg.spec.rows")), 3, 0)
        grid.addWidget(self.sp_rows, 3, 1)
        layout.addLayout(grid)

        self.result_label = QLabel()
        self.result_label.setWordWrap(True)
        layout.addWidget(self.result_label)

        form = QFormLayout()
        self.cb_save = QCheckBox(tr("dlg.spec.save_as_preset"))
        self.ed_name = QLineEdit(name)
        self.ed_name.setPlaceholderText(tr("dlg.spec.name_ph"))
        self.ed_motion = QLineEdit(body_motion)
        self.ed_motion.setPlaceholderText(tr("dlg.spec.motion_ph"))
        if editing_preset:
            self.cb_save.setChecked(True)
            self.cb_save.setVisible(False)
        form.addRow(self.cb_save)
        form.addRow(tr("dlg.spec.name"), self.ed_name)
        form.addRow(tr("dlg.spec.motion"), self.ed_motion)
        layout.addLayout(form)

        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

        for w in (self.sp_sheet_w, self.sp_sheet_h, self.sp_cell_w, self.sp_cell_h,
                  self.sp_cols, self.sp_rows):
            w.valueChanged.connect(self._recompute)
        self.rb_sheet.toggled.connect(self._recompute)
        self.cb_save.toggled.connect(self._recompute)
        self.ed_name.textChanged.connect(self._recompute)
        self._spec: GridSpec | None = None
        self._recompute()

    # -- 계산 ---------------------------------------------------------------
    def _recompute(self) -> None:
        by_sheet = self.rb_sheet.isChecked()
        for w in (self.sp_sheet_w, self.sp_sheet_h):
            w.setEnabled(by_sheet)
        for w in (self.sp_cell_w, self.sp_cell_h):
            w.setEnabled(not by_sheet)

        cols, rows = self.sp_cols.value(), self.sp_rows.value()
        spec: GridSpec | None
        if by_sheet:
            sw, sh = self.sp_sheet_w.value(), self.sp_sheet_h.value()
            spec = spec_from_sheet_size(sw, sh, cols, rows)
            if spec is None:
                self.result_label.setObjectName("ErrorLabel")
                self.result_label.setText(tr("dlg.spec.not_divisible", w=sw, h=sh, cols=cols, rows=rows))
            else:
                self._set_silently(self.sp_cell_w, spec.cell_w)
                self._set_silently(self.sp_cell_h, spec.cell_h)
        else:
            spec = spec_from_cell_size(self.sp_cell_w.value(), self.sp_cell_h.value(), cols, rows)
            if spec is not None:
                self._set_silently(self.sp_sheet_w, spec.sheet_w)
                self._set_silently(self.sp_sheet_h, spec.sheet_h)
        if spec is not None:
            self.result_label.setObjectName("DimLabel")
            self.result_label.setText(tr("dlg.spec.result", grid=grid_text(spec),
                                         sw=spec.sheet_w, sh=spec.sheet_h))
        self.result_label.style().unpolish(self.result_label)
        self.result_label.style().polish(self.result_label)
        self._spec = spec

        ok = spec is not None
        if self.cb_save.isChecked() and not self.ed_name.text().strip():
            ok = False
        self.ed_name.setEnabled(self.cb_save.isChecked())
        self.ed_motion.setEnabled(self.cb_save.isChecked())
        self.buttons.button(QDialogButtonBox.Ok).setEnabled(ok)

    @staticmethod
    def _set_silently(spin: QSpinBox, value: int) -> None:
        spin.blockSignals(True)
        spin.setValue(value)
        spin.blockSignals(False)

    # -- 결과 ---------------------------------------------------------------
    def result_spec(self) -> GridSpec | None:
        return self._spec

    def save_as_preset(self) -> bool:
        return self.cb_save.isChecked()

    def preset_name(self) -> str:
        return self.ed_name.text().strip()

    def body_motion(self) -> str:
        return self.ed_motion.text().strip()


class PresetManagerDialog(QDialog):
    presetsChanged = Signal()

    def __init__(self, config: ConfigManager, parent=None):
        super().__init__(parent)
        self.config = config
        self.setWindowTitle(tr("dlg.presets.title"))
        self.setMinimumSize(460, 340)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(tr("dlg.presets.hint")))
        self.list = QListWidget()
        self.list.currentRowChanged.connect(self._update_buttons)
        self.list.itemDoubleClicked.connect(lambda _i: self._edit())
        layout.addWidget(self.list, stretch=1)

        row = QHBoxLayout()
        self.btn_add = QPushButton(tr("dlg.presets.add"))
        self.btn_edit = QPushButton(tr("dlg.presets.edit"))
        self.btn_dup = QPushButton(tr("dlg.presets.duplicate"))
        self.btn_del = QPushButton(tr("dlg.presets.delete"))
        for b in (self.btn_add, self.btn_edit, self.btn_dup, self.btn_del):
            row.addWidget(b)
        row.addStretch(1)
        close = QPushButton(tr("btn.close"))
        close.clicked.connect(self.accept)
        row.addWidget(close)
        layout.addLayout(row)

        self.btn_add.clicked.connect(self._add)
        self.btn_edit.clicked.connect(self._edit)
        self.btn_dup.clicked.connect(self._duplicate)
        self.btn_del.clicked.connect(self._delete)
        self._reload()

    def _user_presets(self) -> list[dict]:
        return [p for p in all_presets(self.config.get("sheet_presets", [])) if not p.get("builtin")]

    def _reload(self, select_id: str | None = None) -> None:
        self.list.clear()
        for p in all_presets(self.config.get("sheet_presets", [])):
            text = preset_list_label(p)
            if p.get("builtin"):
                text += "  " + tr("dlg.presets.builtin_tag")
            item = QListWidgetItem(text)
            item.setData(256, p["id"])
            self.list.addItem(item)
            if select_id and p["id"] == select_id:
                self.list.setCurrentItem(item)
        if self.list.currentRow() < 0 and self.list.count():
            self.list.setCurrentRow(0)
        self._update_buttons()

    def _current(self) -> dict | None:
        item = self.list.currentItem()
        if item is None:
            return None
        pid = item.data(256)
        for p in all_presets(self.config.get("sheet_presets", [])):
            if p["id"] == pid:
                return p
        return None

    def _update_buttons(self, *_args) -> None:
        cur = self._current()
        has = cur is not None
        builtin = bool(cur and cur.get("builtin"))
        self.btn_edit.setEnabled(has and not builtin)
        self.btn_del.setEnabled(has and not builtin)
        self.btn_dup.setEnabled(has)

    def _store(self, presets: list[dict], select_id: str | None = None) -> None:
        self.config.set("sheet_presets", presets)
        self._reload(select_id)
        self.presetsChanged.emit()

    def _add(self) -> None:
        dlg = SheetSpecDialog(self, editing_preset=True)
        if dlg.exec() and dlg.result_spec():
            preset = make_user_preset(dlg.preset_name(), dlg.result_spec(), dlg.body_motion())
            self._store(self._user_presets() + [preset], preset["id"])

    def _edit(self) -> None:
        cur = self._current()
        if not cur or cur.get("builtin"):
            return
        dlg = SheetSpecDialog(self, spec=preset_spec(cur), name=cur.get("name", ""),
                              body_motion=cur.get("body_motion", ""), editing_preset=True)
        if dlg.exec() and dlg.result_spec():
            spec = dlg.result_spec()
            updated = []
            for p in self._user_presets():
                if p["id"] == cur["id"]:
                    p = dict(p, name=dlg.preset_name(), cols=spec.cols, rows=spec.rows,
                             cell_w=spec.cell_w, cell_h=spec.cell_h, body_motion=dlg.body_motion())
                updated.append(p)
            self._store(updated, cur["id"])

    def _duplicate(self) -> None:
        cur = self._current()
        if not cur:
            return
        name = tr("dlg.presets.copy_name", name=preset_display_name(cur))
        copy = make_user_preset(name, preset_spec(cur), cur.get("body_motion", ""))
        self._store(self._user_presets() + [copy], copy["id"])

    def _delete(self) -> None:
        cur = self._current()
        if not cur or cur.get("builtin"):
            return
        answer = QMessageBox.question(self, tr("dlg.presets.delete"),
                                      tr("dlg.presets.delete_confirm", name=preset_display_name(cur)))
        if answer == QMessageBox.Yes:
            self._store([p for p in self._user_presets() if p["id"] != cur["id"]])
