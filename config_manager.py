"""
config_manager.py
-------------------
config.json 로드/저장. 위치 규칙은 기획서 §6.5 (PSBrushViewer v0.2.0 버그 교훈):

- 쓰기 가능한 파일(config.json, autosave/, output/)은 ``base_dir()`` 기준 —
  exe로 실행 중(sys.frozen)이면 **exe가 있는 폴더**, 소스 실행이면 이 파일이 있는 폴더.
- 읽기 전용 리소스(locales/, HELP_*.md, CHANGELOG.md, icon.ico)는 ``resource_path()`` —
  PyInstaller onefile이면 번들 임시 폴더(sys._MEIPASS).

config.json 스키마는 DEFAULT_CONFIG 참고. 누락된 키는 기본값으로 보강한다(구버전 호환).
개인 경로가 들어 있어 .gitignore로 버전관리에서 제외한다.
"""

from __future__ import annotations

import copy
import json
import os
import sys

CONFIG_FILE_NAME = "config.json"

# 유니티 프로젝트의 Base Body 폴더 (기획서 §4.1, 2026-11-13 확인). 환경설정에서 변경 가능.
DEFAULT_BODY_DIR = (
    r"D:\프로젝트\STELLAR PROFITS\Assets\Resources\Art\UnitSprites\Allies\Body"
)

RECENT_LIMIT = 10

DEFAULT_CONFIG = {
    # 일반
    "language": "ko",  # "ko" | "en"
    "app_theme": "dark",  # "dark" | "light"
    "window": {"width": 1500, "height": 920},
    # 저장 (§4.4)
    "save_dir": "",  # 비어 있으면 <base_dir>/output
    "recent_save_dirs": [],
    "ask_save_dir_each_time": False,
    "alpha_threshold_enabled": True,
    "alpha_threshold": 128,
    "transparent_rgb": "black",  # "black" (0,0,0) | "extend" (가장자리 색 확장)
    "unity_meta_copy": True,  # 4단계: Unity Assets 안에 새 PNG를 저장하면 같은 폴더 시트의 .meta를 복제
    # 열기
    "last_open_dir": "",
    "recent_files": [],
    # 이미지 가져오기 (크기·배경 조정 팝업)
    "import": {
        "skip_when_ready": True,  # 크기가 규격과 맞고 투명 배경이 있으면 팝업 없이 바로 연다
        "last_preset": "",  # 마지막으로 고른 시트 규격 id ("" = 비율이 가장 비슷한 규격)
        "size_mode": "auto",  # "auto"(비율 유지) | "custom"(가로·세로 직접)
        "resample": "nearest",  # nearest | box | lanczos
        "tolerance": 32,  # 배경색 허용 오차 (채널별 최대 차이)
        "scope": "edge",  # edge(가장자리와 이어진 부분만) | all(같은 색 전부)
        "fit_canvas": True,  # 시트 규격 크기로 여백 채우기/잘라내기
        "align": "center",  # center | top | bottom
    },
    # 시트 규격 (기본 H1·H2는 코드에 내장, 여기엔 사용자 프리셋만)
    "sheet_presets": [],
    # 참조 (Base Body)
    "body_dir": DEFAULT_BODY_DIR,
    # 보기
    "background_mode": "checker",  # checker | white | black | dots (구버전의 "body"는 body_visible 로 이전)
    "body_visible": False,  # Base Body 참조 레이어 표시
    "body_opacity": 60,  # 0~100
    "body_above": False,  # True: 시트 위에 겹침 / False: 시트 아래
    "reference_tint": True,  # 참조 레이어에 색조를 입혀 편집 레이어와 구분
    "zoom_percent": 200,  # 100~800
    "grid": {"show": True, "color": "#00e5ff", "width": 1},
    # 자동 저장 (§4.5-5)
    "autosave": {"enabled": True, "interval_min": 10, "keep": 2},
    # 2단계: 회색+마스크 변환 (§4.3)
    "convert": {"mode": "color", "regions": 2, "l_weight": 50, "hsplit_y": 0, "despeckle": True},
    "mask_view": {"mode": "off", "opacity": 60, "missing": False},  # off | overlay | only
    "outfit_preview": {"enabled": False, "colors": [], "gain": 1.0},  # 캔버스 색 미리보기 (회색 × 색)
    # 3단계: 마스크 경계 보정 / F·M 쌍 작업 / 파츠 참조
    "mask_edit": {"brush": 1, "scope": "cell"},  # scope: cell | selection | sheet
    "pair": {"view": "side", "overlay_opacity": 40},  # view: side(나란히) | overlay(겹쳐보기)
    # 4단계: 셀 편집 창 (§4.2 추가 8)
    "cell_edit": {"neighbors": True, "neighbor_opacity": 40, "zoom": 800, "brush": 1},
    "batch_convert": {"in_dir": "", "out_dir": "", "normalize": "auto"},  # 4단계: 폴더 일괄 변환 (normalize: auto|on|off)
    "allies_dir": "",  # 비어 있으면 Base Body 폴더의 상위(Allies) 폴더
    "mask_missing_warn_pct": 10,  # 저장 전 검증: 불투명 픽셀 중 인덱스 0 비율이 이 % 이상이면 경고
    # compare.slots / compare.squad / compare.with_squad / compare.layout 은 비교 창이 처음 저장할 때 생긴다
}


