"""4단계(v0.4.0) 나머지: 파일명 검증 강화 / 폴더 일괄 변환 / Unity .meta 복제 / 다음·이전 파일 / 단축키 정리."""

import os
import sys
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import batch_convert as bc  # noqa: E402
import export_utils as eu  # noqa: E402
import unity_meta as um  # noqa: E402
from sheet_model import BUILTIN_PRESETS  # noqa: E402


def _keys(issues):
    return [i.key for i in issues]


def _color_sheet(path, w=300, h=540):
    arr = np.zeros((h, w, 4), np.uint8)
    arr[20:70, 20:80] = (200, 40, 40, 255)  # 빨강 (많음) → 영역 1
    arr[70:90, 20:80] = (40, 40, 200, 255)  # 파랑 (적음) → 영역 2
    Image.fromarray(arr, "RGBA").save(path)
    return arr


class NameValidationTests(unittest.TestCase):
    def test_rule_name_is_clean(self):
        self.assertEqual(eu.validate_name("H1WK_P001F", "F", "H1WK"), [])

    def test_case_legacy_zero_mask_suffix_motion(self):
        self.assertEqual(_keys(eu.validate_name("h1wk_p001f", "F")), ["validate.name_case"])
        self.assertEqual(_keys(eu.validate_name("H1WK_TC201", "C")), ["validate.name_legacy_kind"])
        self.assertEqual(_keys(eu.validate_name("H1WK_C003", "C")), ["validate.name_legacy_kind"])
        self.assertIn("validate.name_number_zero", _keys(eu.validate_name("H1WK_P000F", "F")))
        self.assertIn("validate.name_mask_suffix", _keys(eu.validate_name("H1WK_P001F_M", "F")))
        self.assertEqual(_keys(eu.validate_name("H1WK_P001F", "F", "H2")), ["validate.name_motion"])
        self.assertEqual(_keys(eu.validate_name("hello", "F")), ["validate.name_pattern"])

    def test_folder_checks(self):
        with tempfile.TemporaryDirectory() as d:
            for n in ("H1WK_P001.png", "H1WK_P002F.png", "H1WK_P003F_M.png"):
                open(os.path.join(d, n), "wb").close()
            self.assertIn("validate.name_common_exists", _keys(eu.validate_name("H1WK_P001F", "F", "", d)))
            self.assertIn("validate.name_gendered_exists", _keys(eu.validate_name("H1WK_P002", "C", "", d)))
            self.assertIn("validate.name_stale_mask", _keys(eu.validate_name("H1WK_P003F", "F", "", d, False)))
            self.assertNotIn("validate.name_stale_mask", _keys(eu.validate_name("H1WK_P003F", "F", "", d, True)))


META = """fileFormatVersion: 2
guid: 0123456789abcdef0123456789abcdef
TextureImporter:
  internalIDToNameTable: []
  serializedVersion: 13
  filterMode: 0
  spriteMode: 2
  spriteSheet:
    serializedVersion: 2
    sprites:
    - serializedVersion: 2
      name: H1WK_U001F_0
      rect:
        x: 0
      outline: []
      spriteID: aaaa
      internalID: 111
      vertices: []
    - serializedVersion: 2
      name: H1WK_U001F_1
      outline: []
      spriteID: bbbb
      internalID: 222
      vertices: []
    outline: []
    physicsShape: []
    spriteID: cccc
    internalID: 0
    nameFileIdTable:
      H1WK_U001F_0: 111
      H1WK_U001F_1: 222
  userData:
"""


class UnityMetaTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.proj = self.tmp.name
        os.makedirs(os.path.join(self.proj, "ProjectSettings"))
        self.folder = os.path.join(self.proj, "Assets", "Outfit")
        os.makedirs(self.folder)
        Image.new("RGBA", (300, 540)).save(os.path.join(self.folder, "H1WK_U001F.png"))
        with open(os.path.join(self.folder, "H1WK_U001F.png.meta"), "w", encoding="utf-8", newline="") as f:
            f.write(META)

    def tearDown(self):
        self.tmp.cleanup()

    def test_guid_rule_matches_project(self):
        # 프로젝트의 gen_* 생성본(BodyF_H1WK.png.meta)과 같은 규칙
        self.assertEqual(um.meta_guid("BodyF_H1WK"), __import__("hashlib").md5(b"S_unit_BodyF_H1WK").hexdigest())

    def test_rewrite_replaces_guid_names_and_ids_deterministically(self):
        out = um.rewrite_meta(META, "H1WK_P001F")
        self.assertIn(f"guid: {um.meta_guid('H1WK_P001F')}", out)
        self.assertNotIn("H1WK_U001F", out)
        self.assertIn("name: H1WK_P001F_1", out)
        self.assertNotIn("spriteID: aaaa", out)
        self.assertIn("    spriteID: cccc", out)  # 시트 자체 필드는 그대로
        self.assertIn("    internalID: 0", out)
        _sid, iid = um._sprite_ids(um.meta_guid("H1WK_P001F"), "H1WK_P001F_1")
        self.assertIn(f"internalID: {iid}", out)
        self.assertIn(f"H1WK_P001F_1: {iid}", out)  # 이름표와 스프라이트 ID 일치
        self.assertEqual(out, um.rewrite_meta(META, "H1WK_P001F"))
        self.assertEqual(len(out.splitlines()), len(META.splitlines()))

    def test_copy_meta_only_in_unity_and_only_when_missing(self):
        new_png = os.path.join(self.folder, "H1WK_P001F.png")
        Image.new("RGBA", (300, 540)).save(new_png)
        code, tpl = um.copy_meta_for(new_png, (300, 540))
        self.assertEqual((code, tpl), (um.META_OK, "H1WK_U001F.png.meta"))
        self.assertEqual(um.copy_meta_for(new_png, (300, 540))[0], um.META_EXISTS)
        # 크기가 다르면 템플릿 없음, 마스크는 마스크 템플릿만
        other = os.path.join(self.folder, "H2_P001F.png")
        self.assertEqual(um.copy_meta_for(other, (1536, 1280))[0], um.META_NO_TEMPLATE)
        self.assertEqual(um.copy_meta_for(os.path.join(self.folder, "H1WK_P001F_M.png"), (300, 540))[0],
                         um.META_NO_TEMPLATE)
        # Unity 프로젝트 밖
        out = os.path.join(self.tmp.name, "plain")
        os.makedirs(out)
        self.assertEqual(um.copy_meta_for(os.path.join(out, "a.png"), (300, 540))[0], um.META_NOT_UNITY)


class BatchConvertTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.src = os.path.join(self.tmp.name, "src")
        self.out = os.path.join(self.tmp.name, "out")
        os.makedirs(self.src)
        _color_sheet(os.path.join(self.src, "H1WK_P001F.png"))
        _color_sheet(os.path.join(self.src, "H1WK_P002M.png"))
        Image.new("RGBA", (64, 64)).save(os.path.join(self.src, "icon.png"))  # 규격 없음
        Image.new("L", (300, 540)).save(os.path.join(self.src, "H1WK_P001F_M.png"))  # 마스크 → 목록 제외
        Image.new("RGBA", (300, 540)).save(os.path.join(self.src, "H1WK_P001F_backup.png"))  # 백업 → 제외

    def tearDown(self):
        self.tmp.cleanup()

    def test_list_sources_skips_mask_and_backup(self):
        names = [os.path.basename(p) for p in bc.list_sources(self.src)]
        self.assertEqual(names, ["H1WK_P001F.png", "H1WK_P002M.png", "icon.png"])

    def test_convert_color_sheet_to_gray_and_mask(self):
        opts = bc.BatchOptions(out_dir=self.out, regions=2, meta_copy=False)
        res = bc.convert_file(os.path.join(self.src, "H1WK_P001F.png"), BUILTIN_PRESETS, opts)
        self.assertIn(res.status, (bc.ST_OK, bc.ST_WARN))
        gray = np.array(Image.open(res.out_path).convert("RGBA"))
        self.assertTrue((gray[30, 30, 0] == gray[30, 30, 1] == gray[30, 30, 2]))  # 회색
        with Image.open(res.mask_path) as im:
            self.assertEqual(im.mode, "L")
            mask = np.array(im)
        self.assertEqual(int(mask[30, 30]), 64)  # 많은 색 → 영역 1
        self.assertEqual(int(mask[80, 30]), 128)  # 적은 색 → 영역 2
        self.assertEqual(int(mask[0, 0]), 0)

    def test_skip_and_no_mask_mode(self):
        opts = bc.BatchOptions(out_dir=self.out, mode=bc.MODE_NONE)
        self.assertEqual(bc.convert_file(os.path.join(self.src, "icon.png"), BUILTIN_PRESETS, opts).status, bc.ST_SKIP)
        res = bc.convert_file(os.path.join(self.src, "H1WK_P002M.png"), BUILTIN_PRESETS, opts)
        self.assertEqual(res.mask_path, "")
        self.assertFalse(os.path.exists(os.path.join(self.out, "H1WK_P002M_M.png")))

    def test_same_folder_detection(self):
        self.assertTrue(bc.same_folder(self.src, self.src + os.sep))
        self.assertFalse(bc.same_folder(self.src, self.out))


