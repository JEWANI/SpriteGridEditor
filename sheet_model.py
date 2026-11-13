"""
sheet_model.py
----------------
시트 RGBA 배열(NumPy), 그리드 정보, 시트 규격 프리셋, 문서 상태.

표기: 그리드는 **열×행(가로 칸×세로 칸)**, 셀 위치는 ``R{행}C{열}`` (1부터). 내부 인덱스는 0부터.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from typing import NamedTuple

import numpy as np

from history import History

# --------------------------------------------------------------------------- #
#  그리드 / 셀 영역
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class GridSpec:
    cols: int  # 열(가로 칸)
    rows: int  # 행(세로 칸)
    cell_w: int
    cell_h: int

    @property
    def sheet_w(self) -> int:
        return self.cols * self.cell_w

    @property
    def sheet_h(self) -> int:
        return self.rows * self.cell_h


class CellRect(NamedTuple):
    """셀 단위 사각형 (0-based, 끝 포함). r=행, c=열."""

    r0: int
    c0: int
    r1: int
    c1: int

    @property
    def rows(self) -> int:
        return self.r1 - self.r0 + 1

    @property
    def cols(self) -> int:
        return self.c1 - self.c0 + 1

    @property
    def shape(self) -> tuple[int, int]:
        return (self.rows, self.cols)

    @staticmethod
    def from_cells(a: tuple[int, int], b: tuple[int, int]) -> "CellRect":
        (ra, ca), (rb, cb) = a, b
        return CellRect(min(ra, rb), min(ca, cb), max(ra, rb), max(ca, cb))

    @staticmethod
    def single(r: int, c: int) -> "CellRect":
        return CellRect(r, c, r, c)


def cell_label(r: int, c: int) -> str:
    return f"R{r + 1}C{c + 1}"


def rect_label(rect: CellRect) -> str:
    if rect.rows == 1 and rect.cols == 1:
        return f"[R{rect.r0 + 1},C{rect.c0 + 1}]"
    return f"[R{rect.r0 + 1},C{rect.c0 + 1}~R{rect.r1 + 1},C{rect.c1 + 1}]"


# --------------------------------------------------------------------------- #
#  시트 규격 프리셋 (§3)
# --------------------------------------------------------------------------- #

BUILTIN_PRESETS: list[dict] = [
    # body_motion: Base Body 파일명 모션 접미사 (BodyF_H1WK.png 의 H1WK). 없으면 "".
    {"id": "H1WK", "name_key": "preset.H1WK", "cols": 3, "rows": 4,
     "cell_w": 100, "cell_h": 135, "body_motion": "H1WK", "builtin": True},
    {"id": "H2", "name_key": "preset.H2", "cols": 6, "rows": 5,
     "cell_w": 256, "cell_h": 256, "body_motion": "H2", "builtin": True},
]


def new_user_preset_id() -> str:
    return "u" + uuid.uuid4().hex[:8]


def make_user_preset(name: str, spec: GridSpec, body_motion: str = "") -> dict:
    return {
        "id": new_user_preset_id(), "name": name,
        "cols": spec.cols, "rows": spec.rows,
        "cell_w": spec.cell_w, "cell_h": spec.cell_h,
        "body_motion": body_motion.strip(),
    }


def preset_spec(preset: dict) -> GridSpec:
    return GridSpec(int(preset["cols"]), int(preset["rows"]),
                    int(preset["cell_w"]), int(preset["cell_h"]))


def all_presets(user_presets: list[dict]) -> list[dict]:
    """내장(H1·H2) + 사용자 프리셋. 사용자 프리셋의 id가 비었거나 중복이면 보정한다."""
    result = [dict(p) for p in BUILTIN_PRESETS]
    seen = {p["id"] for p in result}
    for p in user_presets:
        try:
            preset_spec(p)
        except (KeyError, TypeError, ValueError):
            continue
        q = dict(p)
        if not q.get("id") or q["id"] in seen:
            q["id"] = new_user_preset_id()
        q["builtin"] = False
        seen.add(q["id"])
        result.append(q)
    return result


def spec_from_sheet_size(sheet_w: int, sheet_h: int, cols: int, rows: int) -> GridSpec | None:
    """시트 크기 + 열·행 수 → 셀 크기. 나누어떨어지지 않으면 None."""
    if cols < 1 or rows < 1 or sheet_w < 1 or sheet_h < 1:
        return None
    if sheet_w % cols or sheet_h % rows:
        return None
    return GridSpec(cols, rows, sheet_w // cols, sheet_h // rows)


def spec_from_cell_size(cell_w: int, cell_h: int, cols: int, rows: int) -> GridSpec | None:
    """셀 크기 + 열·행 수 → 시트 크기(GridSpec.sheet_w/h)."""
    if cols < 1 or rows < 1 or cell_w < 1 or cell_h < 1:
        return None
    return GridSpec(cols, rows, cell_w, cell_h)


def find_matching_presets(presets: list[dict], width: int, height: int) -> list[dict]:
    return [p for p in presets
            if preset_spec(p).sheet_w == width and preset_spec(p).sheet_h == height]


def pick_preset_for_file(presets: list[dict], width: int, height: int, filename: str) -> dict | None:
    """크기가 일치하는 프리셋 중 파일명이 모션 키(H1WK/H2)로 시작하는 것을 우선한다."""
    matches = find_matching_presets(presets, width, height)
    if not matches:
        return None
    stem = filename.upper()
    for p in matches:
        motion = (p.get("body_motion") or "").upper()
        if motion and stem.startswith(motion):
            return p
    return matches[0]


# --------------------------------------------------------------------------- #
#  체형 (§3.1)
# --------------------------------------------------------------------------- #

GENDER_FEMALE = "F"
GENDER_MALE = "M"
GENDER_COMMON = "C"  # 체형 공용
GENDERS = (GENDER_FEMALE, GENDER_MALE, GENDER_COMMON)

_GENDER_RES = (
    re.compile(r"^Body([FM])(?:_|$)", re.IGNORECASE),  # BodyF_H1WK, BodyM_T2_H1WK
    re.compile(r"\d([FM])$"),  # H1WK_P001F
)


def detect_gender(filename_stem: str) -> str | None:
    """파일명에서 체형 자동 인식. 못 찾으면 None(= 공용으로 보고 사용자가 고른다)."""
    stem = re.sub(r"_M$", "", filename_stem)  # 마스크 접미사 `_M`은 체형이 아니다
    for pattern in _GENDER_RES:
        m = pattern.search(stem)
        if m:
            return m.group(1).upper()
    return None


# --------------------------------------------------------------------------- #
#  문서
# --------------------------------------------------------------------------- #


class SheetModel:
    """열려 있는 시트 한 장의 상태 (픽셀 + 그리드 + 체형/피부톤 + 실행취소 기록)."""

    def __init__(self, arr: np.ndarray, grid: GridSpec, preset_id: str = "custom",
                 name: str = "", source_path: str = ""):
        if arr.ndim != 3 or arr.shape[2] != 4 or arr.dtype != np.uint8:
            raise ValueError("시트 배열은 (H, W, 4) uint8 이어야 합니다")
        self.arr = np.ascontiguousarray(arr)
        self.grid = grid
        self.preset_id = preset_id
        self.name = name  # 확장자 제외 이름
        self.source_path = source_path  # 연 파일 경로
        self.save_path = ""  # 이번 세션에 저장한 경로
        self.gender = GENDER_COMMON
        self.skin = "T1"
        self.history = History()
        self.forced_dirty = False  # 자동 저장 복구본 등 — 저장하기 전까지 수정됨으로 취급
        # 인덱스 마스크 (0~4, CHR-ART.5 §3.3). has_mask=False면 마스크 없는 시트(회색만/컬러 원본).
        self.mask = np.zeros(self.arr.shape[:2], dtype=np.uint8)
        self.has_mask = False
        self.mask_snapped = 0  # 불러온 _M 파일에서 규격 밖 값을 스냅한 픽셀 수 (저장 전 검증에 표시)

    def set_mask(self, index: np.ndarray) -> None:
        """불러온 마스크를 그대로 붙인다 (실행 취소 기록 없음 — 열기·복구 전용)."""
        if index.shape != self.mask.shape:
            raise ValueError("마스크 크기가 시트와 다릅니다")
        self.mask[...] = index
        self.has_mask = True

    # -- 크기 / 그리드 ----------------------------------------------------
    @property
    def width(self) -> int:
        return int(self.arr.shape[1])

    @property
    def height(self) -> int:
        return int(self.arr.shape[0])

    def grid_matches_image(self) -> bool:
        return self.grid.sheet_w == self.width and self.grid.sheet_h == self.height

    def contains(self, rect: CellRect) -> bool:
        return 0 <= rect.r0 <= rect.r1 < self.grid.rows and 0 <= rect.c0 <= rect.c1 < self.grid.cols

    # -- 수정 상태 --------------------------------------------------------
    @property
    def dirty(self) -> bool:
        return self.forced_dirty or not self.history.is_clean()

    def mark_saved(self) -> None:
        self.history.mark_saved()
        self.forced_dirty = False

    @property
    def display_name(self) -> str:
        return f"{self.name}.png" if self.name else ""
