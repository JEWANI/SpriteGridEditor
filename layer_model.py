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

# CHR-ART.5 §7 합성 순서: Body 0 → Eyes +1 → Outfit +2 → Hair +3 → Helmet +4 → Weapon +5
PARTS = ("Body", "Eyes", "Outfit", "Hair", "Helmet", "Weapon")
PART_Z = {name: i for i, name in enumerate(PARTS)}
KIND_PAIR = "pair"  # F/M 쌍 작업의 참조 시트 (겹쳐보기용, 개수 한도에 포함되지 않음)


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
    part: str = ""  # 파츠 참조 레이어면 Body/Eyes/Outfit/Hair/Helmet/Weapon (그 외 "")

    def clamp(self) -> None:
        self.opacity = max(0, min(100, int(self.opacity)))


def can_add_extra(extra_count: int) -> bool:
    return extra_count < MAX_EXTRA_LAYERS


def stack_order(refs: list[RefLayer]) -> tuple[list[RefLayer], list[RefLayer]]:
    """(아래 레이어들, 위 레이어들) — 각각 그리는 순서(먼저 그린 것이 더 아래)."""
    below = [r for r in refs if not r.above]
    above = [r for r in refs if r.above]
    return below, above


def part_is_above(part: str, edit_part: str) -> bool:
    """§7 합성 순서에서 part가 편집 중인 파츠(edit_part)보다 위에 놓이는가."""
    return PART_Z.get(part, 0) > PART_Z.get(edit_part, PART_Z["Outfit"])


def apply_part_order(refs: list[RefLayer], edit_part: str) -> None:
    """파츠 참조 레이어의 위/아래 위치를 편집 파츠 기준으로 다시 정한다 (사용자 PNG·Body는 건드리지 않음)."""
    for r in refs:
        if r.part and r.kind == KIND_EXTRA:
            r.above = part_is_above(r.part, edit_part)


def sort_for_paint(refs: list[RefLayer]) -> list[RefLayer]:
    """그리는 순서: 파츠 레이어는 §7 순서(아래→위), 파츠가 아닌 레이어는 원래 순서를 유지한다."""
    parts = sorted((r for r in refs if r.part), key=lambda r: PART_Z.get(r.part, 0))
    others = [r for r in refs if not r.part]
    return others[:1] + parts + others[1:] if others and others[0].kind == KIND_BODY else parts + others
