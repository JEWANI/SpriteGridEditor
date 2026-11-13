"""2단계: 회색+마스크 변환 / 밝기 정규화 / Nudge / 틴트 미리보기 / 통계 / 저장 전 검증 / 마스크 저장·불러오기."""

import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import cell_ops  # noqa: E402
import mask_convert as mc  # noqa: E402
import sheet_model as sm  # noqa: E402
from autosave import AutoSaver  # noqa: E402
from history import History  # noqa: E402

H1 = sm.GridSpec(3, 4, 100, 135)
OUTFIT_DIR = r"D:\프로젝트\STELLAR PROFITS\Assets\Resources\Art\UnitSprites\Allies\Outfit"
VALUES = {0, 64, 128, 192, 255}

SHIRT = [(40, 70, 160), (60, 95, 190), (25, 50, 120)]  # 파란 상의 (명암 3단)
PANTS = [(150, 40, 40), (180, 60, 60), (110, 25, 25)]  # 빨간 하의
BUTTON = (240, 220, 60)


def outfit_sheet() -> np.ndarray:
    """H1 시트: 모든 셀에 상의(위)·하의(아래), 명암 줄무늬, 정면 셀에만 단추."""
    a = np.zeros((540, 300, 4), np.uint8)
    for r in range(4):
        for c in range(3):
            y0, x0 = r * 135, c * 100
            for i in range(30, 70):
                a[y0 + i, x0 + 30:x0 + 70, :3] = SHIRT[i % 3]
            for i in range(70, 110):
                a[y0 + i, x0 + 35:x0 + 65, :3] = PANTS[i % 3]
            a[y0 + 30:y0 + 110, x0 + 30:x0 + 70, 3] = np.where(
                a[y0 + 30:y0 + 110, x0 + 30:x0 + 70, :3].any(axis=-1), 255, 0)
    a[50:53, 49:52, :3] = BUTTON  # R1C1 단추 3×3
    return a


class GrayTests(unittest.TestCase):
    def test_gray_keeps_lightness(self):
        rgb = np.array([[[200, 30, 40, 255], [20, 160, 90, 255], [90, 90, 250, 255]]], np.uint8)
        g = mc.to_gray(rgb)
        L_color = mc.rgb_to_lab(rgb[..., :3])[..., 0]
        L_gray = mc.rgb_to_lab(np.repeat(g[..., None], 3, axis=-1))[..., 0]
        self.assertTrue(np.all(np.abs(L_color - L_gray) < 0.7))

    def test_normalize_hits_rule(self):
        a = outfit_sheet()
        out = mc.make_gray_rgba(a, normalize=True)
        op = out[..., 3] > 0
        b = out[..., 0][op] / 255.0
        self.assertAlmostEqual(np.percentile(b, 2), 0.42, delta=0.02)
        self.assertAlmostEqual(np.percentile(b, 98), 1.0, delta=0.02)
        self.assertAlmostEqual(np.median(b), 0.72, delta=0.03)
        self.assertTrue(np.array_equal(out[..., 3], a[..., 3]))  # 알파 유지
        self.assertTrue(np.all(out[..., 0] == out[..., 1]) and np.all(out[..., 1] == out[..., 2]))
        self.assertTrue(np.all(out[~op, :3] == 0))

    def test_auto_off_for_gray_in_rule(self):
        a = outfit_sheet()
        normalized = mc.make_gray_rgba(a, True)
        self.assertFalse(mc.should_normalize(mc.brightness_stats(normalized)))
        self.assertTrue(mc.should_normalize(mc.brightness_stats(a)))  # 컬러 → 켬


