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
from cell_editor import PAD, SLOT_COUNT, TOOL_FILL, TOOL_MOVE, TOOL_PAINT, CellEditDialog  # noqa: E402
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


if __name__ == "__main__":
    unittest.main()