# --------------------------------------------------------------------------- #
#  UI                                                                          #
# --------------------------------------------------------------------------- #
from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

_app = QApplication.instance() or QApplication([])

import i18n  # noqa: E402
from autosave import AutoSaver  # noqa: E402
from batch_dialog import BatchConvertDialog  # noqa: E402
from config_manager import ConfigManager  # noqa: E402
from main_window import MainWindow  # noqa: E402
from sheet_model import all_presets  # noqa: E402
from shortcuts_dialog import build_markdown, collect_menu_shortcuts, find_conflicts  # noqa: E402


class Stage4bUiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = self.tmp.name
        self.cfg = ConfigManager(os.path.join(d, "config.json"))
        self.cfg.set("body_dir", os.path.join(d, "Allies", "Body"))
        self.cfg.set("save_dir", os.path.join(d, "out"))
        os.makedirs(os.path.join(d, "Allies", "Body"))
        self.src = os.path.join(d, "src")
        os.makedirs(self.src)
        for n in ("H1WK_P001F.png", "H1WK_P002F.png", "H1WK_P003F.png"):
            _color_sheet(os.path.join(self.src, n))
        i18n.set_language("ko")
        self.win = MainWindow(self.cfg, AutoSaver(os.path.join(d, "autosave")))
        self.win.show()
        _app.processEvents()

    def tearDown(self):
        if self.win.model is not None:
            self.win.model.mark_saved()
            self.win.model.forced_dirty = False
        self.win.close()
        self.tmp.cleanup()

    def test_no_shortcut_conflicts_and_list_has_new_items(self):
        mb = self.win.menuBar()
        self.assertEqual(find_conflicts(mb), {})
        rows = collect_menu_shortcuts(mb)
        keys = {r[2] for r in rows}
        for k in ("Ctrl+E", "S", "Ctrl+Shift+B", "Shift+M", "Ctrl+PgDown", "Ctrl+/"):
            self.assertIn(k, keys)
        md = build_markdown(mb)
        self.assertIn("셀 편집 창", md)
        self.assertNotIn("keys.", md)  # 모든 키가 번역됨

    def test_next_prev_file(self):
        w = self.win
        self.assertTrue(w.open_file(os.path.join(self.src, "H1WK_P001F.png")))
        self.assertTrue(w.open_sibling(1))
        self.assertEqual(w.model.name, "H1WK_P002F")
        self.assertTrue(w.open_sibling(1))
        self.assertFalse(w.open_sibling(1))  # 마지막
        self.assertTrue(w.open_sibling(-1))
        self.assertEqual(w.model.name, "H1WK_P002F")

    def test_batch_dialog_runs_and_opens_result(self):
        out = os.path.join(self.tmp.name, "out2")
        dlg = BatchConvertDialog(self.win, self.cfg, all_presets([]), self.src)
        dlg.ed_out.setText(out)
        self.assertEqual(dlg.tree.topLevelItemCount(), 3)
        dlg.tree.topLevelItem(2).setCheckState(0, Qt.Unchecked)
        results = dlg.run(confirm=False)
        self.assertEqual(len(results), 2)
        self.assertTrue(os.path.exists(os.path.join(out, "H1WK_P001F_M.png")))
        self.assertFalse(os.path.exists(os.path.join(out, "H1WK_P003F.png")))
        self.assertEqual(self.cfg.get("batch_convert.out_dir"), os.path.normpath(out))
        dlg._on_double(dlg.tree.topLevelItem(0), 2)
        self.assertEqual(dlg.open_path, os.path.join(os.path.normpath(out), "H1WK_P001F.png"))
        # 같은 폴더는 거부
        dlg2 = BatchConvertDialog(self.win, self.cfg, all_presets([]), self.src)
        dlg2.ed_out.setText(self.src)
        dlg2.ed_in.setText(self.src)
        import batch_dialog
        orig = batch_dialog.QMessageBox.warning
        batch_dialog.QMessageBox.warning = staticmethod(lambda *a, **k: None)
        try:
            self.assertEqual(dlg2.run(confirm=False), [])
        finally:
            batch_dialog.QMessageBox.warning = orig


if __name__ == "__main__":
    unittest.main()
