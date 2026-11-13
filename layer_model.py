"""
layer_model.py
----------------
화면에 겹쳐 보이는 **참조(읽기 전용) 레이어** 모델. Qt에 의존하지 않는다.

- 편집 레이어 = 지금 열어 둔 시트 1장 (클릭·붙여넣기·저장의 대상). 항상 정확히 1개.
- 참조 레이어 = 위치 확인용 그림 (Base Body, 사용자가 추가한 PNG). 편집·저장되지 않는다.
  시트 위(above) 또는 아래(below)에 겹칠 수 있고, 눈 아이콘(visible)·투명도(opacity)를 레이어별로 갖는다.
- 참조 레이어는 Base Body 1개 + 추가 PNG 최대 5개 = **최대 6개** (MAX_REFERENCE_LAYERS).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

MAX_REFERENCE_LAYERS = 6  # Base Body 포함
MAX_EXTRA_LAYERS = MAX_REFERENCE_LAYERS - 1

KIND_BODY = "body"
KIND_EXTRA = "extra"


@dataclass
class RefLayer:
    name: str
    image: Any = None  # QImage (UI 쪽에서 채움)
    kind: str = KIND_EXTRA
    path: str = ""
    visible: bool = True
    opacity: int = 60  # 0~100
    above: bool = False  # True: 시트 위 / False: 시트 아래
    uid: int = field(default=0)

    def clamp(self) -> None:
        self.opacity = max(0, min(100, int(self.opacity)))


def can_add_extra(extra_count: int) -> bool:
    return extra_count < MAX_EXTRA_LAYERS


def stack_order(refs: list[RefLayer]) -> tuple[list[RefLayer], list[RefLayer]]:
    """(아래 레이어들, 위 레이어들) — 각각 그리는 순서(먼저 그린 것이 더 아래)."""
    below = [r for r in refs if not r.above]
    above = [r for r in refs if r.above]
    return below, above
