"""
mask_tools.py
---------------
마스크 경계 보정 브러시 (기획서 §4.3-3, 3단계). **Qt 의존 없음 → 단위 테스트 가능.**

- 도구: 연필 / 채우기(Flood Fill) / 사각형 채우기 / 영역 번호 교체 / 지우개(= 인덱스 0).
- 모든 좌표는 정수 픽셀(Nearest), 브러시는 정사각형(도트 작업용).
- **알파 보호**: 알파가 0인 곳에는 인덱스 1~4를 칠할 수 없다 (지우개는 0을 칠하므로 어디서나 가능).
- **범위(scope)**: 현재 셀(획이 시작된 셀) / 선택 셀(Source) / 시트 전체 — 범위 밖 픽셀은 건드리지 않는다.
- 실행 취소: ``MaskRecorder``가 작업 전 마스크를 기억했다가 바뀐 사각형만 ``history.Edit``로 만든다
  (획 하나·채우기 한 번·교체 한 번 = 편집 하나).
"""

from __future__ import annotations

from collections import deque

import numpy as np

from history import Edit
from sheet_model import CellRect, GridSpec

TOOL_PENCIL = "pencil"
TOOL_FILL = "fill"
TOOL_RECT = "rect"
TOOL_ERASER = "eraser"
TOOLS = (TOOL_PENCIL, TOOL_FILL, TOOL_RECT, TOOL_ERASER)

SCOPE_CELL = "cell"
SCOPE_SELECTION = "selection"
SCOPE_SHEET = "sheet"
SCOPES = (SCOPE_CELL, SCOPE_SELECTION, SCOPE_SHEET)

MAX_BRUSH = 15
MAX_INDEX = 4

Bounds = tuple[int, int, int, int]  # (y0, y1, x0, x1), 끝 미포함
DirtyRect = tuple[int, int, int, int]


def scope_bounds(grid: GridSpec, scope: str, cell: tuple[int, int] | None,
                 selection: CellRect | None) -> Bounds:
    """범위 → 픽셀 사각형. 셀/선택이 없으면 시트 전체로 취급하지 않고 가능한 가장 좁은 범위로 내려간다."""
    if scope == SCOPE_SHEET:
        return 0, grid.sheet_h, 0, grid.sheet_w
    if scope == SCOPE_SELECTION and selection is not None:
        return (selection.r0 * grid.cell_h, (selection.r1 + 1) * grid.cell_h,
                selection.c0 * grid.cell_w, (selection.c1 + 1) * grid.cell_w)
    if cell is None and selection is not None:
        cell = (selection.r0, selection.c0)
    if cell is None:
        return 0, grid.sheet_h, 0, grid.sheet_w
    r, c = cell
    return r * grid.cell_h, (r + 1) * grid.cell_h, c * grid.cell_w, (c + 1) * grid.cell_w


def _clip(bounds: Bounds, shape: tuple[int, int]) -> Bounds:
    y0, y1, x0, x1 = bounds
    return max(0, y0), min(shape[0], y1), max(0, x0), min(shape[1], x1)


def _union(a: DirtyRect | None, b: DirtyRect | None) -> DirtyRect | None:
    if a is None:
        return b
    if b is None:
        return a
    return min(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), max(a[3], b[3])


# --------------------------------------------------------------------------- #
#  연필 / 지우개                                                              #
# --------------------------------------------------------------------------- #
def stamp(mask: np.ndarray, alpha: np.ndarray, x: int, y: int, size: int, index: int,
          bounds: Bounds) -> DirtyRect | None:
    """(x, y)를 중심으로 size×size 정사각형 브러시를 찍는다. 바뀐 사각형(없으면 None)을 돌려준다."""
    size = max(1, min(MAX_BRUSH, int(size)))
    by0, by1, bx0, bx1 = _clip(bounds, mask.shape)
    half = size // 2
    y0, y1 = max(by0, y - half), min(by1, y - half + size)
    x0, x1 = max(bx0, x - half), min(bx1, x - half + size)
    if y0 >= y1 or x0 >= x1:
        return None
    view = mask[y0:y1, x0:x1]
    if index > 0:
        sel = alpha[y0:y1, x0:x1] > 0  # 알파 보호
        changed = sel & (view != index)
        if not changed.any():
            return None
        view[sel] = index
    else:
        if not (view != 0).any():
            return None
        view[...] = 0
    return y0, y1, x0, x1


