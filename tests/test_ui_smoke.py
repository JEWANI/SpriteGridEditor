"""UI 스모크 테스트 (오프스크린 Qt). 실제 마우스 클릭/드래그 → 셀 편집 → 저장 흐름을 검증한다.

실행: 프로젝트 폴더에서  python -m unittest discover tests -v
(PySide6가 없거나 디스플레이가 없는 환경에서는 QT_QPA_PLATFORM=offscreen 으로 실행된다)
"""

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
from PySide6.QtWidgets import QApplication  # noqa: E402

import export_utils  # noqa: E402
import i18n  # noqa: E402
from autosave import AutoSaver  # noqa: E402
from config_manager import ConfigManager  # noqa: E402
from app_info import APP_VERSION  # noqa: E402
from main_window import MainWindow  # noqa: E402
from sheet_model import CellRect  # noqa: E402

_app = QApplication.instance() or QApplication([])


def make_sheet(path, w=300, h=540, seed=3):
    rng = np.random.default_rng(seed)
    arr = rng.integers(0, 256, size=(h, w, 4), dtype=np.uint8)  # 반투명 알파 포함
    Image.fromarray(arr, "RGBA").save(path)
    return arr


class UiSmokeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name
        self.body_dir = os.path.join(self.dir, "Body")
        os.makedirs(self.body_dir)
        make_sheet(os.path.join(self.body_dir, "BodyF_H1WK.png"), seed=9)
        self.cfg = ConfigManager(os.path.join(self.dir, "config.json"))
        self.cfg.set("body_dir", self.body_dir)
        self.cfg.set("save_dir", os.path.join(self.dir, "out"))
        self.sheet_path = os.path.join(self.dir, "H1WK_P001F.png")
        self.orig = make_sheet(self.sheet_path)
        i18n.set_language("ko")
        self.win = MainWindow(self.cfg, AutoSaver(os.path.join(self.dir, "autosave")))
        self.win.resize(1400, 860)
        self.win.show()
        _app.processEvents()

    def tearDown(self):
        if self.win.model is not None:
            self.win.model.mark_saved()
            self.win.model.forced_dirty = False
        self.win.close()
        self.tmp.cleanup()
        i18n.set_language("ko")

    # -- 헬퍼 -----------------------------------------------------------------
    def cell_pos(self, r, c):
        """셀 (r,c) 한가운데의 캔버스 뷰포트 좌표."""
        cv = self.win.canvas
        g = self.win.model.grid
        s = cv._scale()
        ox, oy = cv._origin()
        return QPoint(ox + int((c + 0.5) * g.cell_w * s), oy + int((r + 0.5) * g.cell_h * s))

    def click(self, r, c, mod=Qt.NoModifier):
        QTest.mouseClick(self.win.canvas.viewport(), Qt.LeftButton, mod, self.cell_pos(r, c))

    def cell(self, arr, r, c):
        g = self.win.model.grid
        return arr[r * g.cell_h:(r + 1) * g.cell_h, c * g.cell_w:(c + 1) * g.cell_w]

    # -- 테스트 ---------------------------------------------------------------
    def test_open_autodetects_preset_gender_and_body(self):
        self.assertTrue(self.win.open_file(self.sheet_path))
        m = self.win.model
        self.assertEqual(m.preset_id, "H1WK")
        self.assertEqual((m.grid.cols, m.grid.rows, m.grid.cell_w, m.grid.cell_h), (3, 4, 100, 135))
        self.assertEqual(m.gender, "F")
        self.assertEqual(self.win.lb_body_name.text(), "BodyF_H1WK.png")  # Base Body 자동 매칭
        self.assertEqual(self.win.windowTitle(), f"SpriteGrid Editor v{APP_VERSION} - H1WK_P001F.png")
        self.assertEqual(self.win.canvas.source(), CellRect.single(0, 0))

    def test_mouse_selection_source_target_and_range(self):
        self.win.open_file(self.sheet_path)
        cv = self.win.canvas
        self.click(3, 0)
        self.assertEqual(cv.source(), CellRect.single(3, 0))
        self.click(3, 2, Qt.ControlModifier)
        self.assertEqual(cv.target(), CellRect.single(3, 2))
        self.assertEqual(cv.source(), CellRect.single(3, 0))  # Source는 유지
        self.click(1, 1, Qt.ShiftModifier)
        self.assertEqual(cv.target(), CellRect.single(1, 1))
        # 드래그로 다중 셀 선택 (한 행 전체)
        vp = cv.viewport()
        QTest.mousePress(vp, Qt.LeftButton, Qt.NoModifier, self.cell_pos(0, 0))
        QTest.mouseMove(vp, self.cell_pos(0, 2))
        QTest.mouseRelease(vp, Qt.LeftButton, Qt.NoModifier, self.cell_pos(0, 2))
        self.assertEqual(cv.source(), CellRect(0, 0, 0, 2))
        self.assertIn("R1,C1~R1,C3", self.win.lb_sel.text())

    def test_r4c1_to_r4c3_mirror_apply_and_undo_redo(self):
        """완료 기준: 미러 결과가 NumPy 좌우 반전과 픽셀 단위로 일치."""
        self.win.open_file(self.sheet_path)
        m = self.win.model
        self.click(3, 0)
        self.click(3, 2, Qt.ControlModifier)
        self.win.mirror_apply()
        self.assertTrue(np.array_equal(self.cell(m.arr, 3, 2), np.fliplr(self.cell(self.orig, 3, 0))))
        self.assertTrue(np.array_equal(self.cell(m.arr, 3, 0), self.cell(self.orig, 3, 0)))  # Source 불변
        self.assertTrue(m.dirty)
        self.assertTrue(self.win.windowTitle().endswith("*"))
        self.win.undo()
        self.assertTrue(np.array_equal(m.arr, self.orig))
        self.assertFalse(m.dirty)
        self.win.redo()
        self.assertTrue(np.array_equal(self.cell(m.arr, 3, 2), np.fliplr(self.cell(self.orig, 3, 0))))

    def test_copy_paste_mirror_row_and_clear_cut(self):
        self.win.open_file(self.sheet_path)
        m = self.win.model
        cv = self.win.canvas
        cv.set_source(CellRect(0, 0, 0, 2))  # 한 행 3셀
        self.win.copy_cells()
        cv.set_target(CellRect.single(1, 0))
        self.win.paste_cells(mirror=True)
        for c in range(3):  # 셀 순서 유지 + 셀마다 반전
            self.assertTrue(np.array_equal(self.cell(m.arr, 1, c), np.fliplr(self.cell(self.orig, 0, c))))
        # 일반 붙여넣기
        cv.set_target(CellRect.single(2, 0))
        self.win.paste_cells()
        for c in range(3):
            self.assertTrue(np.array_equal(self.cell(m.arr, 2, c), self.cell(self.orig, 0, c)))
        # 범위를 벗어나는 붙여넣기는 막힌다
        before = m.arr.copy()
        cv.set_target(CellRect.single(3, 2))
        self.win.paste_cells()
        self.assertTrue(np.array_equal(m.arr, before))
        # 잘라내기 / 지우기
        cv.set_source(CellRect.single(0, 1))
        self.win.cut_cells()
        self.assertEqual(int(self.cell(m.arr, 0, 1).sum()), 0)
        cv.set_source(CellRect.single(3, 0))
        self.win.clear_cells()
        self.assertEqual(int(self.cell(m.arr, 3, 0).sum()), 0)
        # 전부 되돌리면 원본
        while m.history.can_undo:
            self.win.undo()
        self.assertTrue(np.array_equal(m.arr, self.orig))

    def test_target_range_shape_must_match(self):
        self.win.open_file(self.sheet_path)
        m = self.win.model
        cv = self.win.canvas
        cv.set_source(CellRect(0, 0, 0, 1))  # 1행×2열
        self.win.copy_cells()
        cv.set_target(CellRect(1, 0, 2, 1))  # 2행×2열 → 모양 다름
        before = m.arr.copy()
        self.win.paste_cells()
        self.assertTrue(np.array_equal(m.arr, before))
        cv.set_target(CellRect(1, 0, 1, 1))  # 1행×2열 → OK
        self.win.paste_cells()
        self.assertTrue(np.array_equal(self.cell(m.arr, 1, 1), self.cell(self.orig, 0, 1)))

    def test_save_applies_alpha_threshold_and_clean_state(self):
        self.win.open_file(self.sheet_path)
        m = self.win.model
        self.win.canvas.set_source(CellRect.single(0, 0))
        self.win.clear_cells()
        out_path = os.path.join(self.dir, "out", "H1WK_P002F.png")
        m.save_path = out_path  # 대화상자 없이 조용히 저장하는 경로를 검증
        self.assertTrue(self.win.save())
        saved = export_utils.load_png_rgba(out_path)
        self.assertTrue(set(np.unique(saved[..., 3])).issubset({0, 255}))  # 반투명 알파 없음
        self.assertEqual(int(saved[saved[..., 3] == 0][:, :3].sum()), 0)  # 투명 RGB (0,0,0)
        self.assertEqual(int(saved[:135, :100].sum()), 0)  # 지운 셀
        self.assertFalse(m.dirty)
        self.assertEqual(self.win.windowTitle(), f"SpriteGrid Editor v{APP_VERSION} - H1WK_P002F.png")
        # 재저장 → 백업 생성
        self.win.canvas.set_source(CellRect.single(0, 1))
        self.win.clear_cells()
        self.assertTrue(self.win.save())
        self.assertTrue(os.path.exists(os.path.join(self.dir, "out", "H1WK_P002F_backup.png")))

    def test_grid_mismatch_blocks_save(self):
        self.win.open_file(self.sheet_path)
        m = self.win.model
        spec = type(m.grid)(6, 5, 256, 256)  # H2 규격을 300×540 시트에 적용
        self.win._apply_grid(spec, "H2")
        self.assertFalse(m.grid_matches_image())
        m.save_path = os.path.join(self.dir, "out", "x.png")
        import PySide6.QtWidgets as W
        W.QMessageBox.critical = staticmethod(lambda *a, **k: None)  # 오류 팝업 막기
        self.assertFalse(self.win.save())
        self.assertFalse(os.path.exists(m.save_path))

    def test_zoom_nearest_and_background_modes(self):
        self.win.open_file(self.sheet_path)
        cv = self.win.canvas
        for z in (100, 200, 800):
            cv.set_zoom(z)
            self.assertEqual(cv.zoom(), z)
        cv.zoom_step(-1)
        self.assertEqual(cv.zoom(), 700)
        cv.set_zoom(300)
        for mode in ("checker", "white", "black", "dots"):
            self.win._set_background(mode)
            _app.processEvents()
            img = cv.viewport().grab().toImage()
            self.assertFalse(img.isNull())
        self.win._cycle_background()  # dots → checker
        self.assertEqual(cv.background(), "checker")

    def test_canvas_render_is_pixel_exact(self):
        """800% 줌 렌더 결과가 NumPy 최근접 확대와 일치 (보간 없음)."""
        self.win.open_file(self.sheet_path)
        cv = self.win.canvas
        self.win._set_background("black")
        self.win.a_grid.setChecked(False)
        cv.set_source(None)
        cv.set_zoom(800)
        cv.horizontalScrollBar().setValue(0)  # 줌은 화면 중앙 기준이라 왼쪽 위로 스크롤해 둔다
        cv.verticalScrollBar().setValue(0)
        _app.processEvents()
        m = self.win.model
        m.arr[...] = 0
        m.arr[10:14, 20:24] = (255, 128, 0, 255)  # 4×4 불투명 주황 블록
        cv.refresh()
        _app.processEvents()
        img = cv.viewport().grab().toImage()
        ox, oy = cv._origin()
        px = img.pixelColor(ox + 22 * 8 + 3, oy + 11 * 8 + 3)  # 블록 내부
        self.assertEqual((px.red(), px.green(), px.blue()), (255, 128, 0))
        edge = img.pixelColor(ox + 20 * 8 - 1, oy + 11 * 8)  # 블록 바로 왼쪽 = 검은 배경 (번짐 없음)
        self.assertEqual((edge.red(), edge.green(), edge.blue()), (0, 0, 0))

    def test_language_switch_retranslates(self):
        self.win.open_file(self.sheet_path)
        self.assertEqual(self.win.a_copy.text(), "복사")
        i18n.set_language("en")
        self.win.retranslate_ui()
        self.assertEqual(self.win.a_copy.text(), "Copy")
        self.assertEqual(self.win.lb_preset.text(), "Sheet spec:")
        self.assertIn("Cols 3 × Rows 4", self.win.lb_grid.text())
        i18n.set_language("ko")
        self.win.retranslate_ui()
        self.assertEqual(self.win.a_copy.text(), "복사")

    def test_autosave_and_recovery_roundtrip(self):
        self.win.open_file(self.sheet_path)
        m = self.win.model
        self.win.canvas.set_source(CellRect.single(0, 0))
        self.win.clear_cells()
        self.win._autosave_tick()
        snaps = self.win.autosaver.list_snapshots()
        self.assertEqual(len(snaps), 1)
        # 복구: 같은 내용으로 새 문서가 만들어지고 '수정됨' 상태다
        edited = m.arr.copy()
        self.win._recover(snaps[0])
        self.assertTrue(np.array_equal(self.win.model.arr, edited))
        self.assertTrue(self.win.model.dirty)
        self.assertEqual(self.win.model.name, "H1WK_P001F")
        self.assertEqual(self.win.model.gender, "F")
        # 저장하면 자동 저장본이 지워진다
        self.win.model.save_path = os.path.join(self.dir, "out", "r.png")
        self.win.save()
        self.assertEqual(self.win.autosaver.list_snapshots(), [])

    def test_unmatched_size_opens_with_fallback_grid(self):
        odd = os.path.join(self.dir, "odd.png")
        make_sheet(odd, 123, 77)
        import preset_dialog

        # 규격 대화상자를 띄우지 않고 '취소'한 것처럼 처리
        preset_dialog.SheetSpecDialog.exec = lambda self_: 0
        import main_window as mw
        mw.SheetSpecDialog.exec = lambda self_: 0
        from unittest import mock

        # 규격과 다른 크기 → 가져오기 팝업이 뜬다. '원본 그대로 열기'를 고른 것처럼 처리
        def open_raw(dlg):
            dlg.open_raw = True
            return 1

        with mock.patch("import_dialog.ImportDialog.exec", open_raw):
            self.assertTrue(self.win.open_file(odd))
        g = self.win.model.grid
        self.assertEqual((g.cols, g.rows, g.cell_w, g.cell_h), (1, 1, 123, 77))

    # -- 레이어 구분 / 한도 / 테마 ------------------------------------------------
    def test_layer_bar_and_list_distinguish_edit_and_reference(self):
        self.assertIn("열린 시트가 없습니다", self.win.lb_layerbar.text())
        self.win.open_file(self.sheet_path)
        bar = self.win.lb_layerbar.text()
        self.assertIn("편집 레이어", bar)
        self.assertIn("H1WK_P001F.png", bar)
        self.assertIn("표시 중인 것 없음", bar)  # Body 기본은 숨김
        first = self.win.list_layers.item(0)
        self.assertTrue(first.text().startswith("[편집"))
        self.win._toggle_body()  # Shift+B
        self.assertIn("참조 레이어 (읽기 전용", self.win.lb_layerbar.text())
        self.assertIn("BodyF_H1WK.png", self.win.lb_layerbar.text())
        texts = [self.win.list_layers.item(i).text() for i in range(self.win.list_layers.count())]
        self.assertEqual(sum(t.startswith("[편집") for t in texts), 1)
        self.assertTrue(any(t.startswith("[참조") for t in texts))

    def test_reference_layer_limit_is_six_including_body(self):
        from layer_model import MAX_REFERENCE_LAYERS
        self.assertEqual(MAX_REFERENCE_LAYERS, 6)
        self.win.open_file(self.sheet_path)
        extras = []
        for i in range(7):
            p = os.path.join(self.dir, f"ref{i}.png")
            make_sheet(p, seed=20 + i)
            extras.append(p)
        results = [self.win.add_reference_layer(p) for p in extras]
        self.assertEqual(results, [True] * 5 + [False] * 2)  # Body 1 + 추가 5 = 6
        self.assertEqual(len(self.win._all_refs()), 6)
        self.assertFalse(self.win.btn_layer_add.isEnabled())
        # 추가 레이어 하나를 목록에서 골라 제거 → 다시 추가 가능
        last_uid = self.win.extra_layers[-1].uid
        self.win._rebuild_layer_list(select=last_uid)
        self.win._remove_selected_layer()
        self.assertEqual(len(self.win.extra_layers), 4)
        self.assertTrue(self.win.btn_layer_add.isEnabled())

    def test_reference_layers_are_not_saved_and_not_edited(self):
        self.win.open_file(self.sheet_path)
        p = os.path.join(self.dir, "overlay.png")
        make_sheet(p, w=300, h=540, seed=77)
        self.assertTrue(self.win.add_reference_layer(p))
        self.win.canvas.set_zoom(200)
        _app.processEvents()
        before = self.win.model.arr.copy()
        self.click(0, 0)
        self.assertTrue(np.array_equal(self.win.model.arr, before))  # 클릭은 편집 레이어 선택일 뿐
        self.win._toggle_edit_visible()
        self.assertFalse(self.win.canvas.edit_visible())
        self.assertIn("숨김", self.win.lb_layerbar.text())
        img = self.win.canvas.viewport().grab().toImage()
        self.assertFalse(img.isNull())
        self.win._toggle_edit_visible()
        self.assertTrue(np.array_equal(self.win.model.arr, before))

    def test_old_background_body_setting_migrates_to_body_layer(self):
        self.win.close()
        cfg = ConfigManager(os.path.join(self.dir, "config2.json"))
        cfg.set("background_mode", "body")
        cfg.set("body_dir", self.body_dir)
        win = MainWindow(cfg, AutoSaver(os.path.join(self.dir, "autosave2")))
        try:
            self.assertEqual(cfg.get("background_mode"), "checker")
            self.assertTrue(cfg.get("body_visible"))
            self.assertTrue(win.body_layer.visible)
        finally:
            win.close()
            self.win = MainWindow(self.cfg, AutoSaver(os.path.join(self.dir, "autosave3")))

    def test_all_themes_apply_and_render(self):
        from theme import THEMES, canvas_colors
        self.assertGreaterEqual(len(THEMES), 6)
        self.win.open_file(self.sheet_path)
        self.win.body_layer.visible = True
        self.win._push_layers()
        for name in THEMES:
            self.win._apply_theme(name)
            _app.processEvents()
            c = canvas_colors(name)
            for k in ("workspace", "edit_outline", "ref_tint", "checker_a"):
                self.assertIn(k, c)
            self.assertFalse(self.win.canvas.viewport().grab().toImage().isNull())
        self.win._apply_theme("dark")

    def test_edit_outline_is_drawn_around_sheet(self):
        self.win.open_file(self.sheet_path)
        cv = self.win.canvas
        cv.set_zoom(200)
        cv.set_theme("orange")
        _app.processEvents()
        ox, oy = cv._origin()
        img = cv.viewport().grab().toImage()
        px = img.pixelColor(ox - 2, oy + 40)  # 시트 바로 바깥 (윤곽선 3px)
        self.assertEqual(px.name(), "#ff8c1a")

    def test_screenshot_for_eyeballing(self):
        """레이아웃 육안 확인용 스크린샷 (실패 조건 없음)."""
        out = os.environ.get("SGE_SHOT")
        self.win.open_file(self.sheet_path)
        self.win.body_layer.visible = True
        self.win.canvas.set_zoom(200)
        self.click(3, 0)
        self.click(3, 2, Qt.ControlModifier)
        self.win.mirror_apply()
        _app.processEvents()
        if out:
            self.win.grab().save(out)
        self.assertTrue(True)


if __name__ == "__main__":
    unittest.main()
