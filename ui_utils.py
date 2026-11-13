"""
ui_utils.py
------------
여러 다이얼로그가 같이 쓰는 작은 UI 헬퍼.
"""

from __future__ import annotations

import os

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QMessageBox, QWidget

from i18n import tr


def open_folder(path: str) -> bool:
    """탐색기에서 폴더 열기. 없으면 False."""
    if not path or not os.path.isdir(path):
        return False
    return QDesktopServices.openUrl(QUrl.fromLocalFile(path))


def ask_create_folder(parent: QWidget | None, path: str) -> bool:
    """없는 폴더 경로를 입력했을 때 생성 확인 → 생성. 성공하면 True (§4.4 추가 4)."""
    if os.path.isdir(path):
        return True
    answer = QMessageBox.question(parent, tr("dlg.create_folder.title"),
                                  tr("dlg.create_folder.msg", path=path),
                                  QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
    if answer != QMessageBox.Yes:
        return False
    try:
        os.makedirs(path, exist_ok=True)
    except OSError as e:
        QMessageBox.critical(parent, tr("dlg.error.title"), tr("dlg.create_folder.failed", error=str(e)))
        return False
    return True