def line_points(x0: int, y0: int, x1: int, y1: int) -> list[tuple[int, int]]:
    """Bresenham 정수 직선 (양 끝 포함)."""
    pts = []
    dx, dy = abs(x1 - x0), -abs(y1 - y0)
    sx, sy = (1 if x0 < x1 else -1), (1 if y0 < y1 else -1)
    err = dx + dy
    x, y = x0, y0
    while True:
        pts.append((x, y))
        if x == x1 and y == y1:
            break
        e2 = 2 * err
        if e2 >= dy:
            err += dy
            x += sx
        if e2 <= dx:
            err += dx
            y += sy
    return pts


def stroke_line(mask: np.ndarray, alpha: np.ndarray, p0: tuple[int, int], p1: tuple[int, int],
                size: int, index: int, bounds: Bounds) -> DirtyRect | None:
    """p0 → p1 사이를 브러시로 이어 칠한다 (마우스가 빨리 움직여도 끊기지 않게)."""
    dirty = None
    for x, y in line_points(p0[0], p0[1], p1[0], p1[1]):
        dirty = _union(dirty, stamp(mask, alpha, x, y, size, index, bounds))
    return dirty


# --------------------------------------------------------------------------- #
#  채우기 / 사각형 / 번호 교체                                                #
# --------------------------------------------------------------------------- #
def flood_fill(mask: np.ndarray, alpha: np.ndarray, x: int, y: int, index: int,
               bounds: Bounds) -> DirtyRect | None:
    """(x, y)와 이어진(4방향) 같은 번호 영역을 index로 채운다. 불투명 픽셀·범위 안만 대상.

    스캔라인 방식 — 칠한 픽셀은 이미 index이므로(target과 다름) 방문 표시를 따로 두지 않는다.
    """
    by0, by1, bx0, bx1 = _clip(bounds, mask.shape)
    if not (by0 <= y < by1 and bx0 <= x < bx1) or alpha[y, x] == 0:
        return None
    target = int(mask[y, x])
    if target == index:
        return None
    sub = mask[by0:by1, bx0:bx1]
    op = alpha[by0:by1, bx0:bx1] > 0
    h, w = sub.shape
    stack = deque([(y - by0, x - bx0)])
    dirty: DirtyRect | None = None
    while stack:
        cy, cx = stack.pop()
        if sub[cy, cx] != target or not op[cy, cx]:
            continue
        xl = cx
        while xl > 0 and sub[cy, xl - 1] == target and op[cy, xl - 1]:
            xl -= 1
        xr = cx
        while xr < w - 1 and sub[cy, xr + 1] == target and op[cy, xr + 1]:
            xr += 1
        sub[cy, xl:xr + 1] = index
        dirty = _union(dirty, (by0 + cy, by0 + cy + 1, bx0 + xl, bx0 + xr + 1))
        for ny in (cy - 1, cy + 1):
            if 0 <= ny < h:
                row = (sub[ny, xl:xr + 1] == target) & op[ny, xl:xr + 1]
                starts = np.flatnonzero(row & ~np.concatenate(([False], row[:-1])))
                for s in starts:
                    stack.append((ny, xl + int(s)))
    return dirty


def fill_rect(mask: np.ndarray, alpha: np.ndarray, p0: tuple[int, int], p1: tuple[int, int],
              index: int, bounds: Bounds) -> DirtyRect | None:
    """두 점을 모서리로 하는 사각형(양 끝 포함)을 index로 채운다. 알파 보호·범위 적용."""
    by0, by1, bx0, bx1 = _clip(bounds, mask.shape)
    x0, x1 = sorted((p0[0], p1[0]))
    y0, y1 = sorted((p0[1], p1[1]))
    x0, x1 = max(bx0, x0), min(bx1, x1 + 1)
    y0, y1 = max(by0, y0), min(by1, y1 + 1)
    if y0 >= y1 or x0 >= x1:
        return None
    view = mask[y0:y1, x0:x1]
    if index > 0:
        sel = alpha[y0:y1, x0:x1] > 0
        if not (sel & (view != index)).any():
            return None
        view[sel] = index
    else:
        if not (view != 0).any():
            return None
        view[...] = 0
    return y0, y1, x0, x1


