"""
help_dialog.py
----------------
`HELP_ko.md` / `HELP_en.md` (사용법) 와 `CHANGELOG.md` (변경 내역)를 앱 안 창(마크다운 렌더)으로 보여준다.
문서는 별도 md 파일이고 exe에 포함된다 (resource_path로 읽는다).
"""

from __future__ import annotations

from PySide6.QtWidgets import QDialog, QHBoxLayout, QPushButton, QTextBrowser, QVBoxLayout

from config_manager import resource_path
from i18n import current_language, tr

KIND_HELP = "help"
KIND_CHANGELOG = "changelog"


def _read_text(name: str) -> str | None:
    try:
        with open(resource_path(name), "r", encoding="utf-8") as f:
            return f.read()
    except OSError:
        return None


def load_document(kind: str) -> str:
    if kind == KIND_CHANGELOG:
        text = _read_text("CHANGELOG.md")
    else:
        text = _read_text(f"HELP_{current_language()}.md") or _read_text("HELP_ko.md")
    return text if text is not None else tr("dlg.help.missing")


class HelpDialog(QDialog):
    def __init__(self, kind: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("dlg.help.title_changelog" if kind == KIND_CHANGELOG else "dlg.help.title"))
        self.resize(780, 680)

        layout = QVBoxLayout(self)
        self.browser = QTextBrowser()
        self.browser.setOpenExternalLinks(True)
        self.browser.setMarkdown(load_document(kind))
        layout.addWidget(self.browser, stretch=1)

        row = QHBoxLayout()
        row.addStretch(1)
        close = QPushButton(tr("btn.close"))
        close.clicked.connect(self.accept)
        row.addWidget(close)
        layout.addLayout(row)
