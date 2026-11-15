"""
shortcuts_dialog.py
---------------------
`도움말 → 단축키 목록` (기획서 §9 4단계 — 단축키 정리).

메뉴의 QAction 단축키를 **실행 중인 메뉴에서 바로 읽어** 표로 보여 준다 (문서와 실제가 어긋나지 않게).
메뉴에 없는 캔버스 조작·셀 편집 창 단축키는 고정 표로 덧붙인다.
"""

from __future__ import annotations

from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import QDialog, QHBoxLayout, QMenu, QMenuBar, QPushButton, QTextBrowser, QVBoxLayout

from i18n import tr

# 메뉴 밖 조작 (i18n 키, 키 표기)
CANVAS_KEYS = (
    ("keys.canvas_select", "keys.m_click_drag"),
    ("keys.canvas_target", "keys.m_mod_click"),
    ("keys.canvas_zoom", "keys.m_wheel"),
    ("keys.canvas_pan", "keys.m_pan"),
    ("keys.canvas_cell_edit", "keys.m_double"),
    ("keys.canvas_move_keys", "← → ↑ ↓ / Shift"),
    ("keys.canvas_move_esc", "Esc"),
)
CELL_EDITOR_KEYS = (
    ("keys.ce_paint", "B"),
    ("keys.ce_erase", "E"),
    ("keys.ce_right_erase", "keys.m_right"),
    ("keys.ce_pick", "I"),
    ("keys.ce_temp_pick", "keys.m_alt_hold"),
    ("keys.ce_move", "V"),
    ("keys.ce_move_keys", "← → ↑ ↓ / Shift"),
    ("keys.ce_line", "keys.m_shift_click"),
    ("keys.ce_brush", "[  ]"),
    ("keys.ce_slots", "1 2 3 4 5"),
    ("keys.ce_swap", "X"),
    ("keys.ce_undo", "Ctrl+Z / Ctrl+Y"),
    ("keys.ce_apply", "Ctrl+Enter"),
    ("keys.ce_cancel", "Esc"),
    ("keys.ce_zoom", "keys.m_wheel"),
)


def _clean(text: str) -> str:
    # '&&' = 글자 &, '&' 하나 = 단축 문자 표시
    return text.replace("&&", "\0").replace("&", "").replace("\0", "&").replace("|", "\\|")


def collect_menu_shortcuts(menubar: QMenuBar) -> list[tuple[str, str, str]]:
    """(메뉴 경로, 기능 이름, 단축키) — 메뉴 순서대로, 같은 동작은 한 번만."""
    rows: list[tuple[str, str, str]] = []
    seen: set[int] = set()

    def walk(menu: QMenu, path: str) -> None:
        for act in menu.actions():
            if act.menu() is not None:
                walk(act.menu(), f"{path} › {_clean(act.menu().title())}")
                continue
            keys = [s.toString(QKeySequence.NativeText) for s in act.shortcuts() if not s.isEmpty()]
            if not keys or id(act) in seen:
                continue
            seen.add(id(act))
            rows.append((path, _clean(act.text()), " / ".join(keys)))

    for act in menubar.actions():
        if act.menu() is not None:
            walk(act.menu(), _clean(act.menu().title()))
    return rows


def find_conflicts(menubar: QMenuBar) -> dict[str, list[str]]:
    """같은 단축키가 서로 다른 동작에 걸려 있으면 {단축키: [기능…]} (테스트·점검용)."""
    owners: dict[str, dict[int, str]] = {}

    def walk(menu: QMenu) -> None:
        for act in menu.actions():
            if act.menu() is not None:
                walk(act.menu())
                continue
            for s in act.shortcuts():
                if not s.isEmpty():
                    owners.setdefault(s.toString(), {})[id(act)] = _clean(act.text())

    for act in menubar.actions():
        if act.menu() is not None:
            walk(act.menu())
    return {k: list(v.values()) for k, v in owners.items() if len(v) > 1}


def build_markdown(menubar: QMenuBar) -> str:
    lines = [f"# {tr('dlg.keys.title')}", "", tr("dlg.keys.intro"), "",
             f"## {tr('dlg.keys.menus')}", "",
             f"| {tr('dlg.keys.col_menu')} | {tr('dlg.keys.col_action')} | {tr('dlg.keys.col_key')} |", "|---|---|---|"]
    for path, name, key in collect_menu_shortcuts(menubar):
        lines.append(f"| {path} | {name} | `{key}` |")
    for title, rows in (("dlg.keys.canvas", CANVAS_KEYS), ("dlg.keys.cell_editor", CELL_EDITOR_KEYS)):
        lines += ["", f"## {tr(title)}", "", f"| {tr('dlg.keys.col_action')} | {tr('dlg.keys.col_key')} |", "|---|---|"]
        for key, keys in rows:
            lines.append(f"| {tr(key)} | `{tr(keys) if keys.startswith('keys.') else keys}` |")
    return "\n".join(lines) + "\n"


class ShortcutsDialog(QDialog):
    def __init__(self, menubar: QMenuBar, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("dlg.keys.title"))
        self.resize(720, 720)
        layout = QVBoxLayout(self)
        self.browser = QTextBrowser()
        self.browser.setMarkdown(build_markdown(menubar))
        layout.addWidget(self.browser, 1)
        row = QHBoxLayout()
        row.addStretch(1)
        close = QPushButton(tr("btn.close"))
        close.clicked.connect(self.accept)
        row.addWidget(close)
        layout.addLayout(row)
