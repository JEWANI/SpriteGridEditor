"""
export_utils.py
-----------------
저장 처리 (기획서 §4.4) — 알파 임계값, 투명 픽셀 RGB 정리, PNG 저장, 백업, 저장 폴더 결정,
저장 전 검증(시트 쪽). Qt 의존 없음 → 단위 테스트 가능.

- 포맷은 PNG(RGBA)만. sRGB/감마/ICC 메타데이터는 쓰지 않는다 (Image.fromarray 기본값).
- 알파 임계값: 임계값 미만 알파 → 0, 이상 → 255. 완전 투명 픽셀의 RGB는 기본 (0,0,0).
- 덮어쓰기 보호: 같은 이름이 있으면 ``<이름>_backup.png`` 로 이전 파일을 보관한 뒤 저장.
"""

from __future__ import annotations

import os
import re
import shutil
import tempfile
from dataclasses import dataclass

import numpy as np
from PIL import Image

from config_manager import base_dir
from sheet_model import GENDER_COMMON, GridSpec

DEFAULT_ALPHA_THRESHOLD = 128
TRANSPARENT_BLACK = "black"
TRANSPARENT_EXTEND = "extend"

# CHR-ART.5 §3.2: H1WK_{U/P/G/N/S}{번호3자리}{F/M} (체형 공용은 F/M 없음)
NAME_KINDS = ("U", "P", "G", "N", "S")
_NAME_RE = re.compile(r"^[A-Za-z0-9]+_([UPGNS])(\d{3})([FM])?$")
# 구 방어구 시트처럼 종류 문자만 규칙 밖인 이름 (H1WK_C003 · H1WK_TC201 · H1WK_BU001F) — §3.2 "변환 대상"
_LEGACY_RE = re.compile(r"^[A-Za-z0-9]+_([A-Za-z]{1,2})(\d{3})([FM])?$")


# --------------------------------------------------------------------------- #
#  픽셀 처리
# --------------------------------------------------------------------------- #


def apply_alpha_threshold(arr: np.ndarray, threshold: int) -> np.ndarray:
    """알파 < threshold → 0, 그 외 → 255 로 정리한 새 배열."""
    out = arr.copy()
    out[..., 3] = np.where(out[..., 3] >= int(threshold), 255, 0).astype(np.uint8)
    return out


def _extend_edge_colors(arr: np.ndarray, iterations: int = 4) -> np.ndarray:
    """완전 투명 픽셀의 RGB를 이웃 불투명 픽셀 색으로 채운다 (최대 iterations 픽셀 거리)."""
    out = arr.copy()
    opaque = out[..., 3] > 0
    h, w = opaque.shape
    for _ in range(iterations):
        if opaque.all():
            break
        new_rgb = out[..., :3].copy()
        new_opaque = opaque.copy()
        for dy, dx in ((-1, 0), (1, 0), (0, -1), (0, 1), (-1, -1), (-1, 1), (1, -1), (1, 1)):
            src_y = slice(max(0, -dy), h - max(0, dy))
            dst_y = slice(max(0, dy), h - max(0, -dy))
            src_x = slice(max(0, -dx), w - max(0, dx))
            dst_x = slice(max(0, dx), w - max(0, -dx))
            # dst 위치가 아직 비어 있고 src 위치가 채워져 있으면 src 색을 가져온다
            take = (~new_opaque[dst_y, dst_x]) & opaque[src_y, src_x]
            if take.any():
                tmp = new_rgb[dst_y, dst_x]
                tmp[take] = out[src_y, src_x, :3][take]
                new_rgb[dst_y, dst_x] = tmp
                new_opaque[dst_y, dst_x] |= take
        out[..., :3] = new_rgb
        opaque = new_opaque
    return out


def normalize_transparent_rgb(arr: np.ndarray, mode: str = TRANSPARENT_BLACK) -> np.ndarray:
    """알파 0 픽셀의 RGB 정리. black → (0,0,0), extend → 가장자리 색 확장."""
    if mode == TRANSPARENT_EXTEND:
        out = _extend_edge_colors(arr)
        transparent = out[..., 3] == 0
        # 확장이 닿지 않은 먼 투명 영역은 (0,0,0)
        far = transparent & (arr[..., 3] == 0)
        remaining = far & (out[..., :3].sum(axis=2) == 0)
        out[remaining, :3] = 0
        return out
    out = arr.copy()
    out[out[..., 3] == 0, :3] = 0
    return out


