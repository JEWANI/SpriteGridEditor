"""
cell_ops.py
-------------
셀 단위 편집의 **순수 함수** 모음 (Qt 의존 없음 → 단위 테스트 가능). 기획서 §4.2.

- 복사/붙여넣기/지우기/미러는 모두 정수 좌표 + NumPy 슬라이싱/뒤집기만 쓴다 (보간 없음 = Nearest).
- 다중 셀 블록의 미러는 **셀마다 따로 좌우 반전**한다 — 셀 순서(프레임 순서)는 그대로 두고
  각 프레임 그림만 뒤집는다 (걷기 한 행을 통째로 반대 방향에 미러하는 작업용).
- 블록 배열 크기는 (rows*cell_h, cols*cell_w, 4).
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
    y0, y1, x0, x1 = rect_pixels(grid, rect)
    return arr[y0:y1, x0:x1].copy()


def _per_cell(block: np.ndarray, grid: GridSpec, flip_axis: int) -> np.ndarray:
    """블록을 셀 단위로 나눠 각 셀 안에서만 뒤집는다. flip_axis: 1=좌우(열 방향), 0=상하."""
    h, w = block.shape[:2]
    ch, cw = grid.cell_h, grid.cell_w
    if h % ch or w % cw:
        raise ValueError("블록 크기가 셀 크기의 배수가 아닙니다")
    rows, cols = h // ch, w // cw
    cells = block.reshape(rows, ch, cols, cw, 4)
    if flip_axis == 1:
        cells = cells[:, :, :, ::-1, :]
    else:
        cells = cells[:, ::-1, :, :, :]
    return np.ascontiguousarray(cells.reshape(h, w, 4))


def mirror_block(block: np.ndarray, grid: GridSpec) -> np.ndarray:
    """좌우 반전 (Mirror / Flip Horizontal) — 셀마다."""
    return _per_cell(block, grid, 1)


def flip_block_vertical(block: np.ndarray, grid: GridSpec) -> np.ndarray:
    """상하 반전 — 셀마다."""
    return _per_cell(block, grid, 0)


def make_paste_edit(arr: np.ndarray, grid: GridSpec, anchor: tuple[int, int],
                    block: np.ndarray, label: str) -> Edit:
    """block을 anchor(행, 열) 셀 위치부터 덮어쓰는 Edit (투명 픽셀도 그대로 덮어씀)."""
    r, c = anchor
    return make_edit(arr, r * grid.cell_h, c * grid.cell_w, block, label)


def make_clear_edit(arr: np.ndarray, grid: GridSpec, rect: CellRect, label: str) -> Edit:
    """선택 셀의 RGBA를 (0,0,0,0)으로 비우는 Edit."""
    y0, y1, x0, x1 = rect_pixels(grid, rect)
    return make_edit(arr, y0, x0, np.zeros((y1 - y0, x1 - x0, 4), dtype=np.uint8), label)
