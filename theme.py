"""
theme.py
---------
앱 테마(QSS) 정의. 도트 확인에 편한 **다크**가 기본값, 설정에서 라이트로 바꿀 수 있다
(config.json의 "app_theme"). PSBrushViewer의 theme.py 구조를 따른다.

캔버스(canvas_widget.py)는 QSS가 아니라 직접 그리므로 CANVAS_COLORS를 따로 둔다.
새 테마를 추가하려면 COLORS 딕셔너리를 하나 더 만들고 THEMES / THEME_LABELS / CANVAS_COLORS에 등록한다.
"""

import os
import tempfile

DARK_COLORS = {
    "bg": "#1e1e2e",
    "bg_alt": "#252534",
    "panel": "#2b2b3b",
    "border": "#3a3a4d",
    "text": "#e0e0e0",
    "text_dim": "#9a9ab0",
    "accent": "#00a2e8",
    "selection": "#3f45aa",
    "selection_text": "#ffffff",
    "disabled": "#6a6a7c",
    "warn": "#ffb454",
    "error": "#ff6b6b",
}

LIGHT_COLORS = {
    "bg": "#f2f2f5",
    "bg_alt": "#e6e6ec",
    "panel": "#ffffff",
    "border": "#c9c9d4",
    "text": "#202024",
    "text_dim": "#6a6a76",
    "accent": "#0078c8",
    "selection": "#cfd4ff",
    "selection_text": "#202024",
    "disabled": "#a0a0ae",
    "warn": "#b36b00",
    "error": "#c62828",
}


ORANGE_COLORS = {  # 아이콘(주황 도트 + 갈색 윤곽)과 맞춘 테마
    "bg": "#1f1812", "bg_alt": "#2a2018", "panel": "#33271d", "border": "#4d3a2a",
    "text": "#f3e6d8", "text_dim": "#b39b86", "accent": "#ff8c1a",
    "selection": "#8a4a10", "selection_text": "#ffffff", "disabled": "#7a6657",
    "warn": "#ffd166", "error": "#ff6b6b",
}

MIDNIGHT_COLORS = {
    "bg": "#0f1626", "bg_alt": "#16203a", "panel": "#1b2744", "border": "#2c3c66",
    "text": "#dbe6ff", "text_dim": "#8fa3cf", "accent": "#4da3ff",
    "selection": "#2f4f9a", "selection_text": "#ffffff", "disabled": "#5f6f94",
    "warn": "#ffc666", "error": "#ff7b7b",
}

FOREST_COLORS = {
    "bg": "#121c16", "bg_alt": "#19271e", "panel": "#1f3226", "border": "#33513e",
    "text": "#dff0e3", "text_dim": "#94b59f", "accent": "#4cd47a",
    "selection": "#2a6b43", "selection_text": "#ffffff", "disabled": "#617a69",
    "warn": "#ffd166", "error": "#ff7b7b",
}

CONTRAST_COLORS = {  # 고대비: 순검정 바탕 + 흰 글자 + 노랑 강조
    "bg": "#000000", "bg_alt": "#0a0a0a", "panel": "#000000", "border": "#ffffff",
    "text": "#ffffff", "text_dim": "#d0d0d0", "accent": "#ffff00",
    "selection": "#0000ff", "selection_text": "#ffffff", "disabled": "#8c8c8c",
    "warn": "#ffff00", "error": "#ff5050",
}

# 캔버스 작업 영역/머리글 색 (QSS 밖에서 QPainter로 사용)
CANVAS_COLORS = {
    "dark": {
        "workspace": "#14141c", "header_bg": "#252534", "header_text": "#b8b8cc",
        "source": "#ffd84a", "target": "#ff8a3d", "hint": "#9a9ab0",
        "checker_a": "#9a9a9a", "checker_b": "#6e6e6e",
        "edit_outline": "#00a2e8", "ref_tint": "#3a7bff",
    },
    "light": {
        "workspace": "#d9d9e0", "header_bg": "#e6e6ec", "header_text": "#404050",
        "source": "#d4a000", "target": "#e0661a", "hint": "#6a6a76",
        "checker_a": "#ffffff", "checker_b": "#cfcfcf",
        "edit_outline": "#0078c8", "ref_tint": "#2a60d0",
    },
    "orange": {
        "workspace": "#17110c", "header_bg": "#2a2018", "header_text": "#e0c9b2",
        "source": "#ffe066", "target": "#5cd6ff", "hint": "#b39b86",
        "checker_a": "#9c8f84", "checker_b": "#6f645b",
        "edit_outline": "#ff8c1a", "ref_tint": "#4a8cff",
    },
    "midnight": {
        "workspace": "#0a101c", "header_bg": "#16203a", "header_text": "#b8c8ee",
        "source": "#ffd84a", "target": "#ff8a3d", "hint": "#8fa3cf",
        "checker_a": "#8d98b0", "checker_b": "#657089",
        "edit_outline": "#4da3ff", "ref_tint": "#ff9a3d",
    },
    "forest": {
        "workspace": "#0d1510", "header_bg": "#19271e", "header_text": "#bcd8c5",
        "source": "#ffd84a", "target": "#ff8a3d", "hint": "#94b59f",
        "checker_a": "#93a399", "checker_b": "#69786f",
        "edit_outline": "#4cd47a", "ref_tint": "#c06bff",
    },
    "contrast": {
        "workspace": "#000000", "header_bg": "#000000", "header_text": "#ffffff",
        "source": "#ffff00", "target": "#00ffff", "hint": "#d0d0d0",
        "checker_a": "#b0b0b0", "checker_b": "#707070",
        "edit_outline": "#ffff00", "ref_tint": "#ff40ff",
    },
}


