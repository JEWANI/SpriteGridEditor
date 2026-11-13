"""
about_dialog.py
------------------
"SpriteGrid Editor 정보" 다이얼로그. 버전(+코드네임), 만든이, 블로그/GitHub 버튼,
사용 라이브러리 표기(PySide6/Qt는 LGPL — 단일 exe 배포 시 고지용).
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices, QIcon
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

from app_info import (
    APP_NAME, APP_VERSION, APP_VERSION_NAME, APP_VERSION_NAME_EN, AUTHOR, AUTHOR_BLOG_URL,
    DOC_ID, DOC_VERSION, GITHUB_URL,
)
from config_manager import resource_path
from i18n import current_language, tr


class AboutDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("dlg.about.title", app=APP_NAME))
        self.setFixedWidth(420)
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        icon_label = QLabel()
        icon_label.setAlignment(Qt.AlignCenter)
        pm = QIcon(resource_path("icon.ico")).pixmap(96, 96)
        if not pm.isNull():
            icon_label.setPixmap(pm)
            layout.addWidget(icon_label)

        name_label = QLabel(APP_NAME)
        name_font = name_label.font()
        name_font.setPointSize(name_font.pointSize() + 6)
        name_font.setBold(True)
        name_label.setFont(name_font)
        name_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(name_label)

        codename = APP_VERSION_NAME_EN if current_language() == "en" else APP_VERSION_NAME
        version_label = QLabel(tr("dlg.about.version", version=APP_VERSION, codename=codename))
        version_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(version_label)

        desc = QLabel(tr("dlg.about.desc"))
        desc.setAlignment(Qt.AlignCenter)
        desc.setWordWrap(True)
        desc.setObjectName("DimLabel")
        layout.addWidget(desc)

        author_label = QLabel(tr("dlg.about.author", author=AUTHOR))
        author_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(author_label)

        doc_label = QLabel(tr("dlg.about.doc", doc=DOC_ID, ver=DOC_VERSION))
        doc_label.setAlignment(Qt.AlignCenter)
        doc_label.setObjectName("DimLabel")
        layout.addWidget(doc_label)

        links = QHBoxLayout()
        blog_button = QPushButton(tr("dlg.about.blog"))
        blog_button.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(AUTHOR_BLOG_URL)))
        github_button = QPushButton(tr("dlg.about.github"))
        github_button.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(GITHUB_URL)))
        links.addWidget(blog_button)
        links.addWidget(github_button)
        layout.addLayout(links)

        libs = QLabel(tr("dlg.about.libs"))
        libs.setWordWrap(True)
        libs.setObjectName("DimLabel")
        layout.addWidget(libs)

        button_row = QHBoxLayout()
        button_row.addStretch(1)
        close_button = QPushButton(tr("btn.close"))
        close_button.clicked.connect(self.accept)
        button_row.addWidget(close_button)
        layout.addLayout(button_row)
