"""
import_utils.py
----------------
이미지 가져오기 전처리 (Qt 없음 — 단위 테스트 가능).

A1111 / Gemini 등으로 만든 이미지를 시트로 열기 전에
  1) 배경 감지   : 투명 픽셀이 있는지, 모서리(테두리) 색이 단색인지
  2) 배경 투명화 : 지정한 색(들)과 허용 오차 안의 픽셀을 알파 0으로 (가장자리와 이어진 부분만 / 전부)
  3) 크기 조정   : 비율 유지 자동 계산 또는 가로·세로 직접 지정, 리샘플링 선택
  4) 시트 맞춤   : 시트 규격 크기로 투명 여백 채우기 / 넘치면 잘라내기 (가운데·위·아래 정렬)
순서는 항상 배경 투명화 → 크기 조정 → 시트 맞춤 (원본 해상도에서 색을 고르는 게 정확하다).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from PIL import Image

# --------------------------------------------------------------------------- #
#  배경 감지
# --------------------------------------------------------------------------- #

BG_TRANSPARENT = "transparent"  # 이미 투명 배경이 있음
BG_SINGLE = "single"  # 테두리가 한 가지 색
BG_MULTI = "multi"  # 테두리 색이 여러 가지 (그라데이션·무늬 배경)

# 테두리 픽셀이 이 비율 이상 투명이면 "이미 투명 배경"으로 본다
TRANSPARENT_BORDER_RATIO = 0.5
# 테두리 픽셀 중 대표색(허용 오차 안)이 이 비율 이상이면 단색 배경
SINGLE_COLOR_RATIO = 0.9


@dataclass
class BackgroundInfo:
    kind: str  # BG_TRANSPARENT | BG_SINGLE | BG_MULTI
    color: tuple[int, int, int]  # 테두리 대표색 (가장 많은 색)
    ratio: float  # 대표색이 테두리에서 차지하는 비율 (0~1)
    corner_colors: list[tuple[int, int, int]] = field(default_factory=list)  # 서로 다른 모서리 색


def _border_pixels(arr: np.ndarray) -> np.ndarray:
    """테두리 1px 링의 픽셀 (N, 4)."""
    h, w = arr.shape[:2]
    if h < 2 or w < 2:
        return arr.reshape(-1, 4)
    return np.concatenate([arr[0, :], arr[-1, :], arr[1:-1, 0], arr[1:-1, -1]], axis=0)


def color_distance(rgb: np.ndarray, color: tuple[int, int, int]) -> np.ndarray:
    """채널별 차이의 최댓값 (Chebyshev). 허용 오차 비교에 쓴다."""
    c = np.asarray(color, dtype=np.int16)
    return np.abs(rgb.astype(np.int16) - c).max(axis=-1)


def detect_background(arr: np.ndarray, tolerance: int = 16) -> BackgroundInfo:
    border = _border_pixels(arr)
    alpha = border[:, 3]
    if (alpha < 128).mean() >= TRANSPARENT_BORDER_RATIO:
        return BackgroundInfo(BG_TRANSPARENT, (0, 0, 0), 1.0, [])

    opaque = border[alpha >= 128][:, :3]
    if len(opaque) == 0:
        return BackgroundInfo(BG_TRANSPARENT, (0, 0, 0), 1.0, [])

    # 가장 많은 색 (4단계로 양자화해 JPEG 잡음을 묶는다)
    q = (opaque // 4).astype(np.int32)
    keys = (q[:, 0] << 12) | (q[:, 1] << 6) | q[:, 2]
    vals, counts = np.unique(keys, return_counts=True)
    top = vals[np.argmax(counts)]
    members = opaque[keys == top]
    color = tuple(int(v) for v in np.median(members, axis=0).round())
    ratio = float((color_distance(opaque, color) <= tolerance).mean())

    h, w = arr.shape[:2]
    corners: list[tuple[int, int, int]] = []
    for y, x in ((0, 0), (0, w - 1), (h - 1, 0), (h - 1, w - 1)):
        px = arr[y, x]
        if px[3] < 128:
            continue
        cc = tuple(int(v) for v in px[:3])
        if all(color_distance(np.array(cc), o) > tolerance for o in corners):
            corners.append(cc)

    kind = BG_SINGLE if ratio >= SINGLE_COLOR_RATIO else BG_MULTI
    return BackgroundInfo(kind, color, ratio, corners)


def has_transparency(arr: np.ndarray) -> bool:
    return bool((arr[:, :, 3] < 255).any())


# --------------------------------------------------------------------------- #
#  배경 투명화
# --------------------------------------------------------------------------- #

SCOPE_EDGE = "edge"  # 이미지 가장자리와 이어진 부분만 (캐릭터 안쪽의 같은 색은 남김)
SCOPE_ALL = "all"  # 이미지 전체에서 같은 색 전부


def _propagate_runs(match: np.ndarray, filled: np.ndarray) -> np.ndarray:
    """각 행에서 `match` 연속 구간 중 하나라도 `filled`인 구간을 통째로 채운다 (벡터 연산)."""
    prev = np.zeros_like(match)
    prev[:, 1:] = match[:, :-1]
    start = match & ~prev  # 행 첫 칸은 prev=False 라서 구간이 행을 넘지 않는다
    run_id = np.cumsum(start.ravel()) * match.ravel()
    hit = np.bincount(run_id[filled.ravel() & match.ravel()], minlength=int(run_id.max()) + 1) > 0
    hit[0] = False
    return hit[run_id].reshape(match.shape)


def edge_connected(match: np.ndarray) -> np.ndarray:
    """`match` 중 이미지 테두리와 4방향으로 이어진 부분. 행/열 방향 구간 채우기를 수렴할 때까지 반복."""
    filled = np.zeros_like(match)
    filled[0, :] = match[0, :]
    filled[-1, :] = match[-1, :]
    filled[:, 0] |= match[:, 0]
    filled[:, -1] |= match[:, -1]
    for _ in range(500):  # 꺾이는 횟수만큼만 돌면 되므로 보통 10회 안쪽
        before = int(filled.sum())
        filled = _propagate_runs(match, filled)
        filled = _propagate_runs(np.ascontiguousarray(match.T), np.ascontiguousarray(filled.T)).T
        if int(filled.sum()) == before:
            break
    return filled


def background_mask(arr: np.ndarray, colors: list[tuple[int, int, int]], tolerance: int,
                    scope: str = SCOPE_EDGE) -> np.ndarray:
    """투명하게 만들 픽셀 마스크 (H, W) bool."""
    if not colors:
        return np.zeros(arr.shape[:2], dtype=bool)
    rgb = arr[:, :, :3]
    match = np.zeros(arr.shape[:2], dtype=bool)
    for c in colors:
        match |= color_distance(rgb, c) <= tolerance
    match &= arr[:, :, 3] > 0
    if scope == SCOPE_EDGE:
        match = edge_connected(match)
    return match


def remove_background(arr: np.ndarray, colors: list[tuple[int, int, int]], tolerance: int,
                      scope: str = SCOPE_EDGE) -> np.ndarray:
    """배경색 픽셀을 완전 투명(RGBA 0,0,0,0)으로 바꾼 사본."""
    out = arr.copy()
    out[background_mask(arr, colors, tolerance, scope)] = 0
    return out


# --------------------------------------------------------------------------- #
#  크기 조정
# --------------------------------------------------------------------------- #

RESAMPLE_NEAREST = "nearest"  # 도트 유지 (기본)
RESAMPLE_BOX = "box"  # 평균 — 큰 AI 그림을 부드럽게 축소
RESAMPLE_LANCZOS = "lanczos"

_PIL_RESAMPLE = {
    RESAMPLE_NEAREST: Image.NEAREST,
    RESAMPLE_BOX: Image.BOX,
    RESAMPLE_LANCZOS: Image.LANCZOS,
}


def keep_ratio_height(src_w: int, src_h: int, new_w: int) -> int:
    return max(1, round(src_h * new_w / src_w))


def keep_ratio_width(src_w: int, src_h: int, new_h: int) -> int:
    return max(1, round(src_w * new_h / src_h))


def fit_inside(src_w: int, src_h: int, box_w: int, box_h: int) -> tuple[int, int]:
    """비율을 유지하면서 box 안에 들어가는 가장 큰 크기."""
    scale = min(box_w / src_w, box_h / src_h)
    return max(1, round(src_w * scale)), max(1, round(src_h * scale))


def resize(arr: np.ndarray, width: int, height: int, method: str = RESAMPLE_NEAREST) -> np.ndarray:
    h, w = arr.shape[:2]
    if (w, h) == (width, height):
        return arr.copy()
    im = Image.fromarray(arr, "RGBA")  # Pillow는 RGBA 리사이즈 때 알파를 곱해(premultiply) 처리한다
    out = im.resize((width, height), _PIL_RESAMPLE.get(method, Image.NEAREST))
    return np.array(out, dtype=np.uint8)


ALIGN_CENTER = "center"
ALIGN_TOP = "top"
ALIGN_BOTTOM = "bottom"


def place_on_canvas(arr: np.ndarray, canvas_w: int, canvas_h: int,
                    align: str = ALIGN_CENTER) -> np.ndarray:
    """투명 캔버스(canvas_w × canvas_h)에 배치. 작으면 여백, 크면 잘라낸다. 가로는 항상 가운데."""
    h, w = arr.shape[:2]
    out = np.zeros((canvas_h, canvas_w, 4), dtype=np.uint8)
    dx = (canvas_w - w) // 2
    if align == ALIGN_TOP:
        dy = 0
    elif align == ALIGN_BOTTOM:
        dy = canvas_h - h
    else:
        dy = (canvas_h - h) // 2
    # 원본/대상 겹치는 영역
    sx0, dx0 = max(0, -dx), max(0, dx)
    sy0, dy0 = max(0, -dy), max(0, dy)
    cw = min(w - sx0, canvas_w - dx0)
    ch = min(h - sy0, canvas_h - dy0)
    if cw > 0 and ch > 0:
        out[dy0:dy0 + ch, dx0:dx0 + cw] = arr[sy0:sy0 + ch, sx0:sx0 + cw]
    return out


# --------------------------------------------------------------------------- #
#  전체 처리
# --------------------------------------------------------------------------- #


@dataclass
class ImportOptions:
    bg_colors: list[tuple[int, int, int]] = field(default_factory=list)  # 비면 배경 그대로
    tolerance: int = 32
    scope: str = SCOPE_EDGE
    width: int = 0  # 0이면 원본 크기
    height: int = 0
    resample: str = RESAMPLE_NEAREST
    canvas: tuple[int, int] | None = None  # 시트 크기로 맞춤 (여백/잘라내기). None이면 안 함
    align: str = ALIGN_CENTER


def process(arr: np.ndarray, opt: ImportOptions) -> np.ndarray:
    out = remove_background(arr, opt.bg_colors, opt.tolerance, opt.scope) if opt.bg_colors else arr
    if opt.width > 0 and opt.height > 0:
        out = resize(out, opt.width, opt.height, opt.resample)
    if opt.canvas is not None and (out.shape[1], out.shape[0]) != opt.canvas:
        out = place_on_canvas(out, opt.canvas[0], opt.canvas[1], opt.align)
    return np.ascontiguousarray(out)


def color_hex(color: tuple[int, int, int]) -> str:
    return "#{:02X}{:02X}{:02X}".format(*color)