def _arrow_dir() -> str:
    return os.path.join(tempfile.gettempdir(), "SpriteGridEditor_ui")


def _arrow_png(direction: str, color: str) -> str | None:
    """스핀박스 화살표(▲/▼) PNG를 테마 색으로 만들어 임시 폴더에 둔다. 실패하면 None.

    Qt QSS는 CSS의 border 삼각형 기법을 지원하지 않아(검은 네모로 그려짐) 이미지를 쓴다.
    4배 크기로 그린 뒤 줄여서 가장자리를 부드럽게 한다.
    """
    name = f"arrow_{direction}_{color.lstrip('#').lower()}.png"
    path = os.path.join(_arrow_dir(), name)
    if os.path.isfile(path):
        return path
    try:
        from PIL import Image, ImageDraw

        os.makedirs(_arrow_dir(), exist_ok=True)
        s = 4
        w, h = 10 * s, 6 * s
        im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        pts = [(0, h), (w, h), (w / 2, 0)] if direction == "up" else [(0, 0), (w, 0), (w / 2, h)]
        ImageDraw.Draw(im).polygon(pts, fill=color)
        im.resize((10, 6), Image.LANCZOS).save(path)
        return path
    except Exception:  # 쓰기 불가 등 — 화살표 없이 버튼만 남는다
        return None


def _arrow_qss(c: dict) -> str:
    parts = []
    for sub, direction, color in (("up-arrow", "up", c["text"]), ("down-arrow", "down", c["text"]),
                                  ("up-arrow:disabled", "up", c["disabled"]),
                                  ("down-arrow:disabled", "down", c["disabled"])):
        path = _arrow_png(direction, color)
        if path is None:
            continue
        url = path.replace("\\", "/")
        parts.append(f"QSpinBox::{sub}, QDoubleSpinBox::{sub} {{ image: url(\"{url}\"); "
                     f"width: 10px; height: 6px; }}")
    return "\n".join(parts)