def base_dir() -> str:
    """config.json / autosave / output 을 둘 기준 폴더.

    - 소스로 실행(python main.py): 이 모듈 파일이 있는 프로젝트 폴더.
    - PyInstaller onefile exe로 실행: sys.executable(실제 .exe)이 있는 폴더.
      (frozen 상태의 __file__은 매 실행마다 지워지는 임시 폴더라 쓰면 안 됨)
    """
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def resource_path(relative_path: str) -> str:
    """번들에 포함된 읽기 전용 리소스 경로 (개발 환경 / PyInstaller 양쪽 동작)."""
    base_path = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base_path, relative_path)


def _config_path() -> str:
    return os.path.join(base_dir(), CONFIG_FILE_NAME)


def _merge_defaults(data: dict, defaults: dict) -> None:
    """data에 없는 키를 defaults로 보강 (중첩 dict 포함)."""
    for key, default_value in defaults.items():
        if key not in data:
            data[key] = copy.deepcopy(default_value)
        elif isinstance(default_value, dict) and isinstance(data[key], dict):
            _merge_defaults(data[key], default_value)


class ConfigManager:
    """config.json 하나를 감싸는 얇은 래퍼. 값을 바꿀 때마다 즉시 저장한다.

    키는 점 표기를 지원한다: ``cfg.get("autosave.interval_min")``.
    """

    def __init__(self, path: str | None = None):
        self.path = path or _config_path()
        self.save_error: str | None = None
        self.data: dict = self._load()

    # -- 로드/저장 -------------------------------------------------------
    def _load(self) -> dict:
        data = None
        if os.path.exists(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if not isinstance(data, dict):
                    data = None
            except (json.JSONDecodeError, OSError):
                data = None
        fresh = data is None
        if fresh:
            data = {}
        _merge_defaults(data, DEFAULT_CONFIG)
        if fresh:
            self._save(data)
        return data

    def _save(self, data: dict | None = None) -> None:
        try:
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(data if data is not None else self.data, f, ensure_ascii=False, indent=2)
            self.save_error = None
        except OSError as e:  # 읽기 전용 폴더 등 — 앱은 계속 동작
            self.save_error = str(e)

    # -- 일반 접근 -------------------------------------------------------
    def get(self, key: str, default=None):
        node = self.data
        for part in key.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def set(self, key: str, value) -> None:
        parts = key.split(".")
        node = self.data
        for part in parts[:-1]:
            nxt = node.get(part)
            if not isinstance(nxt, dict):
                nxt = {}
                node[part] = nxt
            node = nxt
        node[parts[-1]] = value
        self._save()

    def push_recent(self, key: str, value: str, limit: int = RECENT_LIMIT) -> None:
        """최근 목록 맨 앞에 value를 넣는다 (중복 제거, limit개 유지)."""
        value = os.path.normpath(value)
        items = [v for v in self.get(key, []) if os.path.normpath(v) != value]
        items.insert(0, value)
        self.set(key, items[:limit])

    def remove_recent(self, key: str, value: str) -> None:
        value = os.path.normpath(value)
        self.set(key, [v for v in self.get(key, []) if os.path.normpath(v) != value])

    # -- 자주 쓰는 값 ----------------------------------------------------
    def get_window_size(self) -> tuple[int, int]:
        w = self.get("window", {})
        return int(w.get("width", 1500)), int(w.get("height", 920))

    def set_window_size(self, width: int, height: int) -> None:
        self.set("window", {"width": int(width), "height": int(height)})
