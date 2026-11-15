"""언어 파일 검증: 코드에서 쓰는 키가 ko/en 양쪽에 있고, 두 언어의 키·치환 자리({name})가 일치하는지."""

import glob
import json
import os
import re
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import i18n  # noqa: E402


def load(lang):
    with open(os.path.join(ROOT, "locales", f"{lang}.json"), encoding="utf-8") as f:
        return json.load(f)


KEY_PATTERNS = [
    re.compile(r'\btr\(\s*"([A-Za-z0-9_.]+)"'),
    re.compile(r'\b_t\([^,]+,\s*"([A-Za-z0-9_.]+)"'),
    re.compile(r'\b_action\(\s*"([A-Za-z0-9_.]+)"'),
    re.compile(r'\ba\(\s*"([A-Za-z0-9_.]+)"'),
    re.compile(r'\b_group\(\s*"([A-Za-z0-9_.]+)"'),
    re.compile(r'\b_label\(\s*"([A-Za-z0-9_.]+)"'),
    re.compile(r'"((?:status|edit|dlg|settings|validate|panel|act|menu|tb|gender|preset|btn|theme|note|canvas|layer|layerbar|keys|batch)\.[A-Za-z0-9_.]+)"'),
]
# 동적으로 조립되는 키 (f"bg.{mode}", f"note.{note}")
DYNAMIC = ["bg.checker", "bg.white", "bg.black", "bg.dots",
           "note.save_dir_missing", "note.save_dir_unwritable",
           "theme.dark", "theme.light", "theme.orange", "theme.midnight", "theme.forest", "theme.contrast",
           "dlg.import.resample_nearest", "dlg.import.resample_box", "dlg.import.resample_lanczos",
           "dlg.import.align_center", "dlg.import.align_bottom", "dlg.import.align_top",
           "dlg.import.scope_edge", "dlg.import.scope_all",
           "mask.idx0", "mask.idx1", "mask.idx2", "mask.idx3", "mask.idx4",
           "dlg.save.kind_U", "dlg.save.kind_P", "dlg.save.kind_G", "dlg.save.kind_N", "dlg.save.kind_S",
           "dlg.save.gender_name_F", "dlg.save.gender_name_M", "dlg.save.gender_name_C",
           "tool.select", "tool.pencil", "tool.fill", "tool.rect", "tool.eraser",
           "tool.move", "dlg.celledit.state_name_paint", "dlg.celledit.state_name_erase",
           "tool.index1", "tool.index2", "tool.index3", "tool.index4",
           "dlg.batch.st_ok", "dlg.batch.st_warn", "dlg.batch.st_skip", "dlg.batch.st_error",
           "dlg.batch.norm_auto", "dlg.batch.norm_on", "dlg.batch.norm_off",
           "maskview.off", "maskview.overlay", "maskview.only",
           "dlg.convert.view_src", "dlg.convert.view_gray", "dlg.convert.view_mask", "dlg.convert.view_tint"]


def keys_used_in_code():
    keys = set(DYNAMIC)
    for path in glob.glob(os.path.join(ROOT, "*.py")):
        with open(path, encoding="utf-8") as f:
            text = f.read()
        for pat in KEY_PATTERNS:
            keys.update(m.group(1) for m in pat.finditer(text))
    return keys


def placeholders(text):
    return set(re.findall(r"(?<!\{)\{([a-z_]+)\}(?!\})", text))


class LocaleTests(unittest.TestCase):
    def test_same_keys_in_both_languages(self):
        ko, en = load("ko"), load("en")
        self.assertEqual(set(ko) - set(en), set(), "en.json 에 없는 키")
        self.assertEqual(set(en) - set(ko), set(), "ko.json 에 없는 키")

    def test_every_key_used_in_code_exists(self):
        ko = load("ko")
        missing = sorted(k for k in keys_used_in_code() if k not in ko)
        self.assertEqual(missing, [])

    def test_placeholders_match(self):
        ko, en = load("ko"), load("en")
        for key in ko:
            self.assertEqual(placeholders(ko[key]), placeholders(en[key]), key)

    def test_fallback_to_ko_then_key(self):
        i18n.set_language("en")
        self.assertEqual(i18n.tr("this.key.does.not.exist"), "this.key.does.not.exist")
        i18n.set_language("ko")
        self.assertEqual(i18n.tr("btn.close"), "닫기")
        i18n.set_language("en")
        self.assertEqual(i18n.tr("btn.close"), "Close")
        i18n.set_language("ko")

    def test_format_arguments(self):
        i18n.set_language("ko")
        self.assertEqual(i18n.tr("grid.text", cols=3, rows=4, cw=100, ch=135), "열 3 × 행 4  셀 100×135")


if __name__ == "__main__":
    unittest.main()
