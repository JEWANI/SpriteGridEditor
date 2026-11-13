"""코어 로직 단위 테스트 (cell_ops, history, export_utils, sheet_model, autosave, config).

실행: 프로젝트 폴더에서  python -m unittest discover tests -v
"""

import os
import sys
import tempfile
import unittest

import numpy as np
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cell_ops  # noqa: E402
import export_utils  # noqa: E402
import reference_layers  # noqa: E402
import sheet_model as sm  # noqa: E402
from autosave import AutoSaver  # noqa: E402
from config_manager import ConfigManager  # noqa: E402
from history import History, make_edit  # noqa: E402

H1 = sm.GridSpec(3, 4, 100, 135)  # 열3 × 행4, 셀 100×135 → 300×540


def random_sheet(grid, seed=1):
    rng = np.random.default_rng(seed)
    return rng.integers(0, 256, size=(grid.sheet_h, grid.sheet_w, 4), dtype=np.uint8)


class MirrorTests(unittest.TestCase):
    def test_r4c1_to_r4c3_mirror_matches_numpy_flip(self):
        """완료 기준: R4C1 → R4C3 미러 결과가 NumPy 좌우 반전과 픽셀 단위로 일치."""
        arr = random_sheet(H1)
        src = copy_src = cell_ops.copy_cells(arr, H1, sm.CellRect.single(3, 0))
        expected = np.fliplr(copy_src).copy()
        block = cell_ops.mirror_block(src, H1)
        edit = cell_ops.make_paste_edit(arr, H1, (3, 2), block, "x")
        History().do(arr, edit)
        y0, y1, x0, x1 = cell_ops.rect_pixels(H1, sm.CellRect.single(3, 2))
        self.assertTrue(np.array_equal(arr[y0:y1, x0:x1], expected))

    def test_multi_cell_mirror_flips_each_cell_keeping_order(self):
        arr = random_sheet(H1, 2)
        row = cell_ops.copy_cells(arr, H1, sm.CellRect(0, 0, 0, 2))  # 한 행 3셀
        mirrored = cell_ops.mirror_block(row, H1)
        for c in range(3):
            cell = row[:, c * 100:(c + 1) * 100]
            self.assertTrue(np.array_equal(mirrored[:, c * 100:(c + 1) * 100], np.fliplr(cell)))

    def test_mirror_twice_is_identity(self):
        arr = random_sheet(H1, 3)
        block = cell_ops.copy_cells(arr, H1, sm.CellRect(1, 0, 2, 2))
        self.assertTrue(np.array_equal(cell_ops.mirror_block(cell_ops.mirror_block(block, H1), H1), block))

    def test_flip_vertical(self):
        arr = random_sheet(H1, 4)
        block = cell_ops.copy_cells(arr, H1, sm.CellRect.single(0, 1))
        self.assertTrue(np.array_equal(cell_ops.flip_block_vertical(block, H1), np.flipud(block)))

    def test_rect_fits(self):
        self.assertTrue(cell_ops.rect_fits(H1, 3, 2, (1, 1)))
        self.assertFalse(cell_ops.rect_fits(H1, 3, 2, (1, 2)))
        self.assertFalse(cell_ops.rect_fits(H1, 3, 2, (2, 1)))
        self.assertFalse(cell_ops.rect_fits(H1, -1, 0, (1, 1)))