def replace_index(mask: np.ndarray, alpha: np.ndarray, src: int, dst: int,
                  bounds: Bounds) -> tuple[DirtyRect | None, int]:
    """범위 안의 번호 src를 모두 dst로 바꾼다 (예: 2→3, 누락(0)→1). (바뀐 사각형, 바뀐 픽셀 수)."""
    src, dst = int(src), int(dst)
    if src == dst or not (0 <= src <= MAX_INDEX and 0 <= dst <= MAX_INDEX):
        return None, 0
    y0, y1, x0, x1 = _clip(bounds, mask.shape)
    if y0 >= y1 or x0 >= x1:
        return None, 0
    view = mask[y0:y1, x0:x1]
    sel = view == src
    if dst > 0:
        sel &= alpha[y0:y1, x0:x1] > 0  # 알파 보호 (src=0일 때 투명한 곳은 그대로)
    n = int(sel.sum())
    if n == 0:
        return None, 0
    view[sel] = dst
    ys, xs = np.nonzero(sel)
    return (y0 + int(ys.min()), y0 + int(ys.max()) + 1, x0 + int(xs.min()), x0 + int(xs.max()) + 1), n


# --------------------------------------------------------------------------- #
#  셀 간 마스크 복사                                                          #
# --------------------------------------------------------------------------- #
def paste_mask_block(mask: np.ndarray, alpha: np.ndarray, grid: GridSpec, anchor: tuple[int, int],
                     block: np.ndarray) -> DirtyRect | None:
    """마스크 블록을 anchor(행, 열) 셀 위치부터 덮어쓴다. 시트 RGBA는 건드리지 않고, 알파 0인 곳은 인덱스 0."""
    r, c = anchor
    y0, x0 = r * grid.cell_h, c * grid.cell_w
    h, w = block.shape
    y1, x1 = y0 + h, x0 + w
    if y0 < 0 or x0 < 0 or y1 > mask.shape[0] or x1 > mask.shape[1]:
        return None
    new = np.where(alpha[y0:y1, x0:x1] > 0, block, 0).astype(np.uint8)
    if np.array_equal(mask[y0:y1, x0:x1], new):
        return None
    mask[y0:y1, x0:x1] = new
    return y0, y1, x0, x1


# --------------------------------------------------------------------------- #
#  실행 취소 기록                                                              #
# --------------------------------------------------------------------------- #
class MaskRecorder:
    """작업 시작 시 마스크를 기억해 두고, 끝나면 바뀐 사각형만 Edit로 만든다.

    사용: ``rec = MaskRecorder(arr, mask)`` → 마스크를 직접 수정 → ``rec.add(dirty)`` 로 바뀐 영역을 알림 →
    ``edit = rec.finish(label)`` (바뀐 게 없으면 None) → ``history.do(model, edit)``.
    RGBA는 바뀌지 않으므로 Edit의 before/after 블록이 같다.
    """

    def __init__(self, arr: np.ndarray, mask: np.ndarray):
        self.arr = arr
        self.mask = mask
        self._base = mask.copy()
        self.dirty: DirtyRect | None = None

    def add(self, rect: DirtyRect | None) -> None:
        self.dirty = _union(self.dirty, rect)

    def finish(self, label: str) -> Edit | None:
        if self.dirty is None:
            return None
        y0, y1, x0, x1 = self.dirty
        before = self._base[y0:y1, x0:x1].copy()
        after = self.mask[y0:y1, x0:x1].copy()
        if np.array_equal(before, after):
            return None
        block = self.arr[y0:y1, x0:x1].copy()
        return Edit(label, y0, x0, block, block.copy(), mask_before=before, mask_after=after)