def prepare_export(arr: np.ndarray, threshold_enabled: bool, threshold: int,
                   transparent_rgb: str = TRANSPARENT_BLACK) -> np.ndarray:
    """저장 직전 변환: (옵션) 알파 임계값 → 투명 RGB 정리. 원본 arr는 건드리지 않는다."""
    out = apply_alpha_threshold(arr, threshold) if threshold_enabled else arr.copy()
    return normalize_transparent_rgb(out, transparent_rgb)


# --------------------------------------------------------------------------- #
#  파일 저장
# --------------------------------------------------------------------------- #


def backup_path_for(path: str) -> str:
    root, _ext = os.path.splitext(path)
    return f"{root}_backup.png"


def save_png_rgba(arr: np.ndarray, path: str) -> str | None:
    """RGBA PNG 저장. 같은 이름 파일이 있으면 ``_backup.png`` 로 보관한다.

    임시 파일에 먼저 쓴 뒤 교체하므로 저장 중 오류가 나도 기존 파일이 깨지지 않는다.
    반환: 백업 파일 경로(백업을 만들었을 때) 또는 None.
    """
    folder = os.path.dirname(os.path.abspath(path))
    os.makedirs(folder, exist_ok=True)

    fd, tmp_path = tempfile.mkstemp(suffix=".png", dir=folder)
    os.close(fd)
    try:
        Image.fromarray(np.ascontiguousarray(arr), "RGBA").save(tmp_path, format="PNG")
        backup = None
        if os.path.exists(path):
            backup = backup_path_for(path)
            shutil.copy2(path, backup)
        os.replace(tmp_path, path)
        return backup
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