class HistoryTests(unittest.TestCase):
    def test_undo_redo_and_dirty(self):
        arr = random_sheet(H1, 5)
        original = arr.copy()
        h = History()
        self.assertTrue(h.is_clean())
        h.do(arr, cell_ops.make_clear_edit(arr, H1, sm.CellRect.single(0, 0), "clear"))
        self.assertFalse(h.is_clean())
        self.assertEqual(int(arr[:135, :100].sum()), 0)
        h.undo(arr)
        self.assertTrue(np.array_equal(arr, original))
        self.assertTrue(h.is_clean())
        h.redo(arr)
        self.assertEqual(int(arr[:135, :100].sum()), 0)

    def test_saved_point_tracking(self):
        arr = random_sheet(H1, 6)
        h = History()
        h.do(arr, cell_ops.make_clear_edit(arr, H1, sm.CellRect.single(0, 0), "a"))
        h.mark_saved()
        self.assertTrue(h.is_clean())
        h.do(arr, cell_ops.make_clear_edit(arr, H1, sm.CellRect.single(1, 0), "b"))
        self.assertFalse(h.is_clean())
        h.undo(arr)
        self.assertTrue(h.is_clean())
        # 저장 시점 이전으로 가서 새 편집 → 저장 시점으로 돌아올 수 없다
        h.undo(arr)
        h.do(arr, cell_ops.make_clear_edit(arr, H1, sm.CellRect.single(2, 0), "c"))
        h.undo(arr)
        self.assertFalse(h.is_clean())

    def test_keeps_at_least_50_steps(self):
        arr = random_sheet(H1, 7)
        h = History()
        for i in range(120):
            h.do(arr, make_edit(arr, 0, 0, np.full((1, 1, 4), i % 256, np.uint8), "p"))
        undone = 0
        while h.undo(arr):
            undone += 1
        self.assertGreaterEqual(undone, 50)

    def test_revision_changes(self):
        arr = random_sheet(H1, 8)
        h = History()
        r0 = h.revision
        h.do(arr, cell_ops.make_clear_edit(arr, H1, sm.CellRect.single(0, 0), "a"))
        self.assertNotEqual(h.revision, r0)


