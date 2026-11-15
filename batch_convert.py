"""
batch_convert.py
------------------
폴더 일괄 변환 (기획서 §9 4단계 — 배치 변환). Qt 의존 없음 → 단위 테스트 가능.

폴더 안의 컬러 옷 시트를 **한 번에 회색 + 마스크**로 바꿔 출력 폴더에 저장한다.
변환 규칙은 한 장씩 변환(`도구 → 컬러→회색+마스크 변환`)과 같다 (mask_convert).

- 대상: `*.png` 중 `_M.png`(마스크)·`_backup.png`(백업)은 제외. 크기가 시트 규격 프리셋과 맞지 않으면 건너뜀.
- 색 기반 분할의 영역 번호는 **픽셀 수가 많은 색부터 1(주)·2(보조)·3·4** — 대화상자처럼 손으로 바꿀 수 없으니
  결과를 열어 마스크 보정으로 고친다.
- 밝기 정규화: auto(한 장씩 변환의 기본값과 같은 판단) / on / off.
- 저장은 메인 저장과 같은 처리 (알파 임계값·투명 RGB·마스크 검증·같은 이름이면 `_backup.png`·Unity meta 복제).
- 입력 폴더와 출력 폴더가 같으면 원본(컬러)을 덮어쓰므로 막는다.
"""

from __future__ import annotations

import glob
import os
from dataclasses import dataclass, field

import numpy as np

import export_utils
import mask_convert as mc
import unity_meta
from sheet_model import GENDER_COMMON, detect_gender, pick_preset_for_file, preset_spec

MODE_COLOR, MODE_HSPLIT, MODE_NONE = "color", "hsplit", "none"
NORM_AUTO, NORM_ON, NORM_OFF = "auto", "on", "off"

ST_OK = "ok"
ST_WARN = "warn"
ST_SKIP = "skip"
ST_ERROR = "error"


@dataclass
class BatchOptions:
    out_dir: str
    mode: str = MODE_COLOR
    regions: int = 2
    l_weight: int = 50  # %
    hsplit_y: int = 0  # 0 = 셀 높이 절반
    upper: int = 1
    lower: int = 2
    despeckle: bool = True
    normalize: str = NORM_AUTO
    alpha_enabled: bool = True
    alpha_threshold: int = export_utils.DEFAULT_ALPHA_THRESHOLD
    transparent_rgb: str = export_utils.TRANSPARENT_BLACK
    missing_warn_pct: float = 10.0
    meta_copy: bool = True


@dataclass
class Note:
    key: str
    fmt: dict = field(default_factory=dict)


@dataclass
class BatchResult:
    src: str
    status: str
    out_path: str = ""
    mask_path: str = ""
    notes: list[Note] = field(default_factory=list)


def list_sources(folder: str) -> list[str]:
    """변환 대상 PNG 목록 (이름순). 마스크·백업 파일 제외."""
    if not folder or not os.path.isdir(folder):
        return []
    out = []
    for p in sorted(glob.glob(os.path.join(folder, "*.png")), key=lambda s: os.path.basename(s).lower()):
        stem = os.path.splitext(os.path.basename(p))[0]
        if mc.is_mask_name(stem) or stem.endswith("_backup"):
            continue
        out.append(p)
    return out


def same_folder(a: str, b: str) -> bool:
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def convert_array(arr: np.ndarray, grid, opts: BatchOptions) -> tuple[np.ndarray, np.ndarray | None, bool]:
    """한 장 변환 → (회색 RGBA, 인덱스 마스크 또는 None, 정규화 여부)."""
    if opts.normalize == NORM_AUTO:
        norm = mc.should_normalize(mc.brightness_stats(arr))
    else:
        norm = opts.normalize == NORM_ON
    gray = mc.make_gray_rgba(arr, norm)
    if opts.mode == MODE_NONE:
        return gray, None, norm
    if opts.mode == MODE_HSPLIT:
        y = opts.hsplit_y if 0 < opts.hsplit_y <= grid.cell_h else grid.cell_h // 2
        index = mc.horizontal_split(arr, grid, y, opts.upper, opts.lower)
    else:
        cl = mc.kmeans(arr, opts.regions, opts.l_weight / 100.0)
        index = mc.labels_to_index(cl.labels, [min(i + 1, 4) for i in range(cl.k)])
    if opts.despeckle:
        index = mc.despeckle(index, mc.opaque_mask(arr))
    index, _n = mc.enforce_alpha(index, arr[..., 3])
    return gray, index, norm


def convert_file(path: str, presets: list[dict], opts: BatchOptions) -> BatchResult:
    name = os.path.basename(path)
    stem = os.path.splitext(name)[0]
    try:
        arr = export_utils.load_png_rgba(path)
    except Exception as e:  # 깨진 파일 등
        return BatchResult(path, ST_ERROR, notes=[Note("batch.note_load_failed", {"error": str(e)})])
    h, w = arr.shape[:2]
    preset = pick_preset_for_file(presets, w, h, stem)
    if preset is None:
        return BatchResult(path, ST_SKIP, notes=[Note("batch.note_no_preset", {"w": w, "h": h})])
    grid = preset_spec(preset)
    if not mc.opaque_mask(arr).any():
        return BatchResult(path, ST_SKIP, notes=[Note("batch.note_empty")])

    gray, index, norm = convert_array(arr, grid, opts)
    out = export_utils.prepare_export(gray, opts.alpha_enabled, opts.alpha_threshold, opts.transparent_rgb)
    res = BatchResult(path, ST_OK, out_path=os.path.join(opts.out_dir, name))
    mask_index = None
    if index is not None:
        mask_index, issues = mc.validate_mask(out, index, 0, opts.missing_warn_pct)
        for i in issues:
            if i.level == "error":
                return BatchResult(path, ST_ERROR, notes=[Note(i.key, i.fmt)])
            if i.level == "warn":
                res.status = ST_WARN
                res.notes.append(Note(i.key, i.fmt))
        res.mask_path = mc.mask_path_for(res.out_path)
    gender = detect_gender(stem) or GENDER_COMMON
    for i in export_utils.validate_name(stem, gender, preset.get("body_motion", ""), opts.out_dir,
                                         index is not None):
        res.status = ST_WARN
        res.notes.append(Note(i.key, i.fmt))
    if norm:  # 경고 뒤에 (결과 칸에는 첫 메모만 보이므로 경고가 먼저)
        res.notes.append(Note("batch.note_normalized"))
    try:
        backup = export_utils.save_png_rgba(out, res.out_path)
        if backup:
            res.notes.append(Note("batch.note_backup", {"name": os.path.basename(backup)}))
        if mask_index is not None:
            export_utils.save_png_l(mc.index_to_png(mask_index), res.mask_path)
    except Exception as e:
        return BatchResult(path, ST_ERROR, notes=[Note("batch.note_save_failed", {"error": str(e)})])
    if opts.meta_copy:
        for p in [res.out_path] + ([res.mask_path] if res.mask_path else []):
            code, tpl = unity_meta.copy_meta_for(p, (w, h))
            if code == unity_meta.META_OK:
                res.notes.append(Note("batch.note_meta", {"name": os.path.basename(p) + ".meta", "template": tpl}))
    return res
