"""
main.py
--------
SpriteGrid Editor 진입점. QApplication을 만들고 MainWindow를 띄운다.
테마(QSS)는 config.json에 저장된 사용자 선택을 따라야 하므로 MainWindow가 ConfigManager를 읽어
스스로 적용한다 (MainWindow._apply_theme 참고).
"""

from __future__ import annotations

import os
import sys

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from app_info import APP_NAME
from config_manager import resource_path
from main_window import MainWindow


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)

    # icon.ico 같은 '번들에 포함된 읽기 전용' 리소스만 resource_path로 읽는다.
    # config.json / autosave / output 은 config_manager.base_dir() 기준 (sys._MEIPASS 사용 금지).
    icon_path = resource_path("icon.ico")
    if os.path.exists(icon_path):
        app.setWindowIcon(QIcon(icon_path))

    window = MainWindow()
    if os.path.exists(icon_path):
        window.setWindowIcon(QIcon(icon_path))
    window.show()

    # 명령행으로 PNG 경로를 넘기면(탐색기에서 '연결 프로그램') 바로 연다
    if len(sys.argv) > 1 and os.path.isfile(sys.argv[1]):
        window.open_file(sys.argv[1])

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