def save_png_l(values: np.ndarray, path: str) -> str | None:
    """8비트 그레이스케일(1채널, 모드 L) PNG 저장 — 인덱스 마스크 `_M.png` 용 (§4.4).

    Unity `OutfitMaskImporter`가 R8로 읽는다. sRGB·감마 메타데이터 없음. 백업·임시 파일 교체는 RGBA와 같다.
    """
    folder = os.path.dirname(os.path.abspath(path))
    os.makedirs(folder, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(suffix=".png", dir=folder)
    os.close(fd)
    try:
        Image.fromarray(np.ascontiguousarray(values, dtype=np.uint8), "L").save(tmp_path, format="PNG")
        backup = None
        if os.path.exists(path):
            backup = backup_path_for(path)
            shutil.copy2(path, backup)
        os.replace(tmp_path, path)
        return backup
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


def load_png_rgba(path: str) -> np.ndarray:
    with Image.open(path) as im:
        return np.array(im.convert("RGBA"), dtype=np.uint8)


# --------------------------------------------------------------------------- #
#  저장 폴더 (§4.4)
# --------------------------------------------------------------------------- #


def _is_writable_dir(folder: str) -> bool:
    try:
        os.makedirs(folder, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=folder)
        os.close(fd)
        os.remove(tmp)
        return True
    except OSError:
        return False


def default_output_dir() -> str:
    return os.path.join(base_dir(), "output")


def documents_output_dir() -> str:
    return os.path.join(os.path.expanduser("~"), "Documents", "SpriteGridEditor", "output")


def resolve_save_dir(configured: str) -> tuple[str, str | None]:
    """저장 폴더 결정. 반환: (폴더, 안내 키).

    - 지정 폴더가 있고 쓸 수 있으면 그대로.
    - 지정했지만 사라졌/접근 불가 → ``output/`` 로 되돌리고 'save_dir_missing'.
    - ``output/`` 가 쓰기 불가(Program Files 등) → 문서 폴더 하위 대체, 'save_dir_unwritable'.
    """
    note = None
    if configured:
        if os.path.isdir(configured) and _is_writable_dir(configured):
            return configured, None
        note = "save_dir_missing"
    out = default_output_dir()
    if _is_writable_dir(out):
        return out, note
    return documents_output_dir(), "save_dir_unwritable"


# --------------------------------------------------------------------------- #
#  파일명 / 검증 (§3.1, §4.3-5 중 시트 쪽)
# --------------------------------------------------------------------------- #


def build_name(prefix: str, kind: str, number: int, gender: str) -> str:
    """파일명 도우미: ``{prefix}_{종류}{번호3자리}{체형}`` (체형 공용이면 체형 문자 없음)."""
    suffix = gender if gender in ("F", "M") else ""
    return f"{prefix}_{kind}{int(number):03d}{suffix}"


def parse_name(stem: str) -> tuple[str, str, int, str] | None:
    """규칙에 맞으면 (prefix, 종류, 번호, 체형문자 또는 "") 반환."""
    m = _NAME_RE.match(stem)
    if not m:
        return None
    prefix = stem.split("_", 1)[0]
    return prefix, m.group(1), int(m.group(2)), m.group(3) or ""


@dataclass
class Issue:
    level: str  # "error" (저장 차단) | "warn" (경고만)
    key: str  # i18n 키 (validate.*)
    fmt: dict


def validate_name(stem: str, gender: str, motion: str = "", folder: str = "",
                  has_mask: bool | None = None) -> list[Issue]:
    """파일명 검사 (CHR-ART.5 §3.2). 모두 경고 — 저장은 허용.

    - 규칙 형식 / 대소문자 / 구 종류 문자(C·T·B…) / 번호 000 / 체형 접미사
    - motion: 시트 규격의 모션(H1WK·H2)과 접두사가 다르면 경고
    - folder: 저장 폴더에 같은 ID의 공용↔체형별 파일이 함께 있거나, 마스크 없이 저장하는데
      예전 ``_M.png`` 가 남아 있으면 경고 (has_mask=False 일 때)
    """
    issues: list[Issue] = []
    if re.search(r"_M$", stem):
        issues.append(Issue("warn", "validate.name_mask_suffix", {"name": stem}))
    parsed = parse_name(stem)
    if parsed is None:
        upper = parse_name(stem.upper())
        legacy = _LEGACY_RE.match(stem)
        if upper is not None:
            issues.append(Issue("warn", "validate.name_case", {"name": stem, "fixed": stem.upper()}))
        elif legacy and legacy.group(1).upper() not in NAME_KINDS:
            issues.append(Issue("warn", "validate.name_legacy_kind", {"kind": legacy.group(1)}))
        else:
            issues.append(Issue("warn", "validate.name_pattern", {"name": stem}))
        return issues
    prefix, kind, number, suffix = parsed
    if number == 0:
        issues.append(Issue("warn", "validate.name_number_zero", {}))
    if gender in ("F", "M"):
        if suffix != gender:
            issues.append(Issue("warn", "validate.gender_suffix",
                                {"gender": gender, "suffix": suffix or "-"}))
    elif gender == GENDER_COMMON and suffix:
        issues.append(Issue("warn", "validate.common_has_suffix", {"suffix": suffix}))
    if motion and prefix != motion:
        issues.append(Issue("warn", "validate.name_motion", {"prefix": prefix, "motion": motion}))
    if folder and os.path.isdir(folder):
        base = f"{prefix}_{kind}{number:03d}"
        if suffix:
            if os.path.exists(os.path.join(folder, base + ".png")):
                issues.append(Issue("warn", "validate.name_common_exists", {"name": base + ".png"}))
        else:
            found = [base + g + ".png" for g in ("F", "M") if os.path.exists(os.path.join(folder, base + g + ".png"))]
            if found:
                issues.append(Issue("warn", "validate.name_gendered_exists", {"names": ", ".join(found)}))
        if has_mask is False and os.path.exists(os.path.join(folder, stem + "_M.png")):
            issues.append(Issue("warn", "validate.name_stale_mask", {"name": stem + "_M.png"}))
    return issues


def validate_sheet(arr: np.ndarray, grid: GridSpec) -> list[Issue]:
    """시트 크기가 열×셀너비·행×셀높이와 맞지 않으면 저장 차단 (§3)."""
    h, w = arr.shape[:2]
    if (w, h) != (grid.sheet_w, grid.sheet_h):
        return [Issue("error", "validate.grid_mismatch",
                      {"w": w, "h": h, "gw": grid.sheet_w, "gh": grid.sheet_h})]
    return []
