"""3단계(v0.3.0): 마스크 경계 보정 브러시 / 셀 간 마스크 복사 / F·M 쌍 작업 / 파츠 참조 레이어."""

import os
import sys
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import layer_model  # noqa: E402
import mask_tools as mt  # noqa: E402
import pair_mode  # noqa: E402
import reference_layers  # noqa: E402
from history import History  # noqa: E402
from sheet_model import GridSpec  # noqa: E402


class MaskToolsTests(unittest.TestCase):
    def setUp(self):
        self.grid = GridSpec(2, 2, 10, 10)  # 시트 20x20
        self.alpha = np.full((20, 20), 255, dtype=np.uint8)
        self.alpha[:, 15:] = 0  # 오른쪽 5열은 투명
        self.mask = np.zeros((20, 20), dtype=np.uint8)
        self.all = (0, 20, 0, 20)

    def test_pencil_alpha_protection_and_eraser(self):
        d = mt.stamp(self.mask, self.alpha, 14, 5, 3, 2, self.all)  # x=13..15 → 15는 투명
        self.assertIsNotNone(d)
        self.assertTrue((self.mask[4:7, 13:15] == 2).all())
        self.assertTrue((self.mask[4:7, 15] == 0).all())  # 알파 보호
        mt.stamp(self.mask, self.alpha, 14, 5, 3, 0, self.all)  # 지우개
        self.assertEqual(int(self.mask.sum()), 0)

    def test_stroke_line_is_continuous_and_scope_limits(self):
        cell00 = mt.scope_bounds(self.grid, mt.SCOPE_CELL, (0, 0), None)
        mt.stroke_line(self.mask, self.alpha, (0, 3), (19, 3), 1, 1, cell00)
        self.assertTrue((self.mask[3, :10] == 1).all())
        self.assertTrue((self.mask[3, 10:] == 0).all())  # 셀 밖은 건드리지 않음

    def test_flood_fill_respects_region_alpha_and_bounds(self):
        self.mask[:, :8] = 1
        d = mt.flood_fill(self.mask, self.alpha, 2, 2, 3, self.all)
        self.assertEqual(d, (0, 20, 0, 8))
        self.assertTrue((self.mask[:, :8] == 3).all())
        self.assertTrue((self.mask[:, 8:] == 0).all())
        # 투명 픽셀 클릭은 무시, 같은 번호면 변화 없음
        self.assertIsNone(mt.flood_fill(self.mask, self.alpha, 17, 2, 2, self.all))
        self.assertIsNone(mt.flood_fill(self.mask, self.alpha, 2, 2, 3, self.all))
        # 범위(현재 셀)로 제한
        m2 = np.zeros_like(self.mask)
        mt.flood_fill(m2, self.alpha, 2, 2, 1, mt.scope_bounds(self.grid, mt.SCOPE_CELL, (0, 0), None))
        self.assertEqual(int((m2 == 1).sum()), 100)

    def test_rect_replace_and_missing(self):
        d = mt.fill_rect(self.mask, self.alpha, (13, 2), (4, 6), 4, self.all)  # 모서리 순서 무관
        self.assertEqual(d, (2, 7, 4, 14))
        self.assertTrue((self.mask[2:7, 4:14] == 4).all())
        rect, n = mt.replace_index(self.mask, self.alpha, 4, 2, self.all)
        self.assertEqual(n, 5 * 10)
        self.assertTrue((self.mask[2:7, 4:14] == 2).all())
        # 누락(0) → 1: 투명한 곳은 건드리지 않는다
        rect, n = mt.replace_index(self.mask, self.alpha, 0, 1, self.all)
        self.assertEqual(int((self.mask == 1).sum()), n)
        self.assertTrue((self.mask[:, 15:] == 0).all())
        self.assertEqual(mt.replace_index(self.mask, self.alpha, 3, 3, self.all), (None, 0))

    def test_paste_mask_block_alpha_zero_becomes_index0(self):
        block = np.full((10, 10), 3, dtype=np.uint8)
        d = mt.paste_mask_block(self.mask, self.alpha, self.grid, (0, 1), block)  # 오른쪽 위 셀
        self.assertEqual(d, (0, 10, 10, 20))
        self.assertTrue((self.mask[:10, 10:15] == 3).all())
        self.assertTrue((self.mask[:10, 15:] == 0).all())
        self.assertIsNone(mt.paste_mask_block(self.mask, self.alpha, self.grid, (1, 1), np.zeros((10, 20), np.uint8)))

    def test_recorder_makes_undoable_edit(self):
        arr = np.zeros((20, 20, 4), dtype=np.uint8)
        arr[..., 3] = 255

        class Doc:
            pass
        doc = Doc()
        doc.arr, doc.mask, doc.has_mask = arr, self.mask, True
        rec = mt.MaskRecorder(arr, self.mask)
        rec.add(mt.stamp(self.mask, arr[..., 3], 5, 5, 3, 2, self.all))
        rec.add(mt.stamp(self.mask, arr[..., 3], 9, 9, 1, 2, self.all))
        edit = rec.finish("edit.paint")
        self.assertEqual((edit.y0, edit.x0, edit.mask_after.shape), (4, 4, (6, 6)))
        self.assertIsNone(mt.MaskRecorder(arr, self.mask).finish("edit.paint"))
        hist = History()
        edit.has_mask_before = edit.has_mask_after = True
        hist.do(doc, edit)
        painted = self.mask.copy()
        hist.undo(doc)
        self.assertEqual(int(self.mask.sum()), 0)
        hist.redo(doc)
        self.assertTrue(np.array_equal(self.mask, painted))


