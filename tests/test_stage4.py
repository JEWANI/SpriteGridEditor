"""4단계(v0.4.0): 셀 편집 창(칠하기·지우기·스포이트·Alt 임시 스포이트·적용) / 자유 선택 이동."""

import os
import sys
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import pixel_tools as pt  # noqa: E402
from history import History  # noqa: E402


class _Doc:
    def __init__(self, arr, mask=None):
        self.arr = arr
        self.has_mask = mask is not None
        self.mask = mask if mask is not None else np.zeros(arr.shape[:2], np.uint8)


class PixelToolsTests(unittest.TestCase):
    def setUp(self):
        self.arr = np.zeros((20, 30, 4), np.uint8)
        self.arr[5:10, 5:10] = (10, 20, 30, 255)
        self.mask = np.zeros((20, 30), np.uint8)
        self.mask[5:10, 5:10] = 2

    def test_paint_is_opaque_and_writes_index_within_bounds(self):
        d = pt.paint_stamp(self.arr, self.mask, 1, 1, 3, (200, 100, 50), 3, (0, 20, 0, 2))
        self.assertEqual(d, (0, 3, 0, 2))  # 범위(x<2)로 잘림
        self.assertTrue((self.arr[0:3, 0:2] == (200, 100, 50, 255)).all())
        self.assertTrue((self.mask[0:3, 0:2] == 3).all())
        self.assertEqual(int(self.arr[1, 2, 3]), 0)  # 범위 밖은 그대로
        # 같은 색·번호로 다시 칠하면 바뀐 것 없음
        self.assertIsNone(pt.paint_stamp(self.arr, self.mask, 1, 1, 3, (200, 100, 50), 3, (0, 20, 0, 2)))

    def test_paint_without_index_keeps_mask(self):
        pt.paint_stamp(self.arr, self.mask, 6, 6, 1, (1, 2, 3), None, (0, 20, 0, 30))
        self.assertEqual(int(self.mask[6, 6]), 2)

    def test_erase_clears_rgba_and_mask_and_line_is_continuous(self):
        d = pt.erase_line(self.arr, self.mask, (5, 7), (9, 7), 1, (0, 20, 0, 30))
        self.assertEqual(d, (7, 8, 5, 10))
        self.assertTrue((self.arr[7, 5:10] == 0).all())
        self.assertTrue((self.mask[7, 5:10] == 0).all())
        self.assertIsNone(pt.erase_stamp(self.arr, self.mask, 0, 0, 1, (0, 20, 0, 30)))  # 이미 비어 있음

    def test_pick_returns_color_and_index(self):
        self.assertEqual(pt.pick(self.arr, self.mask, 6, 6), ((10, 20, 30, 255), 2))
        self.assertEqual(pt.pick(self.arr, None, 0, 0), ((0, 0, 0, 0), None))

    def test_recorder_makes_single_undo_step(self):
        doc = _Doc(self.arr, self.mask)
        h = History()
        before = self.arr.copy()
        rec = pt.PixelRecorder(doc)
        rec.add(pt.paint_line(doc.arr, doc.mask, (0, 0), (10, 0), 1, (9, 9, 9), 1, (0, 20, 0, 30)))
        rec.add(pt.erase_stamp(doc.arr, doc.mask, 6, 6, 1, (0, 20, 0, 30)))
        edit = rec.finish("edit.cell_edit")
        h.do(doc, edit)
        self.assertEqual(int(doc.mask[0, 3]), 1)
        h.undo(doc)
        self.assertTrue(np.array_equal(doc.arr, before))
        self.assertEqual(int(doc.mask[6, 6]), 2)
        self.assertEqual(int(doc.mask[0, 3]), 0)

    def test_move_clears_origin_and_overlays_only_opaque(self):
        self.arr[5:10, 14:16] = (99, 99, 99, 255)  # 목적지 옆의 다른 그림
        self.mask[5:10, 14:16] = 4
        sel = (4, 11, 4, 11)  # 투명 테두리 포함 선택
        edit, new_sel = pt.make_move_edit(self.arr, self.mask, sel, 6, 0, "edit.move")
        self.assertEqual(new_sel, (4, 11, 10, 17))
        doc = _Doc(self.arr, self.mask)
        h = History()
        before, mbefore = self.arr.copy(), self.mask.copy()
        h.do(doc, edit)
        self.assertTrue((self.arr[5:10, 11:16] == (10, 20, 30, 255)).all())  # 옮겨진 그림이 옆 그림 위에
        self.assertTrue((self.mask[5:10, 11:16] == 2).all())
        self.assertTrue((self.arr[5:10, 5:10, 3] == 0).all())  # 원래 자리는 비움
        self.assertTrue((self.mask[5:10, 5:10] == 0).all())
        h.undo(doc)
        self.assertTrue(np.array_equal(self.arr, before) and np.array_equal(self.mask, mbefore))

    def test_move_transparent_part_does_not_erase_neighbor(self):
        arr = np.zeros((10, 20, 4), np.uint8)
        arr[2:4, 2:4] = (1, 1, 1, 255)
        arr[2:4, 9] = (5, 5, 5, 255)  # 이웃 그림
        edit, _ = pt.make_move_edit(arr, None, (0, 6, 0, 6), 5, 0, "edit.move")
        edit.apply(arr)
        self.assertTrue((arr[2:4, 7:9] == (1, 1, 1, 255)).all())
        self.assertTrue((arr[2:4, 9] == (5, 5, 5, 255)).all())  # 선택의 투명 부분이 덮어도 남음

    def test_move_off_sheet_is_clipped_and_noop_returns_none(self):
        arr = np.zeros((10, 10, 4), np.uint8)
        arr[0:2, 0:2] = 255
        edit, sel = pt.make_move_edit(arr, None, (0, 2, 0, 2), -5, 0, "edit.move")
        edit.apply(arr)
        self.assertFalse(arr.any())  # 시트 밖으로 나간 부분은 버림
        self.assertEqual(sel, (0, 2, 0, 2))
        self.assertEqual(pt.make_move_edit(arr, None, (0, 2, 0, 2), 0, 0, "edit.move")[0], None)

    def test_normalize_rect(self):
        self.assertEqual(pt.normalize_rect((5, 3), (2, 8), (10, 10, 4)), (3, 9, 2, 6))
        self.assertEqual(pt.normalize_rect((-3, -3), (20, 20), (10, 10, 4)), (0, 10, 0, 10))


