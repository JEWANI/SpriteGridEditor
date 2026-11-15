"""
pixel_tools.py
----------------
4단계(v0.4.0) 픽셀 편집 로직 — **셀 편집 창**(칠하기·지우기·스포이트)과 **자유 선택 이동**.
Qt 의존 없음 → 단위 테스트 가능. 기획서 EDT-ETC.6 §4.2 (추가 8).

- 칠하기: 정사각형 브러시로 RGBA를 **알파 255**로 칠한다 (반투명 잔상 방지). 마스크가 있으면 같은 자리에
  지정한 인덱스(스포이트로 함께 가져온 값)를 기록한다 — 회색 옷 시트에 칠해도 영역 누락이 생기지 않게.
- 지우기: RGBA (0,0,0,0) + 마스크 인덱스 0.
- 스포이트: 그 픽셀의 RGBA와 마스크 인덱스를 함께 돌려준다.
- 범위(bounds): 칠할 수 있는 사각형 (셀 편집 창의 편집 범위). 밖은 건드리지 않는다.
- 자유 선택 이동: 셀 경계와 무관한 픽셀 사각형을 (dx, dy) 옮긴다. 원래 자리는 투명으로 비우고,
  옮긴 블록은 **불투명 픽셀만** 목적지에 덮는다(투명 부분이 옆 프레임을 지우지 않게). 마스크도 같이 움직인다.
  리사이즈·회전은 없다 (§4.4 Nearest 원칙).
"""

from __future__ import annotations

import numpy as np

from history import Edit
from mask_tools import line_points

MAX_BRUSH = 32

Bounds = tuple[int, int, int, int]  # (y0, y1, x0, x1), 끝 미포함
DirtyRect = tuple[int, int, int, int]


def _union(a: DirtyRect | None, b: DirtyRect | None) -> DirtyRect | None:
    if a is None:
        return b
    if b is None:
        return a
    return min(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), max(a[3], b[3])


def _brush_box(x: int, y: int, size: int, bounds: Bounds) -> DirtyRect | None:
    size = max(1, min(MAX_BRUSH, int(size)))
    by0, by1, bx0, bx1 = bounds
    half = size // 2
    y0, y1 = max(by0, y - half), min(by1, y - half + size)
    x0, x1 = max(bx0, x - half), min(bx1, x - half + size)
    if y0 >= y1 or x0 >= x1:
        return None
    return y0, y1, x0, x1


def clip_bounds(bounds: Bounds, shape: tuple[int, ...]) -> Bounds:
    y0, y1, x0, x1 = bounds
    return max(0, y0), min(shape[0], y1), max(0, x0), min(shape[1], x1)


# --------------------------------------------------------------------------- #
#  칠하기 / 지우기 / 스포이트                                                  #
# --------------------------------------------------------------------------- #
def paint_stamp(arr: np.ndarray, mask: np.ndarray | None, x: int, y: int, size: int,
                rgb: tuple[int, int, int], index: int | None, bounds: Bounds) -> DirtyRect | None:
    """(x, y) 중심 size×size 브러시로 rgb(알파 255)를 칠한다. index가 있으면 마스크에도 기록."""
    box = _brush_box(x, y, size, clip_bounds(bounds, arr.shape))
    if box is None:
        return None
    y0, y1, x0, x1 = box
    color = np.array([rgb[0], rgb[1], rgb[2], 255], dtype=np.uint8)
    view = arr[y0:y1, x0:x1]
    changed = (view != color).any(axis=-1)
    mview = None
    if mask is not None and index is not None:
        mview = mask[y0:y1, x0:x1]
        changed |= mview != index
    if not changed.any():
        return None
    view[...] = color
    if mview is not None:
        mview[...] = index
    return box


def erase_stamp(arr: np.ndarray, mask: np.ndarray | None, x: int, y: int, size: int,
                bounds: Bounds) -> DirtyRect | None:
    """브러시 영역을 완전 투명 (0,0,0,0) + 마스크 0으로."""
    box = _brush_box(x, y, size, clip_bounds(bounds, arr.shape))
    if box is None:
        return None
    y0, y1, x0, x1 = box
    view = arr[y0:y1, x0:x1]
    mview = mask[y0:y1, x0:x1] if mask is not None else None
    if not view.any() and (mview is None or not mview.any()):
        return None
    view[...] = 0
    if mview is not None:
        mview[...] = 0
    return box


def paint_line(arr, mask, p0, p1, size, rgb, index, bounds) -> DirtyRect | None:
    dirty = None
    for x, y in line_points(p0[0], p0[1], p1[0], p1[1]):
        dirty = _union(dirty, paint_stamp(arr, mask, x, y, size, rgb, index, bounds))
    return dirty


def erase_line(arr, mask, p0, p1, size, bounds) -> DirtyRect | None:
    dirty = None
    for x, y in line_points(p0[0], p0[1], p1[0], p1[1]):
        dirty = _union(dirty, erase_stamp(arr, mask, x, y, size, bounds))
    return dirty


def pick(arr: np.ndarray, mask: np.ndarray | None, x: int, y: int) -> tuple[tuple[int, int, int, int], int | None]:
    """스포이트: (RGBA, 마스크 인덱스 또는 None). 범위 밖이면 투명."""
    if not (0 <= y < arr.shape[0] and 0 <= x < arr.shape[1]):
        return (0, 0, 0, 0), None
    r, g, b, a = (int(v) for v in arr[y, x])
    idx = int(mask[y, x]) if mask is not None else None
    return (r, g, b, a), idx


