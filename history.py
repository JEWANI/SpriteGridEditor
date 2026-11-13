"""
history.py
-----------
Undo/Redo 스택. 모든 편집은 **영역(사각형) 스냅샷**으로 기록한다 (기획서 §4.2).

한 번의 편집 = ``Edit`` 하나 = 바뀐 픽셀 사각형의 이전/이후 블록. 시트 전체를 복사하지 않아
H2(1536×1280)에서도 가볍다. 최소 50단계 보장(기본 100단계, 메모리 상한 256MB).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

MAX_STEPS = 100
MAX_BYTES = 256 * 1024 * 1024


@dataclass
class Edit:
    label: str  # 상태바에 보일 작업 이름 (i18n 키)
    y0: int
    x0: int
    before: np.ndarray  # (h, w, 4)
    after: np.ndarray

    @property
    def nbytes(self) -> int:
        return int(self.before.nbytes + self.after.nbytes)

    def _write(self, arr: np.ndarray, block: np.ndarray) -> None:
        h, w = block.shape[:2]
        arr[self.y0:self.y0 + h, self.x0:self.x0 + w] = block

    def apply(self, arr: np.ndarray) -> None:
        self._write(arr, self.after)

    def revert(self, arr: np.ndarray) -> None:
        self._write(arr, self.before)


def make_edit(arr: np.ndarray, y0: int, x0: int, new_block: np.ndarray, label: str) -> Edit:
    """arr의 (y0,x0) 위치 영역을 new_block으로 바꾸는 Edit을 만든다 (arr는 아직 안 바뀜)."""
    h, w = new_block.shape[:2]
    return Edit(label, y0, x0, arr[y0:y0 + h, x0:x0 + w].copy(), np.array(new_block, copy=True))


class History:
    def __init__(self, max_steps: int = MAX_STEPS, max_bytes: int = MAX_BYTES):
        self.max_steps = max_steps
        self.max_bytes = max_bytes
        self._edits: list[Edit] = []
        self._pos = 0  # 적용된 편집 수
        self._saved_pos: int | None = 0  # 저장 시점의 pos (None = 되돌아올 수 없음)
        self.revision = 0  # 변경될 때마다 증가 (자동 저장 변경 감지용)

    # -- 조회 -------------------------------------------------------------
    @property
    def can_undo(self) -> bool:
        return self._pos > 0

    @property
    def can_redo(self) -> bool:
        return self._pos < len(self._edits)

    def undo_label(self) -> str | None:
        return self._edits[self._pos - 1].label if self.can_undo else None

    def redo_label(self) -> str | None:
        return self._edits[self._pos].label if self.can_redo else None

    def is_clean(self) -> bool:
        return self._saved_pos is not None and self._saved_pos == self._pos

    def mark_saved(self) -> None:
        self._saved_pos = self._pos

    # -- 변경 -------------------------------------------------------------
    def do(self, arr: np.ndarray, edit: Edit) -> None:
        """edit을 arr에 적용하고 기록한다 (이후의 redo 기록은 버린다)."""
        edit.apply(arr)
        if self._saved_pos is not None and self._saved_pos > self._pos:
            self._saved_pos = None  # 저장 시점이 redo 쪽에 있었는데 버려짐
        del self._edits[self._pos:]
        self._edits.append(edit)
        self._pos += 1
        self._trim()
        self.revision += 1

    def undo(self, arr: np.ndarray) -> Edit | None:
        if not self.can_undo:
            return None
        self._pos -= 1
        edit = self._edits[self._pos]
        edit.revert(arr)
        self.revision += 1
        return edit

    def redo(self, arr: np.ndarray) -> Edit | None:
        if not self.can_redo:
            return None
        edit = self._edits[self._pos]
        edit.apply(arr)
        self._pos += 1
        self.revision += 1
        return edit

    def _trim(self) -> None:
        def total() -> int:
            return sum(e.nbytes for e in self._edits)

        while len(self._edits) > 1 and (len(self._edits) > self.max_steps or total() > self.max_bytes):
            del self._edits[0]
            self._pos -= 1
            if self._saved_pos is not None:
                self._saved_pos -= 1
                if self._saved_pos < 0:
                    self._saved_pos = None