class SplitTests(unittest.TestCase):
    def test_kmeans_separates_shirt_pants_across_cells(self):
        a = outfit_sheet()
        cl = mc.kmeans(a, 2, l_weight=0.3)
        self.assertEqual(cl.k, 2)
        idx = mc.labels_to_index(cl.labels, [1, 2])
        # 모든 셀에서 상의 픽셀은 같은 번호, 하의는 다른 같은 번호 (시트 공통 중심)
        shirt = {int(idx[r * 135 + 40, c * 100 + 40]) for r in range(4) for c in range(3)}
        pants = {int(idx[r * 135 + 90, c * 100 + 50]) for r in range(4) for c in range(3)}
        self.assertEqual(len(shirt), 1)
        self.assertEqual(len(pants), 1)
        self.assertNotEqual(shirt, pants)
        self.assertEqual(int(idx[0, 0]), 0)  # 투명 → 0
        self.assertTrue(set(np.unique(mc.index_to_png(idx)).tolist()) <= VALUES)

    def test_eyedropper_reassign_keeps_order(self):
        a = outfit_sheet()
        cl = mc.assign_to_colors(a, [PANTS[0], SHIRT[0], BUTTON])
        self.assertEqual(cl.centers_rgb[0], PANTS[0])
        self.assertEqual(int(cl.labels[90, 50]), 0)
        self.assertEqual(int(cl.labels[40, 40]), 1)
        self.assertEqual(int(cl.labels[51, 50]), 2)  # 단추

    def test_horizontal_split(self):
        a = outfit_sheet()
        idx = mc.horizontal_split(a, H1, 70, 1, 2)
        self.assertEqual(int(idx[135 + 40, 40]), 1)
        self.assertEqual(int(idx[135 + 90, 50]), 2)
        self.assertEqual(int(idx[135 + 5, 5]), 0)

    def test_despeckle_absorbs_single_pixel(self):
        op = np.ones((7, 7), bool)
        idx = np.ones((7, 7), np.uint8)
        idx[3, 3] = 3
        out = mc.despeckle(idx, op)
        self.assertEqual(int(out[3, 3]), 1)
        big = np.ones((7, 7), np.uint8)
        big[2:5, 2:5] = 3  # 3×3 덩어리는 유지
        self.assertEqual(int(mc.despeckle(big, op)[3, 3]), 3)

    def test_png_index_roundtrip_and_snap(self):
        idx = np.array([[0, 1, 2, 3, 4]], np.uint8)
        png = mc.index_to_png(idx)
        self.assertEqual(png.tolist(), [[0, 64, 128, 192, 255]])
        back, snapped = mc.png_to_index(np.array([[0, 70, 120, 200, 250]], np.uint8))
        self.assertEqual(back.tolist(), [[0, 1, 2, 3, 4]])
        self.assertEqual(snapped, 4)


class TintStatsTests(unittest.TestCase):
    def test_tint_matches_shader_formula(self):
        gray = np.zeros((1, 3, 4), np.uint8)
        gray[..., :3] = 200
        gray[..., 3] = 255
        idx = np.array([[0, 1, 2]], np.uint8)
        out = mc.tint(gray, idx, [(255, 0, 0), (0, 128, 255), (0, 0, 0), (0, 0, 0)], gain=1.0)
        self.assertEqual(out[0, 0, :3].tolist(), [200, 200, 200])  # 0 = 회색 그대로
        self.assertEqual(out[0, 1, :3].tolist(), [200, 0, 0])
        self.assertEqual(out[0, 2, :3].tolist(), [0, 100, 200])
        bright = mc.tint(gray, idx, [(255, 255, 255)] * 4, gain=1.5)  # saturate(200*1.5)=255
        self.assertEqual(bright[0, 1, :3].tolist(), [255, 255, 255])

    def test_shade_report_warns_dark_color(self):
        a = mc.make_gray_rgba(outfit_sheet(), True)
        idx = mc.labels_to_index(mc.kmeans(outfit_sheet(), 2, 0.3).labels, [1, 2])
        dark = [(20, 20, 25), (240, 240, 240), (0, 0, 0), (0, 0, 0)]
        rep = {s.region: s for s in mc.shade_report(mc.tint(a, idx, dark), idx, dark)}
        self.assertTrue(rep[1].warn)  # V < 0.30
        self.assertFalse(rep[2].warn)

    def test_stats_missing_and_consistency(self):
        a = outfit_sheet()
        idx = mc.labels_to_index(mc.assign_to_colors(a, [SHIRT[0], PANTS[0], BUTTON]).labels, [1, 2, 3])
        idx[135 + 30:135 + 70, 130:170] = 0  # R2C2 상의 영역 누락
        st = mc.mask_stats(idx, a[..., 3], H1)
        self.assertGreater(st.missing, 0)
        self.assertEqual(st.cell_counts[1, 1, 1], 0)
        self.assertIn((1, 1, 1), st.inconsistent)  # 상의가 R2C2에만 없음
        self.assertTrue(any(r == 3 for r, _ri, _ci in st.inconsistent))  # 단추는 R1C1에만 → 다른 셀 경고

    def test_validate_mask(self):
        a = outfit_sheet()
        idx = np.ones((540, 300), np.uint8)  # 투명한 곳까지 1
        fixed, issues = mc.validate_mask(a, idx, 0, 10)
        self.assertEqual(int(fixed[0, 0]), 0)
        self.assertIn("validate.mask_alpha_fixed", [i.key for i in issues])
        miss = np.zeros((540, 300), np.uint8)
        _f, issues = mc.validate_mask(a, miss, 3, 10)
        keys = [i.key for i in issues]
        self.assertIn("validate.mask_missing", keys)
        self.assertIn("validate.mask_snapped", keys)
        _f, issues = mc.validate_mask(a, np.zeros((10, 10), np.uint8), 0, 10)
        self.assertEqual(issues[0].level, "error")

    def test_mask_path(self):
        self.assertEqual(mc.mask_path_for(r"C:\a.b\H1WK_P001F.png"), r"C:\a.b\H1WK_P001F_M.png")


