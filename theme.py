"""
theme.py
---------
앱 테마(QSS) 정의. 도트 확인에 편한 **다크**가 기본값, 설정에서 라이트로 바꿀 수 있다
(config.json의 "app_theme"). PSBrushViewer의 theme.py 구조를 따른다.

캔버스(canvas_widget.py)는 QSS가 아니라 직접 그리므로 CANVAS_COLORS를 따로 둔다.
새 테마를 추가하려면 COLORS 딕셔너리를 하나 더 만들고 THEMES / THEME_LABELS / CANVAS_COLORS에 등록한다.
"""

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

# 캔버스 작업 영역/머리글 색 (QSS 밖에서 QPainter로 사용)
CANVAS_COLORS = {
    "dark": {
        "workspace": "#14141c", "header_bg": "#252534", "header_text": "#b8b8cc",
        "source": "#ffd84a", "target": "#ff8a3d", "hint": "#9a9ab0",
        "checker_a": "#9a9a9a", "checker_b": "#6e6e6e",
    },
    "light": {
        "workspace": "#d9d9e0", "header_bg": "#e6e6ec", "header_text": "#404050",
        "source": "#d4a000", "target": "#e0661a", "hint": "#6a6a76",
        "checker_a": "#ffffff", "checker_b": "#cfcfcf",
    },
}


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

QSpinBox {{
    padding-right: 18px;
}}
QSpinBox::up-button {{
    subcontrol-origin: border;
    subcontrol-position: top right;
    width: 16px;
    border-left: 1px solid {c['border']};
    border-bottom: 1px solid {c['border']};
    background: {c['bg_alt']};
}}
QSpinBox::down-button {{
    subcontrol-origin: border;
    subcontrol-position: bottom right;
    width: 16px;
    border-left: 1px solid {c['border']};
    background: {c['bg_alt']};
}}
QSpinBox::up-arrow {{
    width: 0px;
    height: 0px;
    border-left: 4px solid transparent;
    border-right: 4px solid transparent;
    border-bottom: 5px solid {c['text']};
}}
QSpinBox::down-arrow {{
    width: 0px;
    height: 0px;
    border-left: 4px solid transparent;
    border-right: 4px solid transparent;
    border-top: 5px solid {c['text']};
}}

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


DARK_QSS = _build_qss(DARK_COLORS)
LIGHT_QSS = _build_qss(LIGHT_COLORS)

THEMES = {
    "dark": DARK_QSS,
    "light": LIGHT_QSS,
}

THEME_LABEL_KEYS = {
    "dark": "theme.dark",
    "light": "theme.light",
}


def get_stylesheet(theme_name: str) -> str:
    return THEMES.get(theme_name, DARK_QSS)


def canvas_colors(theme_name: str) -> dict:
    return CANVAS_COLORS.get(theme_name, CANVAS_COLORS["dark"])
