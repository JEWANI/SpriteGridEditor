"""v0.4.1: 셀 편집 창 — 오른쪽 버튼 지우기 / 이동 도구 V / 색 칸 5개(숫자키) / Shift+클릭 직선, Qt 기본 문구 한국어."""

import os
import sys
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from PySide6.QtCore import QPoint, Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QDialogButtonBox, QMessageBox  # noqa: E402

_app = QApplication.instance() or QApplication([])

import i18n  # noqa: E402
import pixel_tools as pt  # noqa: E402
from autosave import AutoSaver  # noqa: E402
from cell_editor import MODE_IMAGE, MODE_MASK, PAD, SLOT_COUNT, TOOL_FILL, TOOL_MOVE, TOOL_PAINT, CellEditDialog  # noqa: E402
from config_manager import ConfigManager  # noqa: E402
from main_window import MainWindow  # noqa: E402
from sheet_model import CellRect  # noqa: E402


class ShiftBlockTests(unittest.TestCase):
    def test_shift_clips_and_clears(self):
        b = np.zeros((4, 4, 4), np.uint8)
        b[0, 0] = 255
        m = np.zeros((4, 4), np.uint8)
        m[0, 0] = 2
        o, mo = pt.shift_block(b, m, 2, 1)
        self.assertTrue((o[1, 2] == 255).all())
        self.assertEqual(int(mo[1, 2]), 2)
        self.assertFalse(o[0, 0].any())
        self.assertFalse(pt.shift_block(b, None, -1, 0)[0].any())  # 칸 밖으로 → 버림


class FillPaletteTests(unittest.TestCase):
    def test_fill_same_color_region_and_erase(self):
        a = np.zeros((10, 10, 4), np.uint8)
        a[2:5, 2:5] = (10, 20, 30, 255)
        a[3, 3] = (1, 1, 1, 255)
        m = np.zeros((10, 10), np.uint8)
        m[2:5, 2:5] = 2
        self.assertEqual(pt.fill_region(a, m, 2, 2, (200, 0, 0), 3, (0, 10, 0, 10)), (2, 5, 2, 5))
        self.assertEqual(tuple(a[2, 2]), (200, 0, 0, 255))
        self.assertEqual(tuple(a[3, 3]), (1, 1, 1, 255))  # 다른 색은 그대로
        self.assertEqual((int(m[2, 2]), int(m[3, 3])), (3, 2))
        pt.fill_region(a, m, 2, 2, (0, 0, 0), None, (0, 10, 0, 10), erase=True)
        self.assertEqual(int(a[2, 2, 3]), 0)
        self.assertEqual(int(m[2, 2]), 0)
        # 범위 밖으로 번지지 않음
        pt.fill_region(a, m, 0, 0, (9, 9, 9), 1, (0, 10, 0, 5))
        self.assertEqual(int(a[0, 6, 3]), 0)

    def test_palette_most_used_first_with_index(self):
        a = np.zeros((4, 4, 4), np.uint8)
        a[0, :] = (5, 5, 5, 255)
        a[1, 0] = (7, 7, 7, 255)
        m = np.zeros((4, 4), np.uint8)
        m[0, :] = 2
        self.assertEqual(pt.palette(a, m, (0, 4, 0, 4)), [((5, 5, 5), 2, 4), ((7, 7, 7), 0, 1)])
        self.assertEqual(pt.palette(a, None, (2, 4, 0, 4)), [])


class QtTranslationTests(unittest.TestCase):
    def tearDown(self):
        i18n.set_language("ko")

    def test_standard_buttons_follow_language(self):
        i18n.set_language("ko")
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.assertEqual([b.text() for b in bb.buttons()], ["확인", "취소"])
        box = QMessageBox()
        box.setStandardButtons(QMessageBox.Save | QMessageBox.Cancel)
        self.assertIn("저장", [b.text() for b in box.buttons()])
        i18n.set_language("en")
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.assertEqual([b.text() for b in bb.buttons()], ["OK", "Cancel"])