class EditWithMaskTests(unittest.TestCase):
    def model(self):
        m = sm.SheetModel(outfit_sheet(), H1, "H1WK", "H1WK_P001F")
        m.set_mask(mc.horizontal_split(m.arr, H1, 70))
        m.history = History()
        return m

    def test_mirror_and_nudge_move_mask_too(self):
        m = self.model()
        rgba = cell_ops.copy_cells(m.arr, H1, sm.CellRect.single(0, 0))
        mask = cell_ops.copy_cells(m.mask, H1, sm.CellRect.single(0, 0))
        self.assertTrue(np.array_equal(cell_ops.mirror_block(mask, H1), np.fliplr(mask)))
        before = m.mask.copy()
        m.history.do(m, cell_ops.make_nudge_edit(m.arr, H1, sm.CellRect.single(0, 0), 2, 1, "n", m.mask))
        self.assertTrue(np.array_equal(m.mask[1:135, 2:100], before[0:134, 0:98]))
        self.assertTrue(np.array_equal(m.arr[1:135, 2:100], rgba[0:134, 0:98]))
        m.history.undo(m)
        self.assertTrue(np.array_equal(m.mask, before))

    def test_paste_alpha_protection(self):
        m = self.model()
        block = np.zeros((135, 100, 4), np.uint8)
        mblock = np.full((135, 100), 2, np.uint8)
        m.history.do(m, cell_ops.make_paste_edit(m.arr, H1, (1, 1), block, "p", m.mask, mblock))
        self.assertEqual(int(m.mask[135 + 50, 150]), 0)

    def test_full_replace_undo_restores_has_mask(self):
        from history import make_edit
        m = sm.SheetModel(outfit_sheet(), H1)
        self.assertFalse(m.has_mask)
        gray = mc.make_gray_rgba(m.arr, True)
        idx = mc.horizontal_split(m.arr, H1, 70)
        e = make_edit(m.arr, 0, 0, gray, "convert", m.mask, idx)
        e.has_mask_before, e.has_mask_after = False, True
        orig = m.arr.copy()
        m.history.do(m, e)
        self.assertTrue(m.has_mask)
        m.history.undo(m)
        self.assertFalse(m.has_mask)
        self.assertTrue(np.array_equal(m.arr, orig))
        m.history.redo(m)
        self.assertTrue(m.has_mask and np.array_equal(m.mask, idx))

    def test_autosave_mask_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            s = AutoSaver(d, pid=99999)
            idx = mc.horizontal_split(outfit_sheet(), H1, 70)
            snap = s.save(outfit_sheet(), {"name": "x"}, 2, idx)
            self.assertEqual(len(s.list_snapshots()), 1)  # _mask.png 는 스냅샷으로 세지 않음
            self.assertTrue(np.array_equal(s.load_mask(snap), idx))
            s.delete(snap)
            self.assertFalse(os.path.exists(snap.mask_path))


