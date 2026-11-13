"""
autosave.py
-------------
자동 저장 / 크래시 복구 (기획서 §4.5-5). Qt 의존 없음.

- 위치: ``<base_dir>/autosave/`` (config.json과 같은 위치 규칙).
- 세션마다 ``session_<pid>.lock`` 을 만들고, 정상 종료 시 지운다. 시작할 때 **프로세스가 이미 죽은
  pid의 lock + 스냅샷**이 남아 있으면 비정상 종료로 보고 복구를 제안한다. (pid별로 나눠서
  두 개를 동시에 실행해도 서로의 자동 저장본을 크래시로 오인하지 않는다.)
- 스냅샷 = ``autosave_<pid>_<시각>.png`` (RGBA, 알파 그대로) + ``.json`` (그리드·체형·배경 설정 등)
  + 마스크가 있으면 ``…_mask.png`` (8비트, 인덱스 0~4 그대로).
- 자동 저장본은 원본 PNG를 덮어쓰지 않는다 (내보내기와 별개).
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass

import numpy as np
from PIL import Image

from config_manager import base_dir


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        handle = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return False
        ctypes.windll.kernel32.CloseHandle(handle)
        return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


@dataclass
class Snapshot:
    png_path: str
    json_path: str
    pid: int
    stamp: str
    meta: dict

    @property
    def mask_path(self) -> str:
        return self.png_path[:-4] + MASK_SUFFIX


MASK_SUFFIX = "_mask.png"


class AutoSaver:
    def __init__(self, folder: str | None = None, pid: int | None = None):
        self.folder = folder or os.path.join(base_dir(), "autosave")
        self.pid = pid if pid is not None else os.getpid()
        self.lock_path = os.path.join(self.folder, f"session_{self.pid}.lock")

    # -- 세션 ---------------------------------------------------------------
    def start_session(self) -> list[Snapshot]:
        """lock 생성 + 죽은 세션의 복구 후보(최신순)를 반환."""
        try:
            os.makedirs(self.folder, exist_ok=True)
        except OSError:
            return []
        candidates = [s for s in self.list_snapshots() if s.pid != self.pid and not _pid_alive(s.pid)]
        self._cleanup_dead_locks()
        try:
            with open(self.lock_path, "w", encoding="utf-8") as f:
                f.write(str(self.pid))
        except OSError:
            pass
        return candidates

    def _cleanup_dead_locks(self) -> None:
        for name in self._listdir():
            if name.startswith("session_") and name.endswith(".lock"):
                try:
                    pid = int(name[len("session_"):-len(".lock")])
                except ValueError:
                    continue
                if pid != self.pid and not _pid_alive(pid):
                    self._remove(os.path.join(self.folder, name))

    def end_session(self) -> None:
        """정상 종료: 이 세션의 스냅샷과 lock 삭제."""
        self.clear()
        self._remove(self.lock_path)

    # -- 스냅샷 -------------------------------------------------------------
    def save(self, arr: np.ndarray, meta: dict, keep: int, mask: np.ndarray | None = None) -> Snapshot | None:
        try:
            os.makedirs(self.folder, exist_ok=True)
            stamp = time.strftime("%Y%m%d_%H%M%S")
            base = os.path.join(self.folder, f"autosave_{self.pid}_{stamp}")
            # 같은 초에 두 번 저장되는 경우 덮어쓰기 방지
            n = 1
            while os.path.exists(base + ".png"):
                n += 1
                base = os.path.join(self.folder, f"autosave_{self.pid}_{stamp}_{n}")
            Image.fromarray(np.ascontiguousarray(arr), "RGBA").save(base + ".png", format="PNG")
            if mask is not None:
                Image.fromarray(np.ascontiguousarray(mask, dtype=np.uint8), "L").save(
                    base + MASK_SUFFIX, format="PNG")
            with open(base + ".json", "w", encoding="utf-8") as f:
                json.dump(meta, f, ensure_ascii=False, indent=2)
        except OSError:
            return None
        self.prune(keep)
        return Snapshot(base + ".png", base + ".json", self.pid, os.path.basename(base), dict(meta))

    def list_snapshots(self) -> list[Snapshot]:
        result: list[Snapshot] = []
        for name in self._listdir():
            if not (name.startswith("autosave_") and name.endswith(".png")) or name.endswith(MASK_SUFFIX):
                continue
            stem = name[:-4]
            parts = stem.split("_")  # autosave, pid, date, time[, n]
            try:
                pid = int(parts[1])
            except (IndexError, ValueError):
                continue
            png = os.path.join(self.folder, name)
            js = os.path.join(self.folder, stem + ".json")
            meta: dict = {}
            try:
                with open(js, "r", encoding="utf-8") as f:
                    meta = json.load(f)
            except (OSError, json.JSONDecodeError):
                pass
            result.append(Snapshot(png, js, pid, stem, meta))
        result.sort(key=lambda s: (os.path.getmtime(s.png_path), s.stamp), reverse=True)
        return result

    def load(self, snap: Snapshot) -> np.ndarray:
        with Image.open(snap.png_path) as im:
            return np.array(im.convert("RGBA"), dtype=np.uint8)

    def load_mask(self, snap: Snapshot) -> np.ndarray | None:
        """스냅샷에 마스크가 있으면 인덱스 마스크 (H, W) uint8, 없으면 None."""
        if not os.path.isfile(snap.mask_path):
            return None
        with Image.open(snap.mask_path) as im:
            return np.array(im.convert("L"), dtype=np.uint8)

    def prune(self, keep: int) -> None:
        mine = [s for s in self.list_snapshots() if s.pid == self.pid]
        for old in mine[max(1, keep):]:
            self.delete(old)

    def delete(self, snap: Snapshot) -> None:
        self._remove(snap.png_path)
        self._remove(snap.json_path)
        self._remove(snap.mask_path)

    def clear(self) -> None:
        """이 세션의 스냅샷 전부 삭제 (정상 저장 / 정상 종료 / 복구 무시)."""
        for s in self.list_snapshots():
            if s.pid == self.pid:
                self.delete(s)

    def discard_candidates(self, candidates: list[Snapshot]) -> None:
        for s in candidates:
            self.delete(s)

    # -- util ---------------------------------------------------------------
    def _listdir(self) -> list[str]:
        try:
            return os.listdir(self.folder)
        except OSError:
            return []

    @staticmethod
    def _remove(path: str) -> None:
        try:
            os.remove(path)
        except OSError:
            pass