class ExportTests(unittest.TestCase):
    def test_threshold_removes_translucent_alpha(self):
        arr = random_sheet(H1, 9)
        out = export_utils.prepare_export(arr, True, 128)
        self.assertTrue(set(np.unique(out[..., 3])).issubset({0, 255}))
        # 임계값 경계: 127 → 0, 128 → 255
        t = np.zeros((1, 2, 4), np.uint8)
        t[0, 0, 3], t[0, 1, 3] = 127, 128
        t[..., :3] = 200
        o = export_utils.apply_alpha_threshold(t, 128)
        self.assertEqual((int(o[0, 0, 3]), int(o[0, 1, 3])), (0, 255))

    def test_transparent_rgb_is_black(self):
        arr = random_sheet(H1, 10)
        arr[..., 3][::2] = 0
        out = export_utils.prepare_export(arr, True, 128)
        self.assertEqual(int(out[out[..., 3] == 0][:, :3].sum()), 0)

    def test_threshold_off_keeps_alpha(self):
        arr = random_sheet(H1, 11)
        out = export_utils.prepare_export(arr, False, 128)
        self.assertTrue(np.array_equal(out[..., 3], arr[..., 3]))

    def test_extend_edge_colors(self):
        arr = np.zeros((5, 5, 4), np.uint8)
        arr[2, 2] = (10, 20, 30, 255)
        out = export_utils.normalize_transparent_rgb(arr, "extend")
        self.assertEqual(tuple(out[2, 3, :3]), (10, 20, 30))  # 이웃은 색 확장
        self.assertEqual(int(out[2, 3, 3]), 0)  # 알파는 그대로 0
        self.assertEqual(tuple(arr[2, 3, :3]), (0, 0, 0))  # 원본 불변

    def test_save_roundtrip_backup_and_no_metadata(self):
        arr = export_utils.prepare_export(random_sheet(H1, 12), True, 128)
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "H1WK_P001F.png")
            self.assertIsNone(export_utils.save_png_rgba(arr, p))
            self.assertTrue(np.array_equal(export_utils.load_png_rgba(p), arr))
            with Image.open(p) as im:
                self.assertEqual(im.mode, "RGBA")
                for bad in ("gamma", "icc_profile", "srgb"):
                    self.assertNotIn(bad, im.info)
            arr2 = arr.copy()
            arr2[0, 0] = (1, 2, 3, 255)
            backup = export_utils.save_png_rgba(arr2, p)
            self.assertEqual(os.path.basename(backup), "H1WK_P001F_backup.png")
            self.assertTrue(np.array_equal(export_utils.load_png_rgba(backup), arr))
            self.assertTrue(np.array_equal(export_utils.load_png_rgba(p), arr2))
            self.assertEqual(sorted(os.listdir(d)), ["H1WK_P001F.png", "H1WK_P001F_backup.png"])

    def test_name_helper_and_validation(self):
        self.assertEqual(export_utils.build_name("H1WK", "P", 1, "F"), "H1WK_P001F")
        self.assertEqual(export_utils.build_name("H1WK", "U", 12, "C"), "H1WK_U012")
        self.assertEqual(export_utils.validate_name("H1WK_P001F", "F"), [])
        self.assertEqual(export_utils.validate_name("H1WK_P001", "C"), [])
        self.assertEqual(export_utils.validate_name("whatever", "F")[0].key, "validate.name_pattern")
        self.assertEqual(export_utils.validate_name("H1WK_P001M", "F")[0].key, "validate.gender_suffix")
        self.assertEqual(export_utils.validate_name("H1WK_P001", "F")[0].key, "validate.gender_suffix")
        self.assertEqual(export_utils.validate_name("H1WK_P001F", "C")[0].key, "validate.common_has_suffix")

    def test_grid_mismatch_blocks(self):
        arr = random_sheet(H1, 13)
        self.assertEqual(export_utils.validate_sheet(arr, H1), [])
        issues = export_utils.validate_sheet(arr, sm.GridSpec(3, 5, 100, 135))
        self.assertEqual(issues[0].level, "error")

    def test_resolve_save_dir(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(export_utils.resolve_save_dir(d), (d, None))
            folder, note = export_utils.resolve_save_dir(os.path.join(d, "nope"))
            self.assertEqual(note, "save_dir_missing")
            self.assertTrue(folder.endswith("output"))


class SheetModelTests(unittest.TestCase):
    def test_spec_calculation(self):
        self.assertEqual(sm.spec_from_sheet_size(300, 540, 3, 4), H1)
        self.assertIsNone(sm.spec_from_sheet_size(301, 540, 3, 4))
        spec = sm.spec_from_cell_size(256, 256, 6, 5)
        self.assertEqual((spec.sheet_w, spec.sheet_h), (1536, 1280))

    def test_builtin_presets_and_detect(self):
        presets = sm.all_presets([])
        self.assertEqual([p["id"] for p in presets], ["H1WK", "H2"])
        self.assertEqual(sm.pick_preset_for_file(presets, 300, 540, "x.png")["id"], "H1WK")
        self.assertEqual(sm.pick_preset_for_file(presets, 1536, 1280, "x.png")["id"], "H2")
        self.assertIsNone(sm.pick_preset_for_file(presets, 123, 45, "x.png"))

    def test_detect_gender(self):
        self.assertEqual(sm.detect_gender("H1WK_P001F"), "F")
        self.assertEqual(sm.detect_gender("H1WK_P001M"), "M")
        self.assertEqual(sm.detect_gender("H1WK_P001F_M"), "F")  # 마스크 접미사 _M은 체형이 아님
        self.assertEqual(sm.detect_gender("BodyM_T2_H1WK"), "M")
        self.assertIsNone(sm.detect_gender("H1WK_P001"))

    def test_labels(self):
        self.assertEqual(sm.cell_label(3, 0), "R4C1")
        self.assertEqual(sm.rect_label(sm.CellRect(3, 0, 3, 2)), "[R4,C1~R4,C3]")

    def test_model_dirty(self):
        arr = random_sheet(H1, 14)
        m = sm.SheetModel(arr, H1, "H1WK", "a")
        self.assertFalse(m.dirty)
        m.history.do(m.arr, cell_ops.make_clear_edit(m.arr, H1, sm.CellRect.single(0, 0), "c"))
        self.assertTrue(m.dirty)
        m.mark_saved()
        self.assertFalse(m.dirty)


class ReferenceTests(unittest.TestCase):
    def test_body_filename(self):
        self.assertEqual(reference_layers.body_filename("F", "T1", "H1WK"), "BodyF_H1WK.png")
        self.assertEqual(reference_layers.body_filename("M", "T3", "H1WK"), "BodyM_T3_H1WK.png")
        self.assertEqual(reference_layers.body_filename("F", "T5", "H2"), "BodyF_T5_H2.png")
        self.assertEqual(reference_layers.body_filename("C", "T1", "H2"), "BodyF_H2.png")

    def test_find_body(self):
        with tempfile.TemporaryDirectory() as d:
            open(os.path.join(d, "BodyF_H2.png"), "wb").close()
            path, name = reference_layers.find_body(d, "F", "T1", "H2")
            self.assertEqual(os.path.basename(path), "BodyF_H2.png")
            path, name = reference_layers.find_body(d, "M", "T1", "H2")  # BodyM_H2 없음
            self.assertIsNone(path)
            self.assertEqual(name, "BodyM_H2.png")
            self.assertEqual(reference_layers.find_body(d, "F", "T1", ""), (None, ""))


class AutoSaveTests(unittest.TestCase):
    def test_snapshot_keep_and_crash_recovery(self):
        with tempfile.TemporaryDirectory() as d:
            a = AutoSaver(d, pid=999991)
            a.start_session()
            arr = random_sheet(H1, 15)
            for i in range(4):
                a.save(arr, {"name": f"n{i}"}, keep=2)
            self.assertEqual(len([s for s in a.list_snapshots() if s.pid == 999991]), 2)
            # 비정상 종료(= end_session 호출 안 됨) → 다른 세션이 시작되면 복구 후보로 보인다
            b = AutoSaver(d, pid=999992)
            import autosave as mod
            orig = mod._pid_alive
            mod._pid_alive = lambda pid: pid == 999992
            try:
                cands = b.start_session()
            finally:
                mod._pid_alive = orig
            self.assertEqual(len(cands), 2)
            loaded = b.load(cands[0])
            self.assertTrue(np.array_equal(loaded, arr))
            # 정상 종료하면 스냅샷과 lock이 지워진다
            a.end_session()
            self.assertEqual([s for s in a.list_snapshots() if s.pid == 999991], [])
            self.assertFalse(os.path.exists(a.lock_path))


class ConfigTests(unittest.TestCase):
    def test_defaults_merge_and_dotted_access(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "config.json")
            with open(p, "w", encoding="utf-8") as f:
                f.write('{"language": "en", "autosave": {"interval_min": 3}}')
            c = ConfigManager(p)
            self.assertEqual(c.get("language"), "en")
            self.assertEqual(c.get("autosave.interval_min"), 3)
            self.assertEqual(c.get("autosave.keep"), 2)  # 누락 키 보강
            self.assertTrue(c.get("autosave.enabled"))
            c.set("autosave.keep", 5)
            self.assertEqual(ConfigManager(p).get("autosave.keep"), 5)

    def test_recent_list_limit_and_dedupe(self):
        with tempfile.TemporaryDirectory() as d:
            c = ConfigManager(os.path.join(d, "config.json"))
            for i in range(15):
                c.push_recent("recent_files", f"a{i}.png")
            c.push_recent("recent_files", "a3.png")
            items = c.get("recent_files")
            self.assertEqual(len(items), 10)
            self.assertEqual(items[0], "a3.png")
            self.assertEqual(items.count("a3.png"), 1)

    def test_corrupt_json_falls_back(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "config.json")
            with open(p, "w", encoding="utf-8") as f:
                f.write("{broken")
            self.assertEqual(ConfigManager(p).get("language"), "ko")


if __name__ == "__main__":
    unittest.main()