class _QtCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        import i18n
        from config_manager import ConfigManager
        from main_window import MainWindow
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name
        self.cfg = ConfigManager(os.path.join(self.dir, "config.json"))
        self.cfg.set("body_dir", self.dir)
        self.cfg.set("save_dir", os.path.join(self.dir, "out"))
        i18n.set_language("ko")
        self.win = MainWindow(self.cfg, AutoSaver(os.path.join(self.dir, "autosave")))

    def tearDown(self):
        if self.win.model is not None:
            self.win.model.mark_saved()
        self.win.close()
        self.tmp.cleanup()

    def open_sheet(self, arr, name="H1WK_P001F.png"):
        path = os.path.join(self.dir, name)
        Image.fromarray(arr).save(path)
        self.assertTrue(self.win.open_file(path))
        return path


class UiFlowTests(_QtCase):
    def convert(self, setup=None):
        def fake_exec(dlg):
            if setup:
                setup(dlg)
            dlg._accept()
            return 1
        with mock.patch("convert_dialog.ConvertDialog.exec", fake_exec):
            self.win.convert_to_gray_mask()

    def test_convert_save_reopen(self):
        self.open_sheet(outfit_sheet())
        self.convert()
        m = self.win.model
        self.assertTrue(m.has_mask)
        self.assertTrue(np.all(m.arr[..., 0] == m.arr[..., 1]))  # 회색
        self.assertTrue(self.win.a_compare.isEnabled())
        # 저장 → _M.png 가 L 모드, 값 집합 규격, 크기 동일
        m.save_path = os.path.join(self.dir, "out", "H1WK_P001F.png")
        with mock.patch("main_window.QMessageBox.warning", return_value=mock.ANY):
            self.assertTrue(self.win.save())
        mpath = os.path.join(self.dir, "out", "H1WK_P001F_M.png")
        with Image.open(mpath) as im:
            self.assertEqual(im.mode, "L")
            self.assertEqual(im.size, (300, 540))
            vals = set(np.unique(np.array(im)).tolist())
        self.assertTrue(vals <= VALUES and len(vals) >= 2)
        saved_mask = m.mask.copy()
        # 다시 열면 마스크가 같이 들어온다
        self.assertTrue(self.win.open_file(m.save_path))
        self.assertTrue(self.win.model.has_mask)
        self.assertTrue(np.array_equal(self.win.model.mask, saved_mask))

    def test_convert_undo_redo(self):
        self.open_sheet(outfit_sheet())
        orig = self.win.model.arr.copy()
        self.convert()
        self.win.undo()
        self.assertFalse(self.win.model.has_mask)
        self.assertTrue(np.array_equal(self.win.model.arr, orig))
        self.win.redo()
        self.assertTrue(self.win.model.has_mask)

    def test_hsplit_and_none_modes(self):
        self.open_sheet(outfit_sheet())
        self.convert(lambda d: d.rb_none.setChecked(True))
        self.assertFalse(self.win.model.has_mask)
        self.convert(lambda d: (d.rb_hsplit.setChecked(True), d.sp_y.setValue(70)))
        self.assertEqual(int(self.win.model.mask[90, 50]), 2)

    def test_mask_views_and_nudge(self):
        self.open_sheet(outfit_sheet())
        self.convert()
        for mode in ("overlay", "only", "off"):
            self.win._set_mask_view(mode)
        self.win.a_tint_view.setChecked(True)
        self.win.a_missing.setChecked(True)
        self.assertIsNotNone(self.win.canvas._display_image)
        self.assertIsNotNone(self.win.canvas._overlay_image)
        self.win.canvas.set_source(sm.CellRect.single(0, 0))
        before = self.win.model.mask.copy()
        self.win.nudge(1, 0)
        self.assertTrue(np.array_equal(self.win.model.mask[:135, 1:100], before[:135, 0:99]))

    def test_compare_and_stats_dialogs_build(self):
        from compare_dialog import CompareDialog
        from stats_dialog import StatsDialog
        self.open_sheet(outfit_sheet())
        self.convert()
        m = self.win.model
        dlg = CompareDialog(self.win, m.arr, m.mask, 1.0, self.cfg, "test")
        self.assertEqual(len(dlg._visible_slots()), 5)
        dlg.chk_squad.setChecked(False)
        self.assertEqual(len(dlg._visible_slots()), 4)
        self.assertIsInstance(self.cfg.get("compare.slots"), list)
        StatsDialog(self.win, mc.mask_stats(m.mask, m.arr[..., 3], m.grid))

    def test_convert_dialog_eyedropper(self):
        from convert_dialog import ConvertDialog
        a = outfit_sheet()
        dlg = ConvertDialog(None, a, H1, self.cfg, [(255, 0, 0)] * 4, 1.0)
        dlg.sp_k.setValue(3)
        dlg._start_pick(2)
        dlg._on_pick_pixel(50, 51)  # 단추
        self.assertEqual(dlg.clusters.centers_rgb[2], BUTTON)
        gray, idx = dlg.compute()
        self.assertEqual(int(idx[51, 50]), dlg.mapping[2])


