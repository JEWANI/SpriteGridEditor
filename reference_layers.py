"""
reference_layers.py
---------------------
참조 이미지(Base Body 등) 탐색. 기획서 §4.1 / §4.5-2.

v0.1.0~0.2.0은 Base Body 한 장만 다뤘고, v0.3.0부터 다중 참조 파츠(Eyes/Outfit/Hair/Helmet/Weapon)를
``Allies/<파츠>/`` 폴더에서 탐색한다 (합성 순서는 CHR-ART.5 §7, layer_model.PART_Z).

Base Body 파일 규칙 (유니티 ``Assets/Resources/Art/UnitSprites/Allies/Body/``, 2026-11-13 확인):

    Body{F|M}[_T{2~5}]_{H1WK|H2}.png     (피부톤 T1 = 접미사 없음)

Base Body는 옷 같은 파츠를 올렸을 때 위치가 맞는지 보기 위한 **읽기 전용 참조 이미지**이며
내보내기에는 포함되지 않는다.
"""

from __future__ import annotations

import os
import re

from layer_model import PARTS
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


# --------------------------------------------------------------------------- #
#  다중 참조 파츠 (§4.5-2)
# --------------------------------------------------------------------------- #
# 파일명 접두 모션 (H1WK_P001F.png 의 H1WK). Body는 접미(BodyF_H1WK)라 별도 처리.
_OUTFIT_RE = re.compile(r"^(?:H1WK|H1ST|H2)_([UPGNSH])\d+[FM]?(?:_M)?$", re.IGNORECASE)
_GENDER_END_RE = re.compile(r"(?:\d|_)([FM])$", re.IGNORECASE)


def allies_dir_from(body_dir: str, allies_dir: str = "") -> str:
    """Allies 폴더. 환경설정에 지정이 없으면 Base Body 폴더(…/Allies/Body)의 상위 폴더."""
    if allies_dir:
        return allies_dir
    if not body_dir:
        return ""
    return os.path.dirname(os.path.normpath(body_dir))


def detect_part(stem: str) -> str:
    """편집 중인 시트 파일명에서 어떤 파츠인지 추정. 못 알아보면 Outfit(옷 작업이 가장 흔하다)."""
    low = stem.lower()
    for part in PARTS:
        if low.startswith(part.lower()):
            return part
    m = _OUTFIT_RE.match(stem)
    if m and m.group(1).upper() == "H":  # CHR-ART.5 §7: 헬멧은 종류 문자 H 제안
        return "Helmet"
    return "Outfit"


# 실제 규칙 (아트 작업 TODO - 유닛 U2): Hair = HairC##_H1WK (체형 문자 없음, C01~16 여 / C17~24 남),
# Eyes = Eyes{C01~C06|N###}[_E1~E7]_H1WK_{Open|Closed}, 옷 = H1WK_{종류}{번호}{F|M}(+_M), Body = Body{F|M}[_T#]_{모션}
_HAIR_NUM_RE = re.compile(r"^Hair[A-Za-z]?(\d+)", re.IGNORECASE)
HAIR_FEMALE_LAST = 16
HAIR_MALE_LAST = 24


def _stem_gender(stem: str, part: str = "") -> str | None:
    if part == "Hair":
        m = _HAIR_NUM_RE.match(stem)
        if m:
            n = int(m.group(1))
            if 1 <= n <= HAIR_FEMALE_LAST:
                return "F"
            if HAIR_FEMALE_LAST < n <= HAIR_MALE_LAST:
                return "M"
            return None
    s = re.sub(r"_M$", "", stem, flags=re.IGNORECASE) if stem.upper().endswith("_M") and len(stem) > 2 else stem
    m = _GENDER_END_RE.search(s)
    return m.group(1).upper() if m else None


def list_part_files(allies_dir: str, part: str, gender: str, motion: str) -> list[str]:
    """``Allies/<part>/`` 안에서 체형·모션에 맞는 PNG 파일명(정렬). 마스크(_M)·백업 파일은 제외.

    체형: 파일명 끝 F/M이 편집 시트 체형과 같거나, 체형 문자가 없는(공용) 파일. 체형 공용(C) 시트는 F 기준.
    모션: 비어 있지 않으면 파일명에 모션 문자열이 들어 있어야 한다 (대소문자 무시).
    """
    folder = os.path.join(allies_dir, part) if allies_dir else ""
    if not folder or not os.path.isdir(folder):
        return []
    want = gender if gender in ("F", "M") else GENDER_FEMALE
    names = []
    for fn in sorted(os.listdir(folder)):
        stem, ext = os.path.splitext(fn)
        if ext.lower() != ".png" or stem.upper().endswith("_M") or stem.lower().endswith("_backup"):
            continue
        if motion and motion.lower() not in stem.lower():
            continue
        g = _stem_gender(stem, part)
        if g is not None and g != want:
            continue
        names.append(fn)
    return names


def auto_part_file(allies_dir: str, part: str, gender: str, motion: str,
                   skin: str = "T1") -> str | None:
    """파츠 자동 선택: Body는 기존 규칙(피부톤 포함), 그 밖에는 후보가 하나뿐일 때만 (여럿이면 사용자가 고른다)."""
    if part == "Body":
        path, _ = find_body(os.path.join(allies_dir, "Body") if allies_dir else "", gender, skin, motion)
        return path
    names = list_part_files(allies_dir, part, gender, motion)
    pick = default_part_file(part, names, gender)
    return os.path.join(allies_dir, part, pick) if pick else None


def default_part_file(part: str, names: list[str], gender: str) -> str | None:
    """후보 중 참조로 띄울 기본 파일. 후보가 하나면 그것, 여럿이면 파츠별 기본값(없으면 None → 사용자가 고른다).

    - Hair: 여 HairC01 / 남 HairC17 (정비실 로더의 성별 기본 헤어)
    - Eyes: 첫 눈 모양 + 첫 눈색의 열림(Open) 시트 — 위치 확인용이라 어느 것이든 같다
    """
    if len(names) == 1:
        return names[0]
    low = {n.lower(): n for n in names}
    if part == "Hair":
        stem = "hairc17" if gender == "M" else "hairc01"
        return next((n for k, n in low.items() if k.startswith(stem)), None)
    if part == "Eyes":
        opens = [n for n in names if "_open" in n.lower()]
        for n in opens:
            if re.match(r"^Eyes[A-Za-z]01(_E1)?_", n, re.IGNORECASE):
                return n
        return opens[0] if opens else None
    return None