# --------------------------------------------------------------------------- #
#  UI                                                                          #
# --------------------------------------------------------------------------- #
from PySide6.QtCore import QPoint, Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

_app = QApplication.instance() or QApplication([])

import i18n  # noqa: E402
import main_window as mw_mod  # noqa: E402
from autosave import AutoSaver  # noqa: E402
from cell_editor import TOOL_ERASE, TOOL_PAINT, TOOL_PICK, CellEditDialog, PAD  # noqa: E402
from config_manager import ConfigManager  # noqa: E402
from main_window import MainWindow  # noqa: E402
from sheet_model import CellRect  # noqa: E402


class Stage4UiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = self.tmp.name
        self.cfg = ConfigManager(os.path.join(d, "config.json"))
        self.cfg.set("body_dir", os.path.join(d, "Allies", "Body"))
        self.cfg.set("save_dir", os.path.join(d, "out"))
        os.makedirs(os.path.join(d, "Allies", "Body"))
        arr = np.zeros((540, 300, 4), np.uint8)
        arr[20:120, 20:80] = (120, 120, 120, 255)  # R1C1 안의 그림
        arr[150:250, 120:180] = (60, 60, 60, 255)  # R2C2
        self.arr = arr
        self.sheet = os.path.join(d, "H1WK_P001F.png")
        Image.fromarray(arr, "RGBA").save(self.sheet)
        i18n.set_language("ko")
        self.win = MainWindow(self.cfg, AutoSaver(os.path.join(d, "autosave")))
        self.win.resize(1400, 860)
        self.win.show()
        _app.processEvents()
        self.assertTrue(self.win.open_file(self.sheet))
        m = self.win.model
        m.set_mask(np.where(m.arr[..., 3] > 0, 1, 0).astype(np.uint8))
        self.win._update_all()

    def tearDown(self):
        if self.win.model is not None:
            self.win.model.mark_saved()
            self.win.model.forced_dirty = False
        self.win.close()
        self.tmp.cleanup()

    def _dialog(self, rect=CellRect.single(0, 0)):
        m = self.win.model
        dlg = CellEditDialog(self.win, m.arr, m.mask, m.grid, rect, self.win._all_refs(), self.cfg)
        dlg.show()
        _app.processEvents()
        return dlg

    def _vpos(self, dlg, x, y):
        """작업 문서 픽셀 (x, y)의 뷰 위젯 좌표 (픽셀 가운데)."""
        s = dlg.view.scale
        return QPoint(PAD + x * s + s // 2, PAD + y * s + s // 2)

    def test_dialog_region_includes_neighbors_and_edit_box(self):
        dlg = self._dialog(CellRect.single(1, 1))
        # R2C2 → 이웃 포함 3×3 셀 = 시트 300×405 (R1~R3), 편집 범위는 가운데 셀
        self.assertEqual(dlg.doc.arr.shape[:2], (405, 300))
        self.assertEqual(dlg.edit_box, (135, 270, 100, 200))
        self.assertEqual(dlg.view.scale, 8)
        self.assertTrue(dlg.view.show_neighbors)
        dlg.reject()
        dlg2 = self._dialog(CellRect(0, 0, 0, 2))  # 한 행 3칸 → 범위 전체
        self.assertEqual(dlg2.edit_box, (0, 135, 0, 300))
        self.assertEqual(dlg2.windowTitle(), i18n.tr("dlg.celledit.title", range="R1C1–R1C3"))
        dlg2.reject()

    def test_paint_erase_pick_and_alt_temp_pick(self):
        dlg = self._dialog()
        dlg._set_zoom(400)
        v = dlg.view
        # 스포이트(I): 그림 색과 마스크 번호를 가져온다
        QTest.keyClick(v, Qt.Key_I)
        self.assertEqual(dlg.tool, TOOL_PICK)
        QTest.mouseClick(v, Qt.LeftButton, Qt.NoModifier, self._vpos(dlg, 30, 30))
        self.assertEqual(dlg.colors[0], ((120, 120, 120), 1))
        # 주/보조 바꾸기(X) 두 번 → 원래대로
        QTest.keyClick(v, Qt.Key_X)
        QTest.keyClick(v, Qt.Key_X)
        self.assertEqual(dlg.colors[0][0], (120, 120, 120))
        # 칠하기(B): 빈 곳에 칠하면 불투명 + 마스크 번호 1
        dlg.colors[0] = ((200, 10, 10), 3)
        QTest.keyClick(v, Qt.Key_B)
        self.assertEqual(dlg.tool, TOOL_PAINT)
        QTest.mousePress(v, Qt.LeftButton, Qt.NoModifier, self._vpos(dlg, 5, 5))
        QTest.mouseMove(v, self._vpos(dlg, 12, 5))
        QTest.mouseRelease(v, Qt.LeftButton, Qt.NoModifier, self._vpos(dlg, 12, 5))
        self.assertTrue((dlg.doc.arr[5, 5:13] == (200, 10, 10, 255)).all())
        self.assertTrue((dlg.doc.mask[5, 5:13] == 3).all())
        self.assertEqual(len(dlg.history._edits), 1)  # 획 하나 = 한 단계
        # 편집 범위 밖(이웃 셀)은 칠해지지 않는다
        QTest.mouseClick(v, Qt.LeftButton, Qt.NoModifier, self._vpos(dlg, 150, 5))
        self.assertEqual(int(dlg.doc.arr[5, 150, 3]), 0)
        # Alt를 누르고 있는 동안 임시 스포이트 → 떼면 칠하기로
        dlg.set_temp_pick(True)
        self.assertEqual(dlg.effective_tool(), TOOL_PICK)
        self.assertIn("Alt", dlg.lb_state.text())
        QTest.mouseClick(v, Qt.LeftButton, Qt.NoModifier, self._vpos(dlg, 30, 30))
        self.assertEqual(dlg.colors[0], ((120, 120, 120), 1))
        dlg.set_temp_pick(False)
        self.assertEqual(dlg.effective_tool(), TOOL_PAINT)
        # Alt+클릭도 스포이트
        dlg.colors[0] = ((1, 2, 3), 2)
        QTest.mouseClick(v, Qt.LeftButton, Qt.AltModifier, self._vpos(dlg, 30, 30))
        self.assertEqual(dlg.colors[0], ((120, 120, 120), 1))
        # 지우기(E) + 브러시 크기 ]
        QTest.keyClick(v, Qt.Key_E)
        QTest.keyClick(v, Qt.Key_BracketRight)
        QTest.keyClick(v, Qt.Key_BracketRight)
        self.assertEqual(dlg.sp_brush.value(), 3)
        self.assertEqual(dlg.tool, TOOL_ERASE)
        QTest.mouseClick(v, Qt.LeftButton, Qt.NoModifier, self._vpos(dlg, 30, 30))
        self.assertTrue((dlg.doc.arr[29:32, 29:32] == 0).all())
        self.assertTrue((dlg.doc.mask[29:32, 29:32] == 0).all())
        # 창 안 실행 취소
        QTest.keyClick(v, Qt.Key_Z, Qt.ControlModifier)
        self.assertEqual(int(dlg.doc.arr[30, 30, 3]), 255)
        QTest.keyClick(v, Qt.Key_Y, Qt.ControlModifier)
        self.assertEqual(int(dlg.doc.arr[30, 30, 3]), 0)
        # 상태 표시·커서
        self.assertIn("3px", dlg.lb_state.text())
        dlg.history.mark_saved()
        dlg.reject()

    def test_apply_is_one_undo_step_and_persists_after_save(self):
        w = self.win
        m = w.model
        before = m.arr.copy()

        class Auto(CellEditDialog):
            def exec(inner):
                inner.colors[0] = ((250, 0, 0), 2)
                QTest.mousePress(inner.view, Qt.LeftButton, Qt.NoModifier, self._vpos(inner, 10, 10))
                QTest.mouseRelease(inner.view, Qt.LeftButton, Qt.NoModifier, self._vpos(inner, 10, 10))
                inner._set_tool(TOOL_ERASE)
                QTest.mouseClick(inner.view, Qt.LeftButton, Qt.NoModifier, self._vpos(inner, 25, 25))
                inner.apply()
                return 1

        orig = mw_mod.CellEditDialog
        mw_mod.CellEditDialog = Auto
        try:
            w.canvas.set_source(CellRect.single(0, 0))
            n0 = len(m.history._edits)
            w.open_cell_editor()
        finally:
            mw_mod.CellEditDialog = orig
        self.assertEqual(len(m.history._edits), n0 + 1)  # 적용 = 메인 실행 취소 한 단계
        self.assertTrue((m.arr[10, 10] == (250, 0, 0, 255)).all())
        self.assertEqual(int(m.mask[10, 10]), 2)
        self.assertEqual(int(m.arr[25, 25, 3]), 0)
        self.assertEqual(int(m.mask[25, 25]), 0)
        self.assertEqual(w._last_msg[0], "status.cell_edited")
        # 메인 실행 취소 한 번이면 원래대로, 다시 실행
        w.undo()
        self.assertTrue(np.array_equal(m.arr, before))
        self.assertEqual(int(m.mask[25, 25]), 1)
        w.redo()
        # 저장 → 다시 열기 후 유지 (4단계 완료 기준)
        m.save_path = os.path.join(self.tmp.name, "out", "H1WK_P001F.png")
        self.assertTrue(w.save())
        w.model.mark_saved()
        self.assertTrue(w.open_file(m.save_path))
        m2 = w.model
        self.assertTrue((m2.arr[10, 10] == (250, 0, 0, 255)).all())
        self.assertEqual(int(m2.mask[10, 10]), 2)
        self.assertEqual(int(m2.arr[25, 25, 3]), 0)

    def test_cancel_and_unchanged_apply_add_no_history(self):
        w = self.win
        m = w.model

        class Cancel(CellEditDialog):
            def exec(inner):
                inner.reject()
                return 0

        class NoChange(CellEditDialog):
            def exec(inner):
                inner.apply()
                return 1

        orig = mw_mod.CellEditDialog
        try:
            w.canvas.set_source(CellRect.single(0, 0))
            n0 = len(m.history._edits)
            for cls in (Cancel, NoChange):
                mw_mod.CellEditDialog = cls
                w.open_cell_editor()
            self.assertEqual(len(m.history._edits), n0)
            self.assertEqual(w._last_msg[0], "status.cell_edit_none")
        finally:
            mw_mod.CellEditDialog = orig

    def test_double_click_selects_cell_and_opens_editor(self):
        w = self.win
        opened = []
        w.open_cell_editor = lambda: opened.append(w.canvas.source())
        cv = w.canvas
        ox, oy = cv._origin()
        s = cv._scale()
        pos = QPoint(ox + 150 * s, oy + 200 * s)  # R2C2
        QTest.mouseDClick(cv.viewport(), Qt.LeftButton, Qt.NoModifier, pos)
        self.assertEqual(opened, [CellRect.single(1, 1)])
        self.assertTrue(w.a_cell_edit.isEnabled())

    def test_settings_control_neighbor_display(self):
        self.cfg.set("cell_edit.neighbors", False)
        self.cfg.set("cell_edit.neighbor_opacity", 70)
        self.cfg.set("cell_edit.zoom", 1200)
        dlg = self._dialog()
        self.assertFalse(dlg.view.show_neighbors)
        self.assertEqual(dlg.view.neighbor_opacity, 70)
        self.assertEqual(dlg.view.scale, 12)
        dlg.chk_neighbors.setChecked(True)
        self.assertTrue(self.cfg.get("cell_edit.neighbors"))
        dlg.reject()

    def _canvas_pos(self, x, y):
        cv = self.win.canvas
        ox, oy = cv._origin()
        s = cv._scale()
        return QPoint(ox + x * s + s // 2, oy + y * s + s // 2)

    def test_move_tool_marquee_drag_and_arrow_keys(self):
        w = self.win
        m = w.model
        cv = w.canvas
        before, mbefore = m.arr.copy(), m.mask.copy()
        w.tool_actions["move"].trigger()
        self.assertEqual(cv.paint_tool(), "move")
        vp = cv.viewport()
        # 사각형 선택 (R1C1 그림 주변)
        QTest.mousePress(vp, Qt.LeftButton, Qt.NoModifier, self._canvas_pos(15, 15))
        QTest.mouseMove(vp, self._canvas_pos(84, 124))
        QTest.mouseRelease(vp, Qt.LeftButton, Qt.NoModifier, self._canvas_pos(84, 124))
        self.assertEqual(cv.pixel_selection(), (15, 125, 15, 85))
        self.assertEqual(w._last_msg[0], "status.px_selected")
        # 선택 안을 끌어 오른쪽 아래로 (칸 경계를 넘어)
        n0 = len(m.history._edits)
        QTest.mousePress(vp, Qt.LeftButton, Qt.NoModifier, self._canvas_pos(40, 40))
        QTest.mouseMove(vp, self._canvas_pos(70, 60))
        QTest.mouseRelease(vp, Qt.LeftButton, Qt.NoModifier, self._canvas_pos(70, 60))
        self.assertEqual(len(m.history._edits), n0 + 1)
        self.assertTrue((m.arr[40:140, 50:110] == (120, 120, 120, 255)).all())
        self.assertEqual(int(m.arr[25, 25, 3]), 0)  # 원래 자리 비움
        self.assertTrue((m.mask[40:140, 50:110] == 1).all())  # 마스크도 같이
        self.assertEqual(cv.pixel_selection(), (35, 145, 45, 115))
        # 방향키 1px / Shift 5px
        cv.setFocus()
        QTest.keyClick(cv, Qt.Key_Left)
        QTest.keyClick(cv, Qt.Key_Up, Qt.ShiftModifier)
        self.assertEqual(cv.pixel_selection(), (30, 140, 44, 114))
        self.assertTrue((m.arr[35:135, 49:109] == (120, 120, 120, 255)).all())
        # 실행 취소 세 번이면 원래대로
        for _ in range(3):
            w.undo()
        self.assertTrue(np.array_equal(m.arr, before) and np.array_equal(m.mask, mbefore))
        # Esc = 선택 해제, 다른 도구로 바꾸면 선택 해제
        QTest.keyClick(cv, Qt.Key_Escape)
        self.assertIsNone(cv.pixel_selection())
        w.move_pixel_selection(1, 0)
        self.assertEqual(w._last_msg[0], "status.move_need_sel")
        w._set_paint_tool(None)
        self.assertIsNone(cv.pixel_selection())

    def test_shortcuts_still_unique(self):
        from collections import Counter
        keys = [a.shortcut().toString() for a in self.win.findChildren(type(self.win.a_save))
                if a.shortcut().toString()]
        self.assertEqual([k for k, n in Counter(keys).items() if n > 1], [])


if __name__ == "__main__":
    unittest.main()