class CellEditorV041Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = self.tmp.name
        self.cfg = ConfigManager(os.path.join(d, "config.json"))
        self.cfg.set("body_dir", os.path.join(d, "Allies", "Body"))
        os.makedirs(os.path.join(d, "Allies", "Body"))
        arr = np.zeros((540, 300, 4), np.uint8)
        arr[20:120, 20:80] = (120, 120, 120, 255)  # R1C1
        sheet = os.path.join(d, "H1WK_P001F.png")
        Image.fromarray(arr, "RGBA").save(sheet)
        i18n.set_language("ko")
        self.win = MainWindow(self.cfg, AutoSaver(os.path.join(d, "autosave")))
        self.win.show()
        _app.processEvents()
        self.assertTrue(self.win.open_file(sheet))
        m = self.win.model
        m.set_mask(np.where(m.arr[..., 3] > 0, 1, 0).astype(np.uint8))
        self.dlg = CellEditDialog(self.win, m.arr, m.mask, m.grid, CellRect.single(0, 0), [], self.cfg)
        self.dlg.show()
        _app.processEvents()

    def tearDown(self):
        self.dlg.history.mark_saved()  # 닫을 때 버리기 확인 창이 뜨지 않게
        self.dlg.done(0)
        if self.win.model is not None:
            self.win.model.mark_saved()
        self.win.close()
        self.tmp.cleanup()

    def _pos(self, x, y):
        s = self.dlg.view.scale
        return QPoint(PAD + x * s + s // 2, PAD + y * s + s // 2)

    def _doc_xy(self, sx, sy):
        """시트 좌표 → 작업 문서 좌표."""
        return sx - self.dlg.origin[1], sy - self.dlg.origin[0]

    def test_right_button_erases_left_paints(self):
        dlg = self.dlg
        x, y = self._doc_xy(30, 30)
        QTest.mouseClick(dlg.view, Qt.RightButton, Qt.NoModifier, self._pos(x, y))
        self.assertEqual(int(dlg.doc.arr[y, x, 3]), 0)
        self.assertEqual(int(dlg.doc.mask[y, x]), 0)
        self.assertEqual(dlg.tool, TOOL_PAINT)  # 도구는 그대로
        QTest.mouseClick(dlg.view, Qt.LeftButton, Qt.NoModifier, self._pos(x, y))
        self.assertEqual(int(dlg.doc.arr[y, x, 3]), 255)
        self.assertEqual(dlg.history._pos, 2)  # 획마다 한 단계

    def test_number_keys_select_slots_and_x_returns(self):
        dlg = self.dlg
        self.assertEqual(len(dlg.colors), SLOT_COUNT)
        QTest.keyClick(dlg, Qt.Key_3)
        self.assertEqual(dlg.slot, 2)
        QTest.keyClick(dlg, Qt.Key_5)
        self.assertEqual(dlg.slot, 4)
        QTest.keyClick(dlg, Qt.Key_X)
        self.assertEqual(dlg.slot, 2)
        # 스포이트는 현재 칸에 들어가고 설정에 남는다
        x, y = self._doc_xy(30, 30)
        dlg._pick_at(x, y)
        self.assertEqual(dlg.colors[2], ((120, 120, 120), 1))
        self.assertEqual(self.cfg.get("cell_edit.colors")[2], ["#787878", 1])
        # 칠하기는 현재 칸 색
        QTest.mouseClick(dlg.view, Qt.LeftButton, Qt.NoModifier, self._pos(*self._doc_xy(90, 30)))
        self.assertTrue((dlg.doc.arr[self._doc_xy(90, 30)[1], self._doc_xy(90, 30)[0], :3] == 120).all())

    def test_move_tool_drag_and_arrows(self):
        dlg = self.dlg
        QTest.keyClick(dlg, Qt.Key_V)
        self.assertEqual(dlg.tool, TOOL_MOVE)
        x, y = self._doc_xy(20, 20)
        QTest.mousePress(dlg.view, Qt.LeftButton, Qt.NoModifier, self._pos(x, y))
        QTest.mouseMove(dlg.view, self._pos(x + 5, y + 2))
        QTest.mouseRelease(dlg.view, Qt.LeftButton, Qt.NoModifier, self._pos(x + 5, y + 2))
        self.assertEqual(int(dlg.doc.arr[y + 2, x + 5, 3]), 255)  # 왼쪽 위 모서리가 (25, 22)로
        self.assertEqual(int(dlg.doc.arr[y, x, 3]), 0)
        self.assertEqual(int(dlg.doc.mask[y + 2, x + 5]), 1)
        # 이웃 칸(R1C2)은 그대로 비어 있음 — 칸 밖으로는 안 넘침
        nx, ny = self._doc_xy(100, 30)
        self.assertEqual(int(dlg.doc.arr[ny, nx, 3]), 0)
        QTest.keyClick(dlg, Qt.Key_Left, Qt.ShiftModifier)
        self.assertEqual(int(dlg.doc.arr[y + 2, x, 3]), 255)  # 5px 왼쪽
        dlg.undo()
        dlg.undo()
        self.assertEqual(int(dlg.doc.arr[y, x, 3]), 255)
        dlg.apply()
        self.assertFalse(dlg.changed)

    def test_shift_click_draws_line(self):
        dlg = self.dlg
        a, b = self._doc_xy(85, 10), self._doc_xy(95, 10)
        QTest.mouseClick(dlg.view, Qt.LeftButton, Qt.NoModifier, self._pos(*a))
        QTest.mouseClick(dlg.view, Qt.LeftButton, Qt.ShiftModifier, self._pos(*b))
        row = dlg.doc.arr[a[1], a[0]:b[0] + 1, 3]
        self.assertTrue((row == 255).all())

    def test_fill_tool_flip_and_palette(self):
        dlg = self.dlg
        QTest.keyClick(dlg, Qt.Key_G)
        self.assertEqual(dlg.tool, TOOL_FILL)
        x, y = self._doc_xy(30, 30)
        QTest.mouseClick(dlg.view, Qt.LeftButton, Qt.NoModifier, self._pos(x, y))
        self.assertEqual(tuple(dlg.doc.arr[y + 50, x + 40, :3]), dlg.colors[0][0])  # 이어진 회색 전부
        QTest.mouseClick(dlg.view, Qt.RightButton, Qt.NoModifier, self._pos(x, y))
        self.assertEqual(int(dlg.doc.arr[y + 50, x + 40, 3]), 0)
        dlg.undo()
        # H: 마우스가 있는 칸 좌우 반전 (그림은 x 20~79 → 20~79 그대로 대칭이므로 한쪽만 칠해 확인)
        QTest.keyClick(dlg, Qt.Key_B)
        QTest.mouseClick(dlg.view, Qt.LeftButton, Qt.NoModifier, self._pos(*self._doc_xy(5, 5)))
        dlg.view.hover = self._doc_xy(5, 5)
        QTest.keyClick(dlg, Qt.Key_H)
        fx, fy = self._doc_xy(94, 5)
        self.assertEqual(int(dlg.doc.arr[fy, fx, 3]), 255)
        self.assertEqual(int(dlg.doc.arr[self._doc_xy(5, 5)[1], self._doc_xy(5, 5)[0], 3]), 0)
        # 시트 색 → 지금 색 칸
        cols = dlg.palette_colors()
        self.assertTrue(cols)
        dlg.select_slot(3)
        dlg.set_slot_color(cols[0][0], cols[0][1])
        self.assertEqual(dlg.colors[3][0], cols[0][0])
        # 마스크 보기 토글
        QTest.keyClick(dlg, Qt.Key_M)
        self.assertTrue(dlg.view.show_mask)
        dlg.view.repaint()

    def test_image_mode_keeps_existing_mask_index(self):
        dlg = self.dlg
        self.assertEqual(dlg.mode, MODE_IMAGE)
        dlg.select_slot(1)  # 색 칸 2 = 마스크 번호 2
        x, y = self._doc_xy(30, 30)  # 그림 있음 (번호 1)
        QTest.mouseClick(dlg.view, Qt.LeftButton, Qt.NoModifier, self._pos(x, y))
        self.assertEqual(tuple(dlg.doc.arr[y, x, :3]), dlg.colors[1][0])
        self.assertEqual(int(dlg.doc.mask[y, x]), 1)  # 기존 번호 유지
        ex, ey = self._doc_xy(90, 10)  # 빈 곳 → 새 픽셀은 색 칸 번호
        QTest.mouseClick(dlg.view, Qt.LeftButton, Qt.NoModifier, self._pos(ex, ey))
        self.assertEqual(int(dlg.doc.mask[ey, ex]), 2)

    def test_mask_mode_paints_index_only(self):
        dlg = self.dlg
        QTest.keyClick(dlg, Qt.Key_Q)
        self.assertEqual(dlg.mode, MODE_MASK)
        self.assertTrue(dlg.view.show_mask)
        self.assertFalse(dlg.btn_move.isEnabled())
        QTest.keyClick(dlg, Qt.Key_3)
        self.assertEqual(dlg.region, 3)
        before = dlg.doc.arr.copy()
        x, y = self._doc_xy(30, 30)
        QTest.mouseClick(dlg.view, Qt.LeftButton, Qt.NoModifier, self._pos(x, y))
        self.assertEqual(int(dlg.doc.mask[y, x]), 3)
        ex, ey = self._doc_xy(90, 10)  # 투명 픽셀은 보호
        QTest.mouseClick(dlg.view, Qt.LeftButton, Qt.NoModifier, self._pos(ex, ey))
        self.assertEqual(int(dlg.doc.mask[ey, ex]), 0)
        QTest.mouseClick(dlg.view, Qt.RightButton, Qt.NoModifier, self._pos(x, y))  # 오른쪽 = 번호 0
        self.assertEqual(int(dlg.doc.mask[y, x]), 0)
        QTest.keyClick(dlg, Qt.Key_G)
        QTest.mouseClick(dlg.view, Qt.LeftButton, Qt.NoModifier, self._pos(*self._doc_xy(60, 100)))
        self.assertEqual(int(dlg.doc.mask[self._doc_xy(60, 100)[1], self._doc_xy(60, 100)[0]]), 3)
        self.assertTrue(np.array_equal(dlg.doc.arr, before))  # 그림은 그대로
        # 스포이트 = 영역 번호, X = 직전 영역
        QTest.keyClick(dlg, Qt.Key_1)
        dlg._pick_at(*self._doc_xy(60, 100))
        self.assertEqual(dlg.region, 3)
        QTest.keyClick(dlg, Qt.Key_X)
        self.assertEqual(dlg.region, 1)
        dlg.undo()
        QTest.keyClick(dlg, Qt.Key_Q)
        self.assertEqual(dlg.mode, MODE_IMAGE)
        self.assertTrue(dlg.btn_move.isEnabled())

    def test_main_opens_mask_mode_when_mask_view_on_and_alt_double_click(self):
        w = self.win
        self.dlg.history.mark_saved()
        modes = []

        class Fake:
            def __init__(self, *a, mode="image", **k):
                modes.append(mode)
                self.result_block = None
                self.changed = False

            def exec(self):
                return 0

        import main_window as mw_mod
        orig = mw_mod.CellEditDialog
        mw_mod.CellEditDialog = Fake
        try:
            w.canvas.set_source(CellRect.single(0, 0))
            self.cfg.set("mask_view.mode", "off")
            w.open_cell_editor()
            self.cfg.set("mask_view.mode", "overlay")
            w.open_cell_editor()
            self.assertEqual(modes, ["image", "mask"])
            # 마스크 연필이 켜져 있어도 Alt+더블클릭이면 열림
            self.cfg.set("mask_view.mode", "off")
            w._set_paint_tool("pencil")
            cv = w.canvas
            ox, oy = cv._origin()
            s = cv._scale()
            QTest.mouseDClick(cv.viewport(), Qt.LeftButton, Qt.AltModifier, QPoint(ox + 50 * s, oy + 60 * s))
            self.assertEqual(modes[-1], "mask")
            self.assertEqual(len(modes), 3)
        finally:
            mw_mod.CellEditDialog = orig
            w._set_paint_tool(None)


class CanvasSelectionTests(unittest.TestCase):
    """v0.4.2: 다시 누르면 선택 해제 / 빈 곳 클릭·Esc·Ctrl+D 로 해제."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = self.tmp.name
        self.cfg = ConfigManager(os.path.join(d, "config.json"))
        self.cfg.set("body_dir", os.path.join(d, "Allies", "Body"))
        os.makedirs(os.path.join(d, "Allies", "Body"))
        sheet = os.path.join(d, "H1WK_P001F.png")
        Image.fromarray(np.zeros((540, 300, 4), np.uint8), "RGBA").save(sheet)
        i18n.set_language("ko")
        self.win = MainWindow(self.cfg, AutoSaver(os.path.join(d, "autosave")))
        self.win.resize(1400, 900)
        self.win.show()
        _app.processEvents()
        self.assertTrue(self.win.open_file(sheet))
        self.cv = self.win.canvas
        self.cv.set_zoom(100)
        _app.processEvents()

    def tearDown(self):
        self.win.close()
        self.tmp.cleanup()

    def _cell(self, r, c):
        ox, oy = self.cv._origin()
        s = self.cv._scale()
        return QPoint(ox + (c * 100 + 50) * s, oy + (r * 135 + 60) * s)

    def test_click_again_toggles_off(self):
        cv, vp = self.cv, self.cv.viewport()
        cv.set_source(None)  # 파일을 열면 첫 칸이 선택돼 있으므로 비우고 시작
        QTest.mouseClick(vp, Qt.LeftButton, Qt.NoModifier, self._cell(0, 0))
        self.assertEqual(cv.source(), CellRect.single(0, 0))
        QTest.mouseClick(vp, Qt.LeftButton, Qt.ControlModifier, self._cell(1, 2))
        self.assertEqual(cv.target(), CellRect.single(1, 2))
        QTest.mouseClick(vp, Qt.LeftButton, Qt.ShiftModifier, self._cell(1, 2))  # Target 다시 → 해제
        self.assertIsNone(cv.target())
        QTest.mouseClick(vp, Qt.LeftButton, Qt.NoModifier, self._cell(0, 0))  # Source 다시 → 해제
        self.assertIsNone(cv.source())
        # 다른 칸은 그냥 바뀜
        QTest.mouseClick(vp, Qt.LeftButton, Qt.NoModifier, self._cell(0, 1))
        QTest.mouseClick(vp, Qt.LeftButton, Qt.NoModifier, self._cell(2, 1))
        self.assertEqual(cv.source(), CellRect.single(2, 1))

    def test_esc_steps_back_and_ctrl_d_and_empty_click(self):
        cv, vp = self.cv, self.cv.viewport()
        cv.set_source(CellRect.single(0, 0))
        cv.set_target(CellRect.from_cells((1, 0), (1, 2)))
        cv.setFocus()
        QTest.keyClick(cv, Qt.Key_Escape)
        self.assertIsNone(cv.target())
        self.assertIsNotNone(cv.source())
        QTest.keyClick(cv, Qt.Key_Escape)
        self.assertIsNone(cv.source())
        cv.set_source(CellRect.single(0, 0))
        cv.set_target(CellRect.single(1, 1))
        self.win.deselect_all()
        self.assertIsNone(cv.source())
        self.assertIsNone(cv.target())
        self.assertEqual(self.win._last_msg[0], "status.deselected")
        cv.set_source(CellRect.single(0, 0))
        ox, oy = cv._origin()
        QTest.mouseClick(vp, Qt.LeftButton, Qt.NoModifier, QPoint(ox + 300 * cv._scale() + 40, oy + 40))  # 시트 오른쪽 빈 곳
        self.assertIsNone(cv.source())


    def test_ctrl_arrows_move_all_cells(self):
        w = self.win
        m = w.model
        m.arr[10, 10] = (255, 0, 0, 255)  # R1C1
        m.arr[150, 210] = (0, 255, 0, 255)  # R2C3
        w.canvas.set_source(CellRect.single(0, 0))
        w._update_all()
        w.nudge_all(2, 0)
        self.assertEqual(int(m.arr[10, 12, 3]), 255)
        self.assertEqual(int(m.arr[150, 212, 3]), 255)  # 선택 밖 셀도 같이
        self.assertEqual(int(m.arr[10, 10, 3]), 0)
        w.undo()
        self.assertEqual(int(m.arr[10, 10, 3]), 255)
        keys = {s.toString() for act in w.nudge_all_actions for s in act.shortcuts()}
        self.assertIn("Ctrl+Up", keys)
        self.assertIn("Ctrl+Shift+Left", keys)

    def test_wheel_scrolls_and_ctrl_alt_wheel_zooms(self):
        from PySide6.QtCore import QPointF
        from PySide6.QtGui import QWheelEvent
        cv = self.cv
        cv.set_zoom(800)
        _app.processEvents()

        def wheel(mods, dy=-120, dx=0):
            pos = QPointF(200, 200)
            ev = QWheelEvent(pos, cv.viewport().mapToGlobal(pos.toPoint()), QPoint(0, 0), QPoint(dx, dy),
                             Qt.NoButton, mods, Qt.NoScrollPhase, False)
            cv.wheelEvent(ev)

        z0, v0 = cv.zoom(), cv.verticalScrollBar().value()
        wheel(Qt.NoModifier)
        self.assertEqual(cv.zoom(), z0)
        self.assertGreater(cv.verticalScrollBar().value(), v0)
        h0 = cv.horizontalScrollBar().value()
        wheel(Qt.ShiftModifier)
        self.assertGreater(cv.horizontalScrollBar().value(), h0)
        wheel(Qt.ControlModifier)
        self.assertLess(cv.zoom(), z0)
        z1 = cv.zoom()
        wheel(Qt.AltModifier, dy=0, dx=120)  # Alt 는 가로 휠로 들어옴
        self.assertGreater(cv.zoom(), z1)


if __name__ == "__main__":
    unittest.main()
