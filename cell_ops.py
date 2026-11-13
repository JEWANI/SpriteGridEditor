"""
cell_ops.py
-------------
셀 단위 편집의 **순수 함수** 모음 (Qt 의존 없음 → 단위 테스트 가능). 기획서 §4.2.

- 복사/붙여넣기/지우기/미러는 모두 정수 좌표 + NumPy 슬라이싱/뒤집기만 쓴다 (보간 없음 = Nearest).
- 다중 셀 블록의 미러는 **셀마다 따로 좌우 반전**한다 — 셀 순서(프레임 순서)는 그대로 두고
  각 프레임 그림만 뒤집는다 (걷기 한 행을 통째로 반대 방향에 미러하는 작업용).
- 블록 배열 크기는 (rows*cell_h, cols*cell_w, 4). 마스크 블록은 (rows*cell_h, cols*cell_w).
- 2단계: 시트에 마스크가 있으면 같은 편집이 마스크에도 똑같이 적용된다(``mask`` 인자).
  Nudge(셀 안에서 내용 이동)도 여기서 만든다.
"""

from __future__ import annotations

import numpy as np

from history import Edit, make_edit
from sheet_model import CellRect, GridSpec


def rect_pixels(grid: GridSpec, rect: CellRect) -> tuple[int, int, int, int]:
    """셀 사각형 → 픽셀 (y0, y1, x0, x1) (끝 미포함)."""
    return (rect.r0 * grid.cell_h, (rect.r1 + 1) * grid.cell_h,
            rect.c0 * grid.cell_w, (rect.c1 + 1) * grid.cell_w)


def rect_fits(grid: GridSpec, r: int, c: int, shape: tuple[int, int]) -> bool:
    """(r, c)를 왼쪽 위로 shape(행,열) 크기의 영역이 그리드 안에 들어가는가."""
    rows, cols = shape
    return r >= 0 and c >= 0 and r + rows <= grid.rows and c + cols <= grid.cols


def copy_cells(arr: np.ndarray, grid: GridSpec, rect: CellRect) -> np.ndarray:
    """RGBA(또는 2차원 마스크) 배열에서 셀 사각형 영역을 잘라 사본으로 돌려준다."""
    y0, y1, x0, x1 = rect_pixels(grid, rect)
    return arr[y0:y1, x0:x1].copy()


def _cells_view(block: np.ndarray, grid: GridSpec) -> tuple[np.ndarray, int, int]:
    """블록 → (rows, ch, cols, cw, ...) 모양. 마지막 축(채널)은 있으면 유지."""
    h, w = block.shape[:2]
    ch, cw = grid.cell_h, grid.cell_w
    if h % ch or w % cw:
        raise ValueError("블록 크기가 셀 크기의 배수가 아닙니다")
    rows, cols = h // ch, w // cw
    return block.reshape((rows, ch, cols, cw) + block.shape[2:]), h, w


def _per_cell(block: np.ndarray, grid: GridSpec, flip_axis: int) -> np.ndarray:
    """블록을 셀 단위로 나눠 각 셀 안에서만 뒤집는다. flip_axis: 1=좌우(열 방향), 0=상하."""
    cells, h, w = _cells_view(block, grid)
    cells = cells[:, :, :, ::-1] if flip_axis == 1 else cells[:, ::-1]
    return np.ascontiguousarray(cells.reshape((h, w) + block.shape[2:]))


def mirror_block(block: np.ndarray, grid: GridSpec) -> np.ndarray:
    """좌우 반전 (Mirror / Flip Horizontal) — 셀마다."""
    return _per_cell(block, grid, 1)


def flip_block_vertical(block: np.ndarray, grid: GridSpec) -> np.ndarray:
    """상하 반전 — 셀마다."""
    return _per_cell(block, grid, 0)


def nudge_block(block: np.ndarray, grid: GridSpec, dx: int, dy: int) -> np.ndarray:
    """Nudge: 각 셀 안에서 내용을 (dx, dy) 픽셀 이동. 셀 밖으로 나간 픽셀은 버리고 빈 곳은 0(투명)."""
    cells, h, w = _cells_view(block, grid)
    out = np.zeros_like(cells)
    ch, cw = grid.cell_h, grid.cell_w
    sy0, dy0 = max(0, -dy), max(0, dy)
    sx0, dx0 = max(0, -dx), max(0, dx)
    hh, ww = ch - abs(dy), cw - abs(dx)
    if hh > 0 and ww > 0:
        out[:, dy0:dy0 + hh, :, dx0:dx0 + ww] = cells[:, sy0:sy0 + hh, :, sx0:sx0 + ww]
    return np.ascontiguousarray(out.reshape((h, w) + block.shape[2:]))


def make_paste_edit(arr: np.ndarray, grid: GridSpec, anchor: tuple[int, int],
                    block: np.ndarray, label: str, mask: np.ndarray | None = None,
                    mask_block: np.ndarray | None = None) -> Edit:
    """block을 anchor(행, 열) 셀 위치부터 덮어쓰는 Edit (투명 픽셀도 그대로 덮어씀).

    mask(문서 마스크)를 주면 마스크도 바꾼다. mask_block이 없으면(마스크 없는 곳에서 복사) 0으로 채운다.
    알파가 0인 픽셀의 인덱스는 항상 0 (알파 보호).
    """
    r, c = anchor
    if mask is not None:
        if mask_block is None:
            mask_block = np.zeros(block.shape[:2], dtype=np.uint8)
        mask_block = np.where(block[..., 3] > 0, mask_block, 0).astype(np.uint8)
    return make_edit(arr, r * grid.cell_h, c * grid.cell_w, block, label, mask, mask_block)


def make_clear_edit(arr: np.ndarray, grid: GridSpec, rect: CellRect, label: str,
                    mask: np.ndarray | None = None) -> Edit:
    """선택 셀의 RGBA를 (0,0,0,0)으로 비우는 Edit (마스크가 있으면 인덱스도 0)."""
    y0, y1, x0, x1 = rect_pixels(grid, rect)
    zeros_mask = np.zeros((y1 - y0, x1 - x0), dtype=np.uint8) if mask is not None else None
    return make_edit(arr, y0, x0, np.zeros((y1 - y0, x1 - x0, 4), dtype=np.uint8), label, mask, zeros_mask)


def make_nudge_edit(arr: np.ndarray, grid: GridSpec, rect: CellRect, dx: int, dy: int, label: str,
                    mask: np.ndarray | None = None) -> Edit:
    """선택 셀들의 내용을 각 셀 안에서 (dx, dy) 이동하는 Edit (마스크도 같이)."""
    y0, _y1, x0, _x1 = rect_pixels(grid, rect)
    block = nudge_block(copy_cells(arr, grid, rect), grid, dx, dy)
    mblock = nudge_block(copy_cells(mask, grid, rect), grid, dx, dy) if mask is not None else None
    return make_edit(arr, y0, x0, block, label, mask, mblock)
