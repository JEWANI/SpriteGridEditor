"""
pair_mode.py
--------------
F/M 쌍 작업 모드 (기획서 §4.5-1, 3단계). **Qt 의존 없음.**

한 벌의 옷을 여성(F)·남성(M) 두 체형으로 이어서 만든다. 자동 변환이 아니라 **반대 체형 Body 위에서 위치를
다시 맞추는 작업**을 돕는다: 현재 시트·마스크를 반대 체형 새 문서로 복제하고, 원본은 읽기 전용 참조로 남긴다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import numpy as np

from sheet_model import GENDER_FEMALE, GENDER_MALE, GridSpec

VIEW_SIDE = "side"  # 나란히 (기본)
VIEW_OVERLAY = "overlay"  # 겹쳐보기 (편집 시트 위에 반투명)
VIEWS = (VIEW_SIDE, VIEW_OVERLAY)

_GENDER_TAIL = re.compile(r"^(?P<head>.*\d)(?P<g>[FM])$")


def other_gender(gender: str) -> str | None:
    """F↔M. 체형 공용(C)·알 수 없는 값은 None (쌍 작업 불가)."""
    if gender == GENDER_FEMALE:
        return GENDER_MALE
    if gender == GENDER_MALE:
        return GENDER_FEMALE
    return None


def counterpart_name(stem: str, new_gender: str) -> str:
    """파일명(확장자·`_M` 제외) 끝의 체형 문자를 바꾼다. `H1WK_P001F` → `H1WK_P001M`.

    체형 문자가 없으면 끝에 붙인다 (`H1WK_P001` → `H1WK_P001M`).
    """
    m = _GENDER_TAIL.match(stem)
    if m:
        return m.group("head") + new_gender
    return stem + new_gender


def can_start(gender: str) -> bool:
    return other_gender(gender) is not None


@dataclass
class PairReference:
    """쌍 작업의 참조 시트 (읽기 전용 사본). 시작 시점의 원본 체형 시트·마스크."""

    arr: np.ndarray  # (H, W, 4) RGBA 사본
    mask: np.ndarray  # (H, W) 인덱스 사본
    has_mask: bool
    grid: GridSpec
    name: str  # 확장자 제외 이름
    gender: str  # 원본 체형 (F/M)

    @property
    def display_name(self) -> str:
        return f"{self.name}.png"