def _build_qss(c: dict) -> str:
    return f"""
QWidget {{
    background-color: {c['bg']};
    color: {c['text']};
    font-size: 13px;
}}
QWidget:disabled {{
    color: {c['disabled']};
}}

QMainWindow, QDialog {{
    background-color: {c['bg']};
}}

#StatusBarInfo, #DimLabel {{
    color: {c['text_dim']};
}}
#LayerBar {{
    background-color: {c['bg_alt']};
    border: 1px solid {c['border']};
    border-left: 5px solid {c['accent']};
    padding: 4px 8px;
}}
#WarnLabel {{
    color: {c['warn']};
}}
#ErrorLabel {{
    color: {c['error']};
}}

QMenuBar {{
    background-color: {c['bg_alt']};
    color: {c['text']};
}}
QMenuBar::item:selected {{
    background-color: {c['selection']};
}}
QMenu {{
    background-color: {c['panel']};
    color: {c['text']};
    border: 1px solid {c['border']};
}}
QMenu::item {{
    padding: 4px 24px 4px 20px;
}}
QMenu::item:selected {{
    background-color: {c['selection']};
}}
QMenu::item:disabled {{
    color: {c['disabled']};
}}
QMenu::separator {{
    height: 1px;
    background: {c['border']};
    margin: 3px 6px;
}}

QToolBar {{
    background-color: {c['bg_alt']};
    border-bottom: 1px solid {c['border']};
    spacing: 6px;
    padding: 3px 6px;
}}
QToolBar QLabel {{
    background: transparent;
}}

QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox {{
    background-color: {c['panel']};
    color: {c['text']};
    border: 1px solid {c['border']};
    border-radius: 4px;
    padding: 3px 6px;
}}
QComboBox QAbstractItemView {{
    background-color: {c['panel']};
    selection-background-color: {c['selection']};
    selection-color: {c['selection_text']};
}}

QPushButton, QToolButton {{
    background-color: {c['panel']};
    color: {c['text']};
    border: 1px solid {c['border']};
    border-radius: 4px;
    padding: 5px 12px;
}}
QPushButton:hover, QToolButton:hover {{
    background-color: {c['selection']};
}}
QPushButton:pressed, QToolButton:pressed {{
    background-color: {c['accent']};
}}
QPushButton:disabled, QToolButton:disabled {{
    background-color: {c['bg_alt']};
    color: {c['disabled']};
}}

QGroupBox {{
    border: 1px solid {c['border']};
    border-radius: 4px;
    margin-top: 10px;
    padding-top: 8px;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    left: 8px;
    padding: 0 4px;
    color: {c['text_dim']};
}}

QCheckBox, QRadioButton, QLabel {{
    background: transparent;
}}
QCheckBox::indicator, QRadioButton::indicator, QGroupBox::indicator {{
    width: 14px;
    height: 14px;
    border: 1px solid {c['text_dim']};
    background: {c['panel']};
}}
QCheckBox::indicator, QGroupBox::indicator {{
    border-radius: 3px;
}}
QRadioButton::indicator {{
    border-radius: 8px;
}}
QCheckBox::indicator:checked, QRadioButton::indicator:checked, QGroupBox::indicator:checked {{
    background: {c['accent']};
    border: 1px solid {c['accent']};
}}
QCheckBox::indicator:disabled, QRadioButton::indicator:disabled {{
    border: 1px solid {c['disabled']};
}}

QSpinBox, QDoubleSpinBox {{
    padding-right: 20px;
}}
QSpinBox::up-button, QDoubleSpinBox::up-button {{
    subcontrol-origin: border;
    subcontrol-position: top right;
    width: 18px;
    border-left: 1px solid {c['border']};
    border-bottom: 1px solid {c['border']};
    border-top-right-radius: 4px;
    background: {c['bg_alt']};
}}
QSpinBox::down-button, QDoubleSpinBox::down-button {{
    subcontrol-origin: border;
    subcontrol-position: bottom right;
    width: 18px;
    border-left: 1px solid {c['border']};
    border-bottom-right-radius: 4px;
    background: {c['bg_alt']};
}}
QSpinBox::up-button:hover, QSpinBox::down-button:hover,
QDoubleSpinBox::up-button:hover, QDoubleSpinBox::down-button:hover {{
    background: {c['selection']};
}}
QSpinBox::up-button:pressed, QSpinBox::down-button:pressed,
QDoubleSpinBox::up-button:pressed, QDoubleSpinBox::down-button:pressed {{
    background: {c['accent']};
}}
{_arrow_qss(c)}

QTabWidget::pane {{
    border: 1px solid {c['border']};
    top: -1px;
}}
QTabBar::tab {{
    background-color: {c['bg_alt']};
    border: 1px solid {c['border']};
    padding: 5px 14px;
}}
QTabBar::tab:selected {{
    background-color: {c['panel']};
}}

QListWidget, QTextBrowser, QPlainTextEdit {{
    background-color: {c['panel']};
    color: {c['text']};
    border: 1px solid {c['border']};
    outline: none;
}}
QListWidget::item:selected {{
    background-color: {c['selection']};
    color: {c['selection_text']};
}}

QSlider::groove:horizontal {{
    height: 4px;
    background: {c['border']};
    border-radius: 2px;
}}
QSlider::handle:horizontal {{
    background: {c['accent']};
    width: 12px;
    margin: -5px 0;
    border-radius: 6px;
}}

QScrollBar:vertical {{
    background: {c['bg']};
    width: 12px;
}}
QScrollBar::handle:vertical {{
    background: {c['border']};
    min-height: 24px;
    border-radius: 5px;
}}
QScrollBar:horizontal {{
    background: {c['bg']};
    height: 12px;
}}
QScrollBar::handle:horizontal {{
    background: {c['border']};
    min-width: 24px;
    border-radius: 5px;
}}
QScrollBar::add-line, QScrollBar::sub-line {{
    height: 0px;
    width: 0px;
}}

QSplitter::handle {{
    background-color: {c['border']};
}}

QStatusBar {{
    background-color: {c['bg_alt']};
    border-top: 1px solid {c['border']};
}}
QStatusBar QLabel {{
    background: transparent;
}}

QDialogButtonBox QPushButton {{
    min-width: 70px;
}}
"""


_COLOR_SETS = {
    "dark": DARK_COLORS,
    "light": LIGHT_COLORS,
    "orange": ORANGE_COLORS,
    "midnight": MIDNIGHT_COLORS,
    "forest": FOREST_COLORS,
    "contrast": CONTRAST_COLORS,
}

THEMES = {name: _build_qss(colors) for name, colors in _COLOR_SETS.items()}
DARK_QSS = THEMES["dark"]
LIGHT_QSS = THEMES["light"]

THEME_LABEL_KEYS = {name: f"theme.{name}" for name in _COLOR_SETS}


def get_stylesheet(theme_name: str) -> str:
    return THEMES.get(theme_name, DARK_QSS)


def canvas_colors(theme_name: str) -> dict:
    return CANVAS_COLORS.get(theme_name, CANVAS_COLORS["dark"])
