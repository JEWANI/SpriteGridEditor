"""
reference_layers.py
---------------------
참조 이미지(Base Body 등) 탐색. 기획서 §4.1 / §4.5-2.

v0.1.0에서는 Base Body 한 장만 다룬다. (다중 참조 레이어 Eyes/Outfit/Hair/Helmet/Weapon 합성은 3단계.)

Base Body 파일 규칙 (유니티 ``Assets/Resources/Art/UnitSprites/Allies/Body/``, 2026-11-13 확인):

    Body{F|M}[_T{2~5}]_{H1WK|H2}.png     (피부톤 T1 = 접미사 없음)

Base Body는 옷 같은 파츠를 올렸을 때 위치가 맞는지 보기 위한 **읽기 전용 참조 이미지**이며
내보내기에는 포함되지 않는다.
"""

from __future__ import annotations

import os

from sheet_model import GENDER_COMMON, GENDER_FEMALE

SKIN_TONES = ("T1", "T2", "T3", "T4", "T5")


def body_filename(gender: str, skin: str, motion: str) -> str:
    """체형(F/M) + 피부톤 + 모션 → 파일명. 체형 공용(C)은 여성(F) Body를 쓴다."""
    g = gender if gender in ("F", "M") else GENDER_FEMALE
    tone = "" if skin in ("", "T1") else f"_{skin}"
    return f"Body{g}{tone}_{motion}.png"


def find_body(body_dir: str, gender: str, skin: str, motion: str) -> tuple[str | None, str]:
    """자동 매칭. 반환: (존재하면 전체 경로 / 없으면 None, 기대한 파일명).

    motion이 비어 있으면(사용자 정의 규격) 자동 매칭 불가 → (None, "").
    """
    if not motion:
        return None, ""
    name = body_filename(gender, skin, motion)
    if not body_dir:
        return None, name
    path = os.path.join(body_dir, name)
    return (path if os.path.isfile(path) else None), name


def uses_common_fallback(gender: str) -> bool:
    return gender == GENDER_COMMON