class PairAndPartsTests(unittest.TestCase):
    def test_pair_names_and_gender(self):
        self.assertEqual(pair_mode.other_gender("F"), "M")
        self.assertEqual(pair_mode.other_gender("M"), "F")
        self.assertIsNone(pair_mode.other_gender("C"))
        self.assertEqual(pair_mode.counterpart_name("H1WK_P001F", "M"), "H1WK_P001M")
        self.assertEqual(pair_mode.counterpart_name("H1WK_P001", "F"), "H1WK_P001F")
        self.assertFalse(pair_mode.can_start("C"))

    def test_part_order_follows_composite_order(self):
        # Body0 → Eyes → Outfit → Hair → Helmet → Weapon
        self.assertTrue(layer_model.part_is_above("Hair", "Outfit"))
        self.assertTrue(layer_model.part_is_above("Weapon", "Hair"))
        self.assertFalse(layer_model.part_is_above("Eyes", "Outfit"))
        refs = [layer_model.RefLayer(name=p, kind=layer_model.KIND_EXTRA, part=p, uid=i + 1)
                for i, p in enumerate(("Weapon", "Eyes", "Hair"))]
        layer_model.apply_part_order(refs, "Outfit")
        self.assertEqual([r.above for r in refs], [True, False, True])
        body = layer_model.RefLayer(name="b", kind=layer_model.KIND_BODY)
        self.assertEqual([r.part for r in layer_model.sort_for_paint([body] + refs)],
                         ["", "Eyes", "Hair", "Weapon"])

    def test_detect_and_list_parts_follow_real_unity_names(self):
        self.assertEqual(reference_layers.detect_part("H1WK_P001F"), "Outfit")
        self.assertEqual(reference_layers.detect_part("HairC17_H1WK"), "Hair")
        self.assertEqual(reference_layers.detect_part("EyesC03_E4_H1WK_Open"), "Eyes")
        with tempfile.TemporaryDirectory() as d:
            for part, names in {
                "Hair": ["HairC01_H1WK.png", "HairC02_H1WK.png", "HairC17_H1WK.png", "HairC24_H1WK.png",
                         "HairC01_H2.png", "HairC01_H1WK_M.png"],
                "Eyes": ["EyesC02_E1_H1WK_Open.png", "EyesC01_E2_H1WK_Open.png", "EyesC01_E1_H1WK_Open.png",
                         "EyesC01_E1_H1WK_Closed.png"],
                "Weapon": ["Weapon_H1WK201Z.png"],
                "Helmet": ["Helmet_A_H1WK.png", "Helmet_B_H1WK.png"],
            }.items():
                os.makedirs(os.path.join(d, part))
                for fn in names:
                    Image.new("RGBA", (4, 4)).save(os.path.join(d, part, fn))
            # Hair: 체형 문자가 없고 번호로 구분 (C01~16 여 / C17~24 남)
            self.assertEqual(reference_layers.list_part_files(d, "Hair", "F", "H1WK"),
                             ["HairC01_H1WK.png", "HairC02_H1WK.png"])
            self.assertEqual(reference_layers.list_part_files(d, "Hair", "M", "H1WK"),
                             ["HairC17_H1WK.png", "HairC24_H1WK.png"])
            self.assertTrue(reference_layers.auto_part_file(d, "Hair", "F", "H1WK").endswith("HairC01_H1WK.png"))
            self.assertTrue(reference_layers.auto_part_file(d, "Hair", "M", "H1WK").endswith("HairC17_H1WK.png"))
            # Eyes: 열림(Open) 시트 중 첫 모양·첫 눈색
            self.assertTrue(reference_layers.auto_part_file(d, "Eyes", "F", "H1WK").endswith("EyesC01_E1_H1WK_Open.png"))
            # 후보 하나면 그것 / 기본값 규칙이 없는 파츠가 여럿이면 자동 선택 안 함
            self.assertTrue(reference_layers.auto_part_file(d, "Weapon", "F", "H1WK").endswith("Weapon_H1WK201Z.png"))
            self.assertIsNone(reference_layers.auto_part_file(d, "Helmet", "F", "H1WK"))