@unittest.skipUnless(os.path.isfile(os.path.join(OUTFIT_DIR, "H1WK_U001F.png")), "Unity 속옷 시트 없음")
class RealDataTests(_QtCase):
    """완료 기준 (기획서 §9 2단계): 기본 속옷 시트로 만든 마스크가 값 집합 {0,64,128,192,255}·L 모드로 저장."""

    def test_underwear_sheet_to_mask(self):
        src = os.path.join(self.dir, "H1WK_U001F.png")
        shutil.copy(os.path.join(OUTFIT_DIR, "H1WK_U001F.png"), src)  # _M 은 복사하지 않음 (새로 만든다)
        self.assertTrue(self.win.open_file(src))
        self.assertFalse(self.win.model.has_mask)

        def setup(d):  # 완성본(중앙값 0.54) → 사용자가 정규화를 끄고 원본 보존 (CHR-ART.5 §3.1, Gain 1.33)
            d.chk_norm.setChecked(False)
            d.rb_hsplit.setChecked(True)

        with mock.patch("convert_dialog.ConvertDialog.exec", lambda d: (setup(d), d._accept(), 1)[2]):
            self.win.convert_to_gray_mask()
        m = self.win.model
        orig = np.array(Image.open(src).convert("RGBA"))
        self.assertTrue(np.array_equal(m.arr[..., 3], orig[..., 3]))
        m.save_path = os.path.join(self.dir, "out", "H1WK_U001F.png")
        with mock.patch("main_window.QMessageBox.warning", return_value=mock.ANY):
            self.assertTrue(self.win.save())
        with Image.open(os.path.join(self.dir, "out", "H1WK_U001F_M.png")) as im:
            self.assertEqual((im.mode, im.size), ("L", (300, 540)))
            vals = set(np.unique(np.array(im)).tolist())
        self.assertTrue(vals <= VALUES)
        self.assertEqual(vals, {0, 64, 128})

    def test_existing_unity_mask_loads(self):
        for n in ("H1WK_U001F.png", "H1WK_U001F_M.png"):
            shutil.copy(os.path.join(OUTFIT_DIR, n), os.path.join(self.dir, n))
        self.assertTrue(self.win.open_file(os.path.join(self.dir, "H1WK_U001F.png")))
        m = self.win.model
        self.assertTrue(m.has_mask)
        self.assertEqual(m.mask_snapped, 0)
        self.assertTrue(set(np.unique(m.mask).tolist()) <= {0, 1, 2, 3, 4})

    @unittest.skipUnless(os.path.isfile(os.path.join(OUTFIT_DIR, "H1WK_TC201.png")), "컬러 방어구 없음")
    def test_color_armor_kmeans(self):
        a = np.array(Image.open(os.path.join(OUTFIT_DIR, "H1WK_TC201.png")).convert("RGBA"))
        cl = mc.kmeans(a, 3)
        self.assertEqual(cl.k, 3)
        idx = mc.despeckle(mc.labels_to_index(cl.labels, [1, 2, 3]), a[..., 3] > 0)
        self.assertTrue(set(np.unique(mc.index_to_png(idx)).tolist()) <= VALUES)
        self.assertEqual(int((idx[a[..., 3] == 0] != 0).sum()), 0)


if __name__ == "__main__":
    unittest.main()
