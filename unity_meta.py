"""
unity_meta.py
---------------
Unity `.meta` 복제 (기획서 §4.4 / §9 4단계 — 기존 `gen_*` 스크립트 방식).

Unity 프로젝트의 `Assets/` 안에 **새 PNG**를 저장할 때, 같은 폴더에서 크기가 같은 시트의 `.meta`를
템플릿으로 복제해 임포트 설정(Point 필터·무압축·스프라이트 분할)을 그대로 물려준다.
Unity가 기본값(Bilinear·압축·통짜 스프라이트)으로 새 meta를 만드는 것을 막기 위한 것.

- guid: 프로젝트 규칙 ``md5('S_unit_' + 파일명(확장자 제외))`` (SCN-BLD.020.3 §7, CHR-ART.5 §3.2)
- 스프라이트 이름 ``{템플릿}_{n}`` → ``{새 이름}_{n}``, spriteID·internalID는 이름에서 결정적으로 다시 만든다
  (같은 파일을 다시 만들어도 값이 같음 → 참조가 깨지지 않음).
- 이미 `.meta`가 있으면 건드리지 않는다 (Unity가 guid를 유지하도록 덮어쓰기 저장은 그대로).
- 템플릿: 같은 폴더의 `*.png.meta` 중 마스크 여부(`_M`)가 같고 PNG 크기가 같은 것 (같은 접두사 우선).
  없으면 복제하지 않는다 (Unity가 기본 설정으로 만든다 — 마스크는 `OutfitMaskImporter`가 처리).
Qt 의존 없음.
"""

from __future__ import annotations

import glob
import hashlib
import os
import re

from PIL import Image

META_OK = "ok"
META_EXISTS = "exists"
META_NOT_UNITY = "not_unity"
META_NO_TEMPLATE = "no_template"
META_FAILED = "failed"


def find_assets_root(path: str) -> str | None:
    """path가 Unity 프로젝트의 Assets 폴더 안이면 그 Assets 폴더 경로 (옆에 ProjectSettings가 있어야 함)."""
    d = os.path.dirname(os.path.abspath(path))
    while True:
        if os.path.basename(d).lower() == "assets" and os.path.isdir(os.path.join(os.path.dirname(d), "ProjectSettings")):
            return d
        parent = os.path.dirname(d)
        if parent == d:
            return None
        d = parent


def meta_guid(stem: str) -> str:
    return hashlib.md5(("S_unit_" + stem).encode("utf-8")).hexdigest()


def _sprite_ids(guid: str, name: str) -> tuple[str, int]:
    h = hashlib.md5((guid + ":" + name).encode("utf-8")).digest()
    internal = int.from_bytes(h[:4], "little", signed=True) or 1
    return h.hex(), internal


def _is_mask(stem: str) -> bool:
    return stem.endswith("_M")


def png_size(path: str) -> tuple[int, int] | None:
    try:
        with Image.open(path) as im:
            return im.size
    except Exception:
        return None


def find_template(png_path: str, size: tuple[int, int]) -> str | None:
    """같은 폴더에서 템플릿 meta를 고른다. size = (가로, 세로)."""
    folder = os.path.dirname(os.path.abspath(png_path))
    stem = os.path.splitext(os.path.basename(png_path))[0]
    mask = _is_mask(stem)
    prefix = stem.split("_", 1)[0]
    best: tuple[int, str] | None = None
    for meta in sorted(glob.glob(os.path.join(folder, "*.png.meta"))):
        png = meta[:-5]
        t_stem = os.path.splitext(os.path.basename(png))[0]
        if t_stem == stem or _is_mask(t_stem) != mask or t_stem.endswith("_backup"):
            continue
        if not os.path.exists(png) or png_size(png) != tuple(size):
            continue
        # 같은 접두사 우선, 그다음 현행 규칙 이름(구 C·T·B 시트보다 U·P·G·N·S 시트)을 우선
        score = (0 if t_stem.split("_", 1)[0] == prefix else 2) + (0 if _RULE_RE.match(t_stem) else 1)
        if best is None or score < best[0]:
            best = (score, meta)
    return best[1] if best else None


_RULE_RE = re.compile(r"^[A-Za-z0-9]+_[UPGNS]\d{3}[FM]?(_M)?$")
_NAME_LINE = re.compile(r"^(\s+name: )(.+?)_(\d+)\s*$")
_TABLE_LINE = re.compile(r"^(\s+)(.+?)_(\d+): (-?\d+)\s*$")


def rewrite_meta(text: str, new_stem: str) -> str:
    """템플릿 meta 텍스트를 새 파일용으로 바꾼다 (guid·스프라이트 이름·ID)."""
    guid = meta_guid(new_stem)
    out: list[str] = []
    lines = text.splitlines()
    in_sprites = in_table = False
    pending_name: str | None = None
    for line in lines:
        stripped = line.strip()
        if line.startswith("guid: "):
            out.append(f"guid: {guid}")
            continue
        if stripped == "sprites:":
            in_sprites = True
        elif stripped == "nameFileIdTable:":
            in_table, in_sprites = True, False
            out.append(line)
            continue
        if in_table:
            m = _TABLE_LINE.match(line)
            if m:
                name = f"{new_stem}_{m.group(3)}"
                out.append(f"{m.group(1)}{name}: {_sprite_ids(guid, name)[1]}")
                continue
            in_table = False
        if in_sprites:
            m = _NAME_LINE.match(line)
            if m:
                pending_name = f"{new_stem}_{m.group(3)}"
                out.append(f"{m.group(1)}{pending_name}")
                continue
            if pending_name and stripped.startswith("spriteID:"):
                out.append(line[:line.index("spriteID:")] + "spriteID: " + _sprite_ids(guid, pending_name)[0])
                continue
            if pending_name and stripped.startswith("internalID:"):
                out.append(line[:line.index("internalID:")] + f"internalID: {_sprite_ids(guid, pending_name)[1]}")
                pending_name = None
                continue
            if stripped in ("outline: []", "physicsShape: []") and line.startswith("    ") and not line.startswith("     "):
                in_sprites = False  # 스프라이트 목록 끝 (spriteSheet 자체 필드)
        out.append(line)
    nl = "\r\n" if "\r\n" in text else "\n"
    return nl.join(out) + nl


def copy_meta_for(png_path: str, size: tuple[int, int]) -> tuple[str, str | None]:
    """png_path용 meta를 템플릿에서 만든다. (결과 코드, 쓴 meta 경로 또는 템플릿 이름)."""
    meta_path = png_path + ".meta"
    if os.path.exists(meta_path):
        return META_EXISTS, None
    if find_assets_root(png_path) is None:
        return META_NOT_UNITY, None
    template = find_template(png_path, size)
    if template is None:
        return META_NO_TEMPLATE, None
    try:
        with open(template, "r", encoding="utf-8", newline="") as f:
            text = f.read()
        stem = os.path.splitext(os.path.basename(png_path))[0]
        with open(meta_path, "w", encoding="utf-8", newline="") as f:
            f.write(rewrite_meta(text, stem))
    except OSError:
        return META_FAILED, None
    return META_OK, os.path.basename(template)