# ---------------------------------------------------------------------- #
#  UI                                                                     #
# ---------------------------------------------------------------------- #
from PySide6.QtCore import QPoint, Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

import export_utils  # noqa: E402
import i18n  # noqa: E402
from autosave import AutoSaver  # noqa: E402
from config_manager import ConfigManager  # noqa: E402
from main_window import MainWindow  # noqa: E402

_app = QApplication.instance() or QApplication([])


class Stage3UiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name
        self.cfg = ConfigManager(os.path.join(self.dir, "config.json"))
        self.cfg.set("body_dir", os.path.join(self.dir, "Allies", "Body"))
        self.cfg.set("save_dir", os.path.join(self.dir, "out"))
        os.makedirs(os.path.join(self.dir, "Allies", "Body"))
        rng = np.random.default_rng(1)
        arr = np.zeros((540, 300, 4), dtype=np.uint8)
        arr[..., :3] = rng.integers(0, 256, (540, 300, 3), dtype=np.uint8)
        arr[..., 3] = 255
        arr[:, 280:, 3] = 0
        self.arr = arr
        self.sheet = os.path.join(self.dir, "H1WK_P001F.png")
        Image.fromarray(arr, "RGBA").save(self.sheet)
        i18n.set_language("ko")
        self.win = MainWindow(self.cfg, AutoSaver(os.path.join(self.dir, "autosave")))
        self.win.resize(1400, 860)
        self.win.show()
        _app.processEvents()
        self.win._ask_pair_body = lambda name: ("go", "")  # 모달 대화상자 대신 (테스트에서 개별 지정)
        self.assertTrue(self.win.open_file(self.sheet))
        m = self.win.model
        idx = np.zeros(m.mask.shape, dtype=np.uint8)
        idx[:, :140] = 1
        idx[:, 140:] = 2
        m.set_mask(np.where(m.arr[..., 3] > 0, idx, 0))
        self.win._refresh_mask_view()
        self.win._update_all()

    def tearDown(self):
        if self.win.model is not None:
            self.win.model.mark_saved()
            self.win.model.forced_dirty = False
        self.win.close()
        self.tmp.cleanup()
        i18n.set_language("ko")

    def test_pencil_stroke_is_one_undo_step_and_persists_after_save_and_reopen(self):
        w = self.win
        w._set_paint_tool("pencil")
        w._set_paint_index(3)
        self.assertEqual(self.cfg.get("mask_view.mode"), "overlay")  # 도구를 켜면 오버레이 자동 ON
        w._on_paint_pressed(10, 10)
        w._on_paint_moved(30, 10)
        w._on_paint_released(30, 10)
        m = w.model
        self.assertTrue((m.mask[10, 10:31] == 3).all())
        self.assertEqual(len(m.history._edits), 1)  # 한 획 = 편집 하나
        w.undo()
        self.assertEqual(int(m.mask[10, 10:31].max()), 1)
        w.redo()
        self.assertTrue((m.mask[10, 10:31] == 3).all())
        self.assertTrue(m.dirty)
        # 저장 → 다시 열기 → 값이 남아 있다 (3단계 완료 기준)
        m.save_path = os.path.join(self.dir, "out", "H1WK_P001F.png")
        self.assertTrue(w.save())
        mask_png = np.array(Image.open(os.path.join(self.dir, "out", "H1WK_P001F_M.png")).convert("L"))
        self.assertTrue((mask_png[10, 10:31] == 192).all())  # 3 → 192 (CHR-ART.5 §3.3)
        w.model.mark_saved()
        self.assertTrue(w.open_file(m.save_path))
        self.assertTrue((w.model.mask[10, 10:31] == 3).all())

    def test_mouse_drag_paints_and_alt_click_selects_source(self):
        w = self.win
        w._set_paint_tool("pencil")
        w._set_paint_index(4)
        cv = w.canvas
        s, (ox, oy) = cv._scale(), cv._origin()
        pt = lambda x, y: QPoint(ox + int((x + 0.5) * s), oy + int((y + 0.5) * s))  # noqa: E731
        QTest.mousePress(cv.viewport(), Qt.LeftButton, Qt.NoModifier, pt(20, 20))
        QTest.mouseMove(cv.viewport(), pt(40, 20))
        QTest.mouseRelease(cv.viewport(), Qt.LeftButton, Qt.NoModifier, pt(40, 20))
        self.assertTrue((w.model.mask[20, 20:41] == 4).all())
        before = cv.source()
        QTest.mouseClick(cv.viewport(), Qt.LeftButton, Qt.AltModifier, pt(250, 450))  # R4C3 근처
        self.assertNotEqual(cv.source(), before)  # Alt+클릭 = Source 선택 (칠하지 않음)
        self.assertEqual(int(w.model.mask[450, 250]), 2)

    def test_alpha_protection_scope_and_fill_undo(self):
        w = self.win
        m = w.model
        w._set_paint_tool("fill")
        w._set_paint_index(4)
        w._on_paint_pressed(290, 10)  # 투명 영역 클릭은 무시
        w._on_paint_released(290, 10)
        self.assertEqual(len(m.history._edits), 0)
        w._on_paint_pressed(10, 10)  # 현재 셀(R1C1)의 영역 1을 4로
        w._on_paint_released(10, 10)
        g = m.grid
        self.assertTrue((m.mask[:g.cell_h, :g.cell_w][m.arr[:g.cell_h, :g.cell_w, 3] > 0] == 4).all())
        self.assertEqual(int(m.mask[g.cell_h, 10]), 1)  # 다음 행 셀은 그대로
        w.undo()
        self.assertEqual(int(m.mask[10, 10]), 1)

    def test_rect_eraser_and_replace(self):
        w = self.win
        m = w.model
        w._set_paint_tool("rect")
        w._set_paint_index(3)
        w._on_paint_pressed(5, 5)
        w._on_paint_released(24, 14)
        self.assertTrue((m.mask[5:15, 5:25] == 3).all())
        w._set_paint_tool("eraser")
        w._on_paint_pressed(5, 5)
        w._on_paint_released(5, 5)
        self.assertEqual(int(m.mask[5, 5]), 0)
        # 번호 교체: 시트 전체에서 2 → 1
        w.cb_scope.setCurrentIndex(w.cb_scope.findData("sheet"))
        w.cb_rep_from.setCurrentIndex(2)
        w.cb_rep_to.setCurrentIndex(1)
        w.replace_region()
        self.assertEqual(int((m.mask == 2).sum()), 0)
        w.undo()
        self.assertGreater(int((m.mask == 2).sum()), 0)

    def test_mask_copy_to_target_and_row_keep_artwork(self):
        w = self.win
        m = w.model
        g = m.grid
        art = m.arr.copy()
        m.mask[0:g.cell_h, 0:g.cell_w] = np.where(m.arr[0:g.cell_h, 0:g.cell_w, 3] > 0, 3, 0)
        w.canvas.set_source(__import__("sheet_model").CellRect.single(0, 0))
        w.mask_copy_to_target()  # Target 없음 → 경고만
        self.assertEqual(len(m.history._edits), 0)
        w.canvas.set_target(__import__("sheet_model").CellRect.single(2, 0))
        w.mask_copy_to_target()
        blk = m.mask[2 * g.cell_h:3 * g.cell_h, 0:g.cell_w]
        self.assertTrue((blk[m.arr[2 * g.cell_h:3 * g.cell_h, 0:g.cell_w, 3] > 0] == 3).all())
        w.mask_copy_to_row()
        self.assertTrue((m.mask[0:g.cell_h, g.cell_w:2 * g.cell_w][
            m.arr[0:g.cell_h, g.cell_w:2 * g.cell_w, 3] > 0] == 3).all())
        self.assertTrue(np.array_equal(m.arr, art))  # 그림은 그대로
        w.undo()
        w.undo()
        self.assertEqual(int(m.mask[2 * g.cell_h + 5, 5]), 1)

    def test_paint_without_mask_warns(self):
        w = self.win
        m = w.model
        m.has_mask = False
        w._set_paint_tool("pencil")
        w._on_paint_pressed(5, 5)
        self.assertIsNone(w._stroke)
        self.assertEqual(w._last_msg[0], "status.paint_need_mask")

    def test_pair_mode_clones_to_other_gender_and_keeps_reference_readonly(self):
        w = self.win
        m = w.model
        m.mask[5, 5] = 4
        w.canvas.set_source(__import__("sheet_model").CellRect.single(0, 0))
        w.copy_cells()
        m.mark_saved()
        w.start_pair()
        self.assertIsNotNone(w.pair)
        nm = w.model
        self.assertEqual((nm.gender, nm.name), ("M", "H1WK_P001M"))
        self.assertTrue(nm.has_mask and int(nm.mask[5, 5]) == 4)
        self.assertTrue(np.array_equal(nm.arr, self.arr))
        self.assertTrue(w.pair_box.isVisible())
        self.assertTrue(nm.dirty and not nm.save_path)  # 새 문서
        self.assertEqual(w._last_msg[0], "status.pair_need_body")  # Base Body 없음 경고
        # 체형이 다른 붙여넣기: 쌍 작업 중에는 교차 체형 경고창이 뜨지 않는다
        w.paste_cells()
        self.assertEqual(w._last_msg[0], "status.pasted")
        # 읽기 전용 참조 캔버스는 선택·붓질을 받지 않는다
        cv = w.pair_canvas
        QTest.mouseClick(cv.viewport(), Qt.LeftButton, Qt.NoModifier, QPoint(100, 100))
        self.assertIsNone(cv.source())
        # 참조 수정 시 편집 문서에 영향 없음, 겹쳐보기로 전환
        w.cb_pair_view.setCurrentIndex(w.cb_pair_view.findData("overlay"))
        w._on_pair_view_activated(0)
        self.assertFalse(w.pair_box.isVisible())
        self.assertTrue(any(r.kind == "pair" for r in w._all_refs()))
        self.assertEqual(sum(1 for r in w.extra_layers if r.kind == "extra"), len(w.extra_layers))
        w.end_pair()
        self.assertIsNone(w.pair)
        self.assertFalse(any(r.kind == "pair" for r in w._all_refs()))

    def test_pair_start_asks_before_when_body_missing(self):
        w = self.win
        w.model.mark_saved()
        asked = []
        w._ask_pair_body = lambda name: (asked.append(name), ("cancel", ""))[1]
        w.start_pair()
        self.assertEqual(asked, ["BodyM_H1WK.png"])  # 시작 전에 묻는다
        self.assertIsNone(w.pair)  # 취소하면 아무것도 바뀌지 않음
        self.assertEqual(w.model.gender, "F")
        body = os.path.join(self.dir, "BodyM_custom.png")
        Image.new("RGBA", (300, 540), (0, 255, 0, 80)).save(body)
        w._ask_pair_body = lambda name: ("pick", body)
        w.start_pair()
        self.assertIsNotNone(w.pair)
        self.assertEqual(w.body_override, body)  # 직접 지정한 Body 적용
        self.assertIsNotNone(w.body_layer.image)
        self.assertEqual(w._last_msg[0], "status.pair_started_mask")  # 마스크 확인 안내
        self.assertEqual(self.cfg.get("mask_view.mode"), "overlay")

    def test_pair_start_requires_f_or_m(self):
        w = self.win
        w.model.gender = "C"
        w.start_pair()
        self.assertIsNone(w.pair)
        self.assertEqual(w._last_msg[0], "status.pair_need_gender")

    def test_pair_zoom_and_scroll_sync_without_loop(self):
        w = self.win
        w.start_pair()
        w.canvas.set_zoom(400)
        _app.processEvents()
        self.assertEqual(w.pair_canvas.zoom(), 400)
        w.pair_canvas.set_zoom(300)
        _app.processEvents()
        self.assertEqual(w.canvas.zoom(), 300)
        w.canvas.horizontalScrollBar().setValue(50)
        self.assertEqual(w.pair_canvas.horizontalScrollBar().value(),
                         min(50, w.pair_canvas.horizontalScrollBar().maximum()))

    def test_parts_layers_order_and_limit(self):
        w = self.win
        allies = os.path.join(self.dir, "Allies")
        names = {"Eyes": "EyesC01_E1_H1WK_Open.png", "Hair": "HairC01_H1WK.png", "Weapon": "Weapon_H1WK201Z.png"}
        for part, fn in names.items():
            os.makedirs(os.path.join(allies, part), exist_ok=True)
            Image.new("RGBA", (300, 540), (255, 0, 0, 100)).save(os.path.join(allies, part, fn))
        w.auto_load_parts()
        parts = {r.part: r for r in w.extra_layers}
        self.assertEqual(set(parts), {"Eyes", "Hair", "Weapon"})
        self.assertFalse(parts["Eyes"].above)  # 편집 파츠(Outfit) 아래
        self.assertTrue(parts["Hair"].above and parts["Weapon"].above)
        # 편집 파츠를 Hair로 바꾸면 위/아래가 다시 정해진다
        w.cb_edit_part.setCurrentIndex(w.cb_edit_part.findData("Hair"))
        w._on_edit_part(0)
        self.assertFalse(parts["Eyes"].above)
        self.assertTrue(parts["Weapon"].above)
        w.cb_edit_part.setCurrentIndex(w.cb_edit_part.findData("Weapon"))
        w._on_edit_part(0)
        self.assertFalse(parts["Hair"].above)
        order = [r.part for r in w._all_refs() if r.part]
        self.assertEqual(order, ["Eyes", "Hair", "Weapon"])  # §7 합성 순서
        # 다시 불러와도 중복 추가되지 않는다
        n = len(w.extra_layers)
        w.auto_load_parts()
        self.assertEqual(len(w.extra_layers), n)

    def test_actions_shortcuts_do_not_conflict(self):
        from collections import Counter
        keys = [a.shortcut().toString() for a in self.win.findChildren(type(self.win.a_save)) if a.shortcut().toString()]
        dup = [k for k, n in Counter(keys).items() if n > 1]
        self.assertEqual(dup, [])


if __name__ == "__main__":
    unittest.main()