# --------------------------------------------------------------------------- #
#  실행 취소 기록 (RGBA + 마스크)                                              #
# --------------------------------------------------------------------------- #
class PixelRecorder:
    """작업 전 상태를 기억해 두고, 끝나면 바뀐 사각형만 ``Edit``로 만든다 (획 하나 = 편집 하나).

    target은 ``arr``/``mask``/``has_mask`` 속성을 가진 문서(SheetModel 또는 셀 편집 창의 작업 문서).
    """

    def __init__(self, target):
        self.target = target
        self._arr0 = target.arr.copy()
        self._mask0 = target.mask.copy() if target.has_mask else None
        self.dirty: DirtyRect | None = None

    def add(self, rect: DirtyRect | None) -> None:
        self.dirty = _union(self.dirty, rect)

    def finish(self, label: str) -> Edit | None:
        if self.dirty is None:
            return None
        y0, y1, x0, x1 = self.dirty
        before = self._arr0[y0:y1, x0:x1].copy()
        after = self.target.arr[y0:y1, x0:x1].copy()
        mb = ma = None
        if self._mask0 is not None:
            mb = self._mask0[y0:y1, x0:x1].copy()
            ma = self.target.mask[y0:y1, x0:x1].copy()
        if np.array_equal(before, after) and (mb is None or np.array_equal(mb, ma)):
            return None
        edit = Edit(label, y0, x0, before, after, mask_before=mb, mask_after=ma)
        if mb is not None:
            edit.has_mask_before = edit.has_mask_after = True
        return edit


# --------------------------------------------------------------------------- #
#  자유 선택 이동                                                              #
# --------------------------------------------------------------------------- #
def normalize_rect(p0: tuple[int, int], p1: tuple[int, int], shape: tuple[int, ...]) -> Bounds | None:
    """두 픽셀(양 끝 포함)로 만든 사각형 → (y0, y1, x0, x1) 끝 미포함, 시트 안으로 자름."""
    x0, x1 = sorted((p0[0], p1[0]))
    y0, y1 = sorted((p0[1], p1[1]))
    b = clip_bounds((y0, y1 + 1, x0, x1 + 1), shape)
    if b[0] >= b[1] or b[2] >= b[3]:
        return None
    return b


def make_move_edit(arr: np.ndarray, mask: np.ndarray | None, sel: Bounds, dx: int, dy: int,
                   label: str) -> tuple[Edit | None, Bounds]:
    """sel 사각형의 내용을 (dx, dy)만큼 옮기는 Edit(아직 적용 안 됨)와 옮긴 뒤의 선택 사각형.

    시트 밖으로 나간 부분은 버린다. 바뀐 게 없으면 Edit는 None.
    """
    H, W = arr.shape[:2]
    y0, y1, x0, x1 = sel
    if (dx == 0 and dy == 0) or y0 >= y1 or x0 >= x1:
        return None, sel
    block = arr[y0:y1, x0:x1].copy()
    mblock = mask[y0:y1, x0:x1].copy() if mask is not None else None
    # 목적지 (시트 안으로 자른 부분)
    ny0, ny1, nx0, nx1 = y0 + dy, y1 + dy, x0 + dx, x1 + dx
    cy0, cy1, cx0, cx1 = max(0, ny0), min(H, ny1), max(0, nx0), min(W, nx1)
    new_sel = (cy0, cy1, cx0, cx1) if cy0 < cy1 and cx0 < cx1 else sel
    # 영향 범위 = 원래 자리 ∪ 목적지
    uy0, uy1 = min(y0, cy0) if cy0 < cy1 else y0, max(y1, cy1) if cy0 < cy1 else y1
    ux0, ux1 = min(x0, cx0) if cx0 < cx1 else x0, max(x1, cx1) if cx0 < cx1 else x1
    region = arr[uy0:uy1, ux0:ux1].copy()
    mregion = mask[uy0:uy1, ux0:ux1].copy() if mask is not None else None
    # 원래 자리 비우기
    region[y0 - uy0:y1 - uy0, x0 - ux0:x1 - ux0] = 0
    if mregion is not None:
        mregion[y0 - uy0:y1 - uy0, x0 - ux0:x1 - ux0] = 0
    # 불투명 픽셀만 목적지에 덮기
    if cy0 < cy1 and cx0 < cx1:
        sb = block[cy0 - ny0:cy1 - ny0, cx0 - nx0:cx1 - nx0]
        opaque = sb[..., 3] > 0
        dst = region[cy0 - uy0:cy1 - uy0, cx0 - ux0:cx1 - ux0]
        dst[opaque] = sb[opaque]
        if mregion is not None:
            mdst = mregion[cy0 - uy0:cy1 - uy0, cx0 - ux0:cx1 - ux0]
            mdst[opaque] = mblock[cy0 - ny0:cy1 - ny0, cx0 - nx0:cx1 - nx0][opaque]
    before = arr[uy0:uy1, ux0:ux1]
    if np.array_equal(before, region) and (mask is None or np.array_equal(mask[uy0:uy1, ux0:ux1], mregion)):
        return None, new_sel
    edit = Edit(label, uy0, ux0, before.copy(), region)
    if mask is not None:
        edit.mask_before = mask[uy0:uy1, ux0:ux1].copy()
        edit.mask_after = mregion
    return edit, new_sel
