"""이미지 가져오기: 배경 감지·투명화·비율 계산·시트 맞춤 (import_utils) + 가져오기 팝업 (오프스크린 Qt)."""

import os
import sys
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import import_utils as iu  # noqa: E402


def ai_like(w=1024, h=1536, bg=(255, 255, 255), body=(200, 120, 80)):
    """단색 배경 + 가운데 인물 + 인물 안쪽에 배경과 같은 색(눈 흰자 같은 것)."""
    a = np.zeros((h, w, 4), np.uint8)
    a[..., :3] = bg
    a[..., 3] = 255
    a[h // 5:h * 4 // 5, w // 3:w * 2 // 3, :3] = body
    a[h // 3:h // 3 + 20, w // 2 - 10:w // 2 + 10, :3] = bg
    return a


class BackgroundTests(unittest.TestCase):
    def test_detect_single_color(self):
        info = iu.detect_background(ai_like())
        self.assertEqual(info.kind, iu.BG_SINGLE)
        self.assertEqual(info.color, (255, 255, 255))

    def test_detect_transparent(self):
        a = ai_like()
        a[..., 3] = 0
        self.assertEqual(iu.detect_background(a).kind, iu.BG_TRANSPARENT)

    def test_detect_multi_color(self):
        a = ai_like()
        a[:, : a.shape[1] // 2, :3] = (0, 0, 255)  # 왼쪽 절반 파랑 → 테두리 색이 둘
        self.assertEqual(iu.detect_background(a).kind, iu.BG_MULTI)

    def test_edge_scope_keeps_inner_same_color(self):
        a = ai_like()
        h, w = a.shape[:2]
        out = iu.remove_background(a, [(255, 255, 255)], 16, iu.SCOPE_EDGE)
        self.assertEqual(out[0, 0, 3], 0)  # 바깥 배경 → 투명
        self.assertEqual(out[h // 3 + 5, w // 2, 3], 255)  # 인물 안쪽 흰색 → 유지
        self.assertEqual(out[h // 2, w // 3 + 5, 3], 255)  # 인물 → 유지

    def test_all_scope_removes_inner_same_color(self):
        a = ai_like()
        h, w = a.shape[:2]
        out = iu.remove_background(a, [(255, 255, 255)], 16, iu.SCOPE_ALL)
        self.assertEqual(out[h // 3 + 5, w // 2, 3], 0)

    def test_tolerance(self):
        a = ai_like(bg=(250, 250, 250))
        self.assertEqual(iu.remove_background(a, [(255, 255, 255)], 4)[0, 0, 3], 255)
        self.assertEqual(iu.remove_background(a, [(255, 255, 255)], 5)[0, 0, 3], 0)

    def test_edge_connected_follows_winding_path(self):
        m = np.zeros((7, 7), bool)
        m[0, :] = True
        m[:, 6] = True
        m[6, 1:] = True
        m[2:7, 1] = True
        m[2, 1:5] = True
        m[2:5, 4] = True  # 테두리에서 꺾여 들어가는 길
        self.assertTrue(iu.edge_connected(m)[4, 4])

        ring = np.zeros((7, 7), bool)
        ring[0, :] = ring[-1, :] = ring[:, 0] = ring[:, -1] = True
        ring[3, 3] = True  # 테두리와 떨어진 섬
        res = iu.edge_connected(ring)
        self.assertTrue(res[0, 0])
        self.assertFalse(res[3, 3])


class SizeTests(unittest.TestCase):
    def test_keep_ratio(self):
        self.assertEqual(iu.keep_ratio_height(1024, 1536, 300), 450)
        self.assertEqual(iu.keep_ratio_width(1024, 1536, 540), 360)

    def test_fit_inside(self):
        self.assertEqual(iu.fit_inside(1024, 1536, 300, 540), (300, 450))
        self.assertEqual(iu.fit_inside(2000, 1000, 300, 540), (300, 150))

    def test_place_on_canvas_pad_and_crop(self):
        a = np.full((450, 300, 4), 255, np.uint8)
        out = iu.place_on_canvas(a, 300, 540, iu.ALIGN_BOTTOM)
        self.assertEqual(out.shape, (540, 300, 4))
        self.assertEqual(out[0, 0, 3], 0)
        self.assertEqual(out[-1, 0, 3], 255)
        big = np.full((600, 400, 4), 255, np.uint8)
        self.assertEqual(iu.place_on_canvas(big, 300, 540).shape, (540, 300, 4))

    def test_nearest_keeps_palette(self):
        out = iu.resize(ai_like(64, 96), 32, 48, iu.RESAMPLE_NEAREST)
        colors = {tuple(int(v) for v in c) for c in out.reshape(-1, 4)}
        self.assertTrue(colors <= {(255, 255, 255, 255), (200, 120, 80, 255)})

    def test_process_order(self):
        opt = iu.ImportOptions(bg_colors=[(255, 255, 255)], tolerance=16, width=300, height=450,
                               resample=iu.RESAMPLE_BOX, canvas=(300, 540))
        out = iu.process(ai_like(), opt)
        self.assertEqual(out.shape, (540, 300, 4))
        self.assertEqual(out[0, 0, 3], 0)
        self.assertEqual(out[270, 150, 3], 255)


class _QtCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])


class ImportDialogTests(_QtCase):
    def setUp(self):
        import i18n
        from config_manager import ConfigManager
        from sheet_model import all_presets
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = ConfigManager(os.path.join(self.tmp.name, "config.json"))
        self.presets = all_presets([])
        i18n.set_language("ko")

    def tearDown(self):
        self.tmp.cleanup()

    def make(self, arr):
        from import_dialog import ImportDialog
        return ImportDialog(None, arr, "test.png", self.presets, self.cfg)

    def test_defaults_for_ai_image(self):
        dlg = self.make(ai_like())
        self.assertEqual(dlg.cb_preset.currentData(), "H1WK")  # 비율이 가장 비슷한 규격
        self.assertTrue(dlg.rb_auto.isChecked())
        self.assertTrue(dlg.rb_bg_auto.isChecked())
        self.assertEqual((dlg.sp_w.value(), dlg.sp_h.value()), (300, 450))  # 시트 안에 비율 유지로 맞춤

    def test_width_drives_height_in_auto_mode(self):
        dlg = self.make(ai_like())
        dlg.sp_w.setValue(512)
        self.assertEqual(dlg.sp_h.value(), 768)
        dlg.sp_h.setValue(300)
        self.assertEqual(dlg.sp_w.value(), 200)

    def test_custom_mode_is_independent(self):
        dlg = self.make(ai_like())
        dlg.rb_custom.setChecked(True)
        dlg.sp_w.setValue(300)
        dlg.sp_h.setValue(540)
        self.assertEqual((dlg.sp_w.value(), dlg.sp_h.value()), (300, 540))

    def test_apply_gives_preset_sized_transparent_sheet(self):
        dlg = self.make(ai_like())
        dlg._accept_apply()
        self.assertEqual(dlg.result_arr.shape, (540, 300, 4))
        self.assertEqual(dlg.result_preset["id"], "H1WK")
        self.assertEqual(dlg.result_arr[0, 0, 3], 0)
        self.assertEqual(self.cfg.get("import.last_preset"), "H1WK")

    def test_pick_color_from_preview(self):
        a = ai_like()
        a[:, : a.shape[1] // 2, :3] = (0, 0, 255)
        dlg = self.make(a)
        self.assertTrue(dlg.rb_pick.isChecked())  # 단색 아님 → 지정 색
        dlg._on_pick_pixel(a.shape[1] - 1, 0)
        self.assertEqual(dlg.pick_color, (255, 255, 255))


class OpenFlowTests(_QtCase):
    """메인 창: 규격과 다른 크기의 불투명 이미지를 열면 팝업 → 적용 결과로 H1 시트가 열린다."""

    def _window(self, d):
        from autosave import AutoSaver
        from config_manager import ConfigManager
        from main_window import MainWindow
        cfg = ConfigManager(os.path.join(d, "config.json"))
        cfg.set("body_dir", d)
        return MainWindow(cfg, AutoSaver(os.path.join(d, "autosave")))

    def test_open_ai_image_through_dialog(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "gemini_out.png")
            Image.fromarray(ai_like()).save(path)
            win = self._window(d)

            def fake_exec(dlg):
                dlg._accept_apply()
                return 1

            with mock.patch("import_dialog.ImportDialog.exec", fake_exec):
                self.assertTrue(win.open_file(path))
            self.assertEqual((win.model.width, win.model.height), (300, 540))
            self.assertEqual(win.model.preset_id, "H1WK")
            self.assertTrue(win.model.dirty)
            win.model.mark_saved()
            win.close()
            win.autosaver.end_session() if hasattr(win.autosaver, "end_session") else None

    def test_ready_sheet_skips_dialog(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "H1WK_P001F.png")
            a = np.zeros((540, 300, 4), np.uint8)
            a[100:200, 100:200] = 255
            Image.fromarray(a).save(path)
            win = self._window(d)
            with mock.patch("import_dialog.ImportDialog.exec", side_effect=AssertionError("팝업이 뜨면 안 됨")):
                self.assertTrue(win.open_file(path))
            self.assertFalse(win.model.dirty)
            win.close()


if __name__ == "__main__":
    unittest.main()
