"""
cell_editor.py
----------------
4단계(v0.4.0) **셀 편집 창** — 선택한 셀(다중 셀이면 그 범위 전체)을 팝업으로 크게 띄우고
도트를 칠하기(B) / 지우기(E) / 스포이트(I) 한다. 기획서 EDT-ETC.6 §4.2 (추가 8).

- 같은 범위로 자른 Base Body·참조 레이어를 아래(또는 위)에 깔아 배경에 맞춰 작업한다.
- 범위 바깥 이웃 셀은 반투명으로 보여준다(읽기 전용). 표시 여부·불투명도는 환경설정 `cell_edit.*`.
- 칠하기·지우기 중 **Alt를 누르고 있는 동안 임시 스포이트**, `[` `]` 브러시 크기.
- (v0.4.1) **마우스 왼쪽 = 칠하기, 오른쪽 = 지우기**, Shift+클릭 = 직전 지점에서 직선,
  **이동 도구 V**(누른 칸의 그림을 끌어 옮기기 · 방향키 1px/Shift 5px, 칸 밖은 잘림),
  **색 칸 5개** — 숫자 1~5로 바꾸고 `X`는 직전 색 칸으로, 색 칸을 한 번 더 누르면 색 선택.
  **채우기 G**(이어진 같은 색 — 오른쪽 버튼은 투명으로), **좌우 반전 H**(마우스가 있는 칸),
  **시트 색** 팔레트(많이 쓴 색 → 지금 색 칸), **마스크 보기 M**(영역 의사색 오버레이).
- 커서 모양(브러시 크기 사각형 / 점선 사각형 / 스포이트) + 위쪽 상태 표시(도구·크기·색, 마우스 아래 좌표·색).
- 스포이트는 색과 **마스크 인덱스를 함께** 가져와 칠할 때 같이 기록한다. 칠하기는 알파 255, 지우면 인덱스 0.
- `적용`(Ctrl+Enter)이면 결과 블록을 돌려주고 닫는다 → 메인 실행 취소에서는 한 단계.
  창 안에서도 Ctrl+Z / Ctrl+Y 로 따로 되돌린다. `취소`·Esc 는 버리고 닫기(바뀐 게 있으면 확인).
- 이 창의 단축키는 창 안에서만 동작한다 (메인의 B 배경 순환·E 마스크 지우개와 충돌하지 않음).
"""

from __future__ import annotations

import math

import numpy as np
from PySide6.QtCore import QPoint, QRect, Qt, QTimer, Signal
from PySide6.QtGui import (
    QBrush, QColor, QCursor, QIcon, QImage, QKeySequence, QPainter, QPen, QPixmap, QPolygon, QShortcut,
)
from PySide6.QtWidgets import (
    QCheckBox, QColorDialog, QComboBox, QDialog, QHBoxLayout, QLabel, QMenu, QMessageBox,
    QPushButton, QScrollArea, QSpinBox, QToolButton, QVBoxLayout, QWidget,
)

import mask_convert as mc
import pixel_tools as pt
from canvas_widget import numpy_to_qimage
from config_manager import ConfigManager
from history import History
from i18n import tr
from layer_model import RefLayer, stack_order
from sheet_model import CellRect, GridSpec
from theme import canvas_colors

TOOL_PAINT = "paint"
TOOL_ERASE = "erase"
TOOL_PICK = "pick"
TOOL_MOVE = "move"
TOOL_FILL = "fill"
SLOT_COUNT = 5
# 기본 색 칸 (rgb, 마스크 번호) — 마스크가 없는 시트에서는 번호를 쓰지 않는다
DEFAULT_SLOTS = [("#3B4C75", 1), ("#FFFFFF", 2), ("#1F1F24", 3), ("#808080", 4), ("#B8322E", 1)]
ZOOMS = (100, 200, 300, 400, 600, 800, 1000, 1200, 1600, 2000, 2400)
PAD = 24


class _WorkDoc:
    """창 안의 작업 문서 (History.do 의 대상). 범위 + 이웃 셀을 포함한 사각형의 사본."""

    def __init__(self, arr: np.ndarray, mask: np.ndarray | None):
        self.arr = arr
        self.has_mask = mask is not None
        self.mask = mask if mask is not None else np.zeros(arr.shape[:2], dtype=np.uint8)


def edit_region(grid: GridSpec, rect: CellRect, neighbors: bool = True) -> tuple[int, int, int, int]:
    """창에 띄울 사각형 (y0, y1, x0, x1): 편집 범위 + (이웃 셀 표시 시) 사방 한 칸, 시트 안으로 자름."""
    m = 1 if neighbors else 0
    r0, r1 = max(0, rect.r0 - m), min(grid.rows - 1, rect.r1 + m)
    c0, c1 = max(0, rect.c0 - m), min(grid.cols - 1, rect.c1 + m)
    return r0 * grid.cell_h, (r1 + 1) * grid.cell_h, c0 * grid.cell_w, (c1 + 1) * grid.cell_w


def make_pick_cursor() -> QCursor:
    """스포이트 커서 (끝 = 찍는 지점, 왼쪽 아래)."""
    pm = QPixmap(24, 24)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing, True)
    p.setPen(QPen(QColor("#000000"), 3))
    p.drawLine(2, 21, 15, 8)
    p.setPen(QPen(QColor("#ffffff"), 1.5))
    p.drawLine(2, 21, 15, 8)
    p.setPen(QPen(QColor("#000000"), 1))
    p.setBrush(QColor("#ffffff"))
    p.drawPolygon(QPolygon([QPoint(13, 6), QPoint(18, 11), QPoint(21, 8), QPoint(16, 3)]))
    p.setBrush(QColor("#000000"))
    p.drawEllipse(QPoint(19, 5), 3, 3)
    p.end()
    return QCursor(pm, 1, 22)


def make_square_cursor(size_px: int, dashed: bool) -> QCursor:
    """브러시 크기(화면 픽셀)만큼의 정사각형 테두리 커서. 너무 크면 64px로 제한."""
    n = max(5, min(64, int(size_px)))
    pm = QPixmap(n + 2, n + 2)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setPen(QPen(QColor("#000000"), 1))
    p.drawRect(0, 0, n + 1, n + 1)
    pen = QPen(QColor("#ffffff"), 1, Qt.DashLine if dashed else Qt.SolidLine)
    p.setPen(pen)
    p.drawRect(1, 1, n - 1, n - 1)
    p.end()
    return QCursor(pm, (n + 2) // 2, (n + 2) // 2)


class CellEditView(QWidget):
    """확대 표시 + 마우스 입력. 좌표는 창 작업 문서(region) 기준 픽셀."""

    pressed = Signal(int, int, bool, bool, bool)  # x, y, alt, 오른쪽 버튼, shift
    moved = Signal(int, int, bool)
    released = Signal(int, int)
    hovered = Signal(int, int)  # 밖이면 -1, -1
    zoomRequested = Signal(int, QPoint)  # +1/-1, 뷰 좌표

    def __init__(self, doc: _WorkDoc, edit_box: tuple[int, int, int, int], theme: str, parent=None):
        super().__init__(parent)
        self.doc = doc
        self.edit_box = edit_box  # (y0, y1, x0, x1) region 좌표
        self.theme = theme
        self.scale = 8
        self.refs: list[tuple[QImage, int, bool]] = []  # (잘린 그림, 불투명도, 위?)
        self.cell_lines: tuple[list[int], list[int]] = ([], [])  # region 안 셀 경계 x, y
        self.show_neighbors = True
        self.neighbor_opacity = 40
        self.brush = 1
        self.tool = TOOL_PAINT
        self.show_mask = False  # 마스크 영역 의사색 오버레이 (v0.4.1)
        self.hover: tuple[int, int] | None = None
        self._image = numpy_to_qimage(doc.arr)
        self._drag = False
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self._checker = self._make_checker()
        self._resize()

    def _make_checker(self) -> QBrush:
        colors = canvas_colors(self.theme)
        pm = QPixmap(16, 16)
        pm.fill(QColor(colors["checker_a"]))
        p = QPainter(pm)
        p.fillRect(8, 0, 8, 8, QColor(colors["checker_b"]))
        p.fillRect(0, 8, 8, 8, QColor(colors["checker_b"]))
        p.end()
        return QBrush(pm)

    def set_scale(self, s: int) -> None:
        self.scale = max(1, int(s))
        self._resize()
        self.update()

    def _resize(self) -> None:
        h, w = self.doc.arr.shape[:2]
        self.setFixedSize(w * self.scale + 2 * PAD, h * self.scale + 2 * PAD)

    def pixel_at(self, pos: QPoint, clamp: bool = False) -> tuple[int, int] | None:
        s = self.scale
        x = math.floor((pos.x() - PAD) / s)
        y = math.floor((pos.y() - PAD) / s)
        h, w = self.doc.arr.shape[:2]
        if clamp:
            return max(0, min(w - 1, x)), max(0, min(h - 1, y))
        if 0 <= x < w and 0 <= y < h:
            return x, y
        return None

    def in_edit(self, x: int, y: int) -> bool:
        y0, y1, x0, x1 = self.edit_box
        return x0 <= x < x1 and y0 <= y < y1

    # -- 그리기 --------------------------------------------------------------
    def paintEvent(self, event) -> None:
        colors = canvas_colors(self.theme)
        p = QPainter(self)
        p.setRenderHint(QPainter.SmoothPixmapTransform, False)
        p.setRenderHint(QPainter.Antialiasing, False)
        p.fillRect(self.rect(), QColor(colors["workspace"]))
        s = self.scale
        h, w = self.doc.arr.shape[:2]
        full = QRect(PAD, PAD, w * s, h * s)
        ey0, ey1, ex0, ex1 = self.edit_box
        edit_rect = QRect(PAD + ex0 * s, PAD + ey0 * s, (ex1 - ex0) * s, (ey1 - ey0) * s)
        clip = event.rect()
        # 배경 체크무늬 (이웃 셀을 숨기면 편집 범위만)
        p.setBrushOrigin(PAD, PAD)
        p.fillRect((full if self.show_neighbors else edit_rect).intersected(clip), self._checker)
        below = [r for r in self.refs if not r[2]]
        above = [r for r in self.refs if r[2]]
        for img, op, _a in below:
            self._draw_layer(p, img, op / 100.0, full, edit_rect)
        # 이웃 셀(반투명) → 편집 범위(불투명)
        if self.show_neighbors and self.neighbor_opacity > 0:
            p.save()
            region = QRect(full)
            p.setClipRegion(_minus(region, edit_rect))
            p.setOpacity(self.neighbor_opacity / 100.0)
            p.drawImage(full, self._image)
            p.restore()
        p.save()
        p.setClipRect(edit_rect)
        p.drawImage(full, self._image)
        p.restore()
        for img, op, _a in above:
            self._draw_layer(p, img, op / 100.0, full, edit_rect)
        if self.show_mask and self.doc.has_mask:
            ey0_, ey1_, ex0_, ex1_ = self.edit_box
            sub = self.doc.arr[ey0_:ey1_, ex0_:ex1_]
            over = mc.pseudo_color(self.doc.mask[ey0_:ey1_, ex0_:ex1_], sub[..., 3] > 0)
            p.save()
            p.setOpacity(0.55)
            p.drawImage(edit_rect, numpy_to_qimage(over))
            p.restore()
        # 픽셀 격자 (확대가 클 때)
        if s >= 6:
            pen = QPen(QColor(255, 255, 255, 28), 1)
            p.setPen(pen)
            vis = (full if self.show_neighbors else edit_rect).intersected(clip)
            x = PAD + ((vis.left() - PAD) // s) * s
            while x <= vis.right():
                p.drawLine(x, vis.top(), x, vis.bottom())
                x += s
            y = PAD + ((vis.top() - PAD) // s) * s
            while y <= vis.bottom():
                p.drawLine(vis.left(), y, vis.right(), y)
                y += s
        # 셀 경계
        if self.show_neighbors:
            pen = QPen(QColor(colors.get("grid", "#00e5ff")), 1, Qt.DotLine)
            p.setPen(pen)
            xs, ys = self.cell_lines
            for cx in xs:
                p.drawLine(PAD + cx * s, PAD, PAD + cx * s, PAD + h * s)
            for cy in ys:
                p.drawLine(PAD, PAD + cy * s, PAD + w * s, PAD + cy * s)
        # 편집 범위 윤곽
        p.setPen(QPen(QColor(colors["source"]), 2))
        p.setBrush(Qt.NoBrush)
        p.drawRect(edit_rect.adjusted(-1, -1, 0, 0))
        # 브러시 미리보기 (칠해질 픽셀 범위)
        if self.hover is not None and self.tool not in (TOOL_PICK, TOOL_MOVE, TOOL_FILL):
            x, y = self.hover
            half = self.brush // 2
            pen = QPen(QColor(colors["target"]), 1, Qt.DashLine if self.tool == TOOL_ERASE else Qt.SolidLine)
            p.setPen(pen)
            p.drawRect(QRect(PAD + (x - half) * s, PAD + (y - half) * s, self.brush * s - 1, self.brush * s - 1))
        p.end()

    def _draw_layer(self, p: QPainter, img: QImage, opacity: float, full: QRect, edit_rect: QRect) -> None:
        p.save()
        if not self.show_neighbors:
            p.setClipRect(edit_rect)
        p.setOpacity(opacity)
        p.drawImage(full, img)
        p.restore()

    # -- 입력 ----------------------------------------------------------------
    def mousePressEvent(self, e) -> None:
        self.setFocus()
        if e.button() not in (Qt.LeftButton, Qt.RightButton) or self._drag:
            return
        px = self.pixel_at(e.position().toPoint())
        if px is None:
            return
        self._drag = True
        self._drag_button = e.button()
        self.pressed.emit(px[0], px[1], bool(e.modifiers() & Qt.AltModifier), e.button() == Qt.RightButton,
                          bool(e.modifiers() & Qt.ShiftModifier))

    def mouseMoveEvent(self, e) -> None:
        pos = e.position().toPoint()
        px = self.pixel_at(pos)
        self.hover = px
        self.hovered.emit(*(px if px else (-1, -1)))
        if self._drag:
            cp = self.pixel_at(pos, clamp=True)
            self.moved.emit(cp[0], cp[1], bool(e.modifiers() & Qt.AltModifier))
        self.update()

    def mouseReleaseEvent(self, e) -> None:
        if not self._drag or e.button() != getattr(self, "_drag_button", Qt.LeftButton):
            return
        self._drag = False
        cp = self.pixel_at(e.position().toPoint(), clamp=True)
        self.released.emit(cp[0], cp[1])

    def contextMenuEvent(self, e) -> None:
        e.accept()  # 오른쪽 버튼은 지우기 — 메뉴를 띄우지 않는다

    def leaveEvent(self, e) -> None:
        self.hover = None
        self.hovered.emit(-1, -1)
        self.update()
        super().leaveEvent(e)

    def wheelEvent(self, e) -> None:
        d = e.angleDelta().y()
        if d:
            self.zoomRequested.emit(1 if d > 0 else -1, e.position().toPoint())
        e.accept()


def _minus(outer: QRect, inner: QRect):
    from PySide6.QtGui import QRegion
    return QRegion(outer).subtracted(QRegion(inner))


class CellEditDialog(QDialog):
    """셀 편집 창. exec() 후 ``result_block``(RGBA)·``result_mask``(마스크 블록 또는 None)·``changed``."""

    def __init__(self, parent, arr: np.ndarray, mask: np.ndarray | None, grid: GridSpec, rect: CellRect,
                 refs: list[RefLayer], config: ConfigManager, theme: str = "dark"):
        super().__init__(parent)
        self.config = config
        self.grid = grid
        self.rect = rect
        self.theme = theme
        self.result_block: np.ndarray | None = None
        self.result_mask: np.ndarray | None = None
        self.changed = False
        self.setWindowTitle(tr("dlg.celledit.title", range=_rect_label(rect)))
        self.setMinimumSize(720, 560)

        self.show_neighbors = bool(config.get("cell_edit.neighbors", True))
        # 이웃 셀은 표시 설정과 무관하게 작업 문서에 넣어 둔다 (창 안에서 켜고 끌 수 있게)
        ry0, ry1, rx0, rx1 = edit_region(grid, rect, True)
        self.origin = (ry0, rx0)
        self.doc = _WorkDoc(arr[ry0:ry1, rx0:rx1].copy(), mask[ry0:ry1, rx0:rx1].copy() if mask is not None else None)
        ey0, ey1 = rect.r0 * grid.cell_h - ry0, (rect.r1 + 1) * grid.cell_h - ry0
        ex0, ex1 = rect.c0 * grid.cell_w - rx0, (rect.c1 + 1) * grid.cell_w - rx0
        self.edit_box = (ey0, ey1, ex0, ex1)
        self.history = History()

        self.tool = TOOL_PAINT
        self._temp_pick = False
        self.colors = self._load_slots()  # 색 칸 5개: (rgb, 마스크 인덱스 또는 None)
        self.slot = 0
        self._prev_slot = 1
        self._stroke: dict | None = None
        self._last_point: tuple[int, int] | None = None  # Shift+클릭 직선의 시작점

        self.view = CellEditView(self.doc, self.edit_box, theme)
        self.view.show_neighbors = self.show_neighbors
        self.view.neighbor_opacity = int(config.get("cell_edit.neighbor_opacity", 40))
        self.view.refs = self._crop_refs(refs, ry0, ry1, rx0, rx1)
        self.view.cell_lines = (
            [c * grid.cell_w - rx0 for c in range(rx0 // grid.cell_w + 1, rx1 // grid.cell_w)],
            [r * grid.cell_h - ry0 for r in range(ry0 // grid.cell_h + 1, ry1 // grid.cell_h)])
        self.view.pressed.connect(self._on_press)
        self.view.moved.connect(self._on_move)
        self.view.released.connect(self._on_release)
        self.view.hovered.connect(self._on_hover)
        self.view.zoomRequested.connect(self._on_zoom_wheel)
        self._build_ui()
        zoom = int(config.get("cell_edit.zoom", 800))
        self._set_zoom(min(ZOOMS, key=lambda z: abs(z - zoom)))
        self._set_tool(TOOL_PAINT)
        self.resize(1100, 820)

    # -- 구성 ----------------------------------------------------------------
    def _crop_refs(self, refs: list[RefLayer], y0: int, y1: int, x0: int, x1: int):
        out = []
        below, above = stack_order(refs)
        for layer in below + above:
            img = layer.image
            if not layer.visible or img is None or layer.opacity <= 0:
                continue
            out.append((img.copy(x0, y0, x1 - x0, y1 - y0), int(layer.opacity), bool(layer.above)))
        return out

    def _tool_button(self, key: str, tool: str) -> QToolButton:
        b = QToolButton()
        b.setText(tr(key))
        b.setCheckable(True)
        b.setFocusPolicy(Qt.NoFocus)
        b.setToolButtonStyle(Qt.ToolButtonTextOnly)
        b.clicked.connect(lambda _c=False, t=tool: self._set_tool(t))
        return b

    def _build_ui(self) -> None:
        lay = QVBoxLayout(self)
        bar = QHBoxLayout()
        self.btn_paint = self._tool_button("dlg.celledit.tool_paint", TOOL_PAINT)
        self.btn_erase = self._tool_button("dlg.celledit.tool_erase", TOOL_ERASE)
        self.btn_pick = self._tool_button("dlg.celledit.tool_pick", TOOL_PICK)
        self.btn_move = self._tool_button("dlg.celledit.tool_move", TOOL_MOVE)
        self.btn_fill = self._tool_button("dlg.celledit.tool_fill", TOOL_FILL)
        for b in (self.btn_paint, self.btn_erase, self.btn_pick, self.btn_fill, self.btn_move):
            bar.addWidget(b)
        bar.addSpacing(12)
        bar.addWidget(QLabel(tr("dlg.celledit.brush")))
        self.sp_brush = QSpinBox()
        self.sp_brush.setRange(1, pt.MAX_BRUSH)
        self.sp_brush.setValue(int(self.config.get("cell_edit.brush", 1)))
        self.sp_brush.setFocusPolicy(Qt.ClickFocus)
        self.sp_brush.valueChanged.connect(self._on_brush)
        bar.addWidget(self.sp_brush)
        bar.addSpacing(12)
        bar2 = QHBoxLayout()  # 둘째 줄: 색 칸 5개 + 마스크 번호
        bar2.addWidget(QLabel(tr("dlg.celledit.slots")))
        self.slot_buttons: list[QPushButton] = []
        for i in range(SLOT_COUNT):
            b = QPushButton(str(i + 1))
            b.setFixedSize(40, 28)
            b.setFocusPolicy(Qt.NoFocus)
            b.setToolTip(tr("dlg.celledit.slot_tip", n=i + 1))
            b.clicked.connect(lambda _c=False, i=i: self._on_slot_clicked(i))
            bar2.addWidget(b)
            self.slot_buttons.append(b)
        self.lb_index = QLabel(tr("dlg.celledit.index"))
        self.cb_index = QComboBox()
        self.cb_index.setFocusPolicy(Qt.NoFocus)
        for n in range(5):
            self.cb_index.addItem(tr(f"mask.idx{n}"), n)
        self.cb_index.activated.connect(self._on_index)
        self.btn_palette = QPushButton(tr("dlg.celledit.palette"))
        self.btn_palette.setFocusPolicy(Qt.NoFocus)
        self.btn_palette.setAutoDefault(False)
        self.btn_palette.setToolTip(tr("dlg.celledit.palette_tip"))
        self.btn_palette.clicked.connect(self._show_palette)
        bar2.addWidget(self.btn_palette)
        bar2.addSpacing(12)
        bar2.addWidget(self.lb_index)
        bar2.addWidget(self.cb_index)
        bar2.addStretch(1)
        self.btn_flip = QPushButton(tr("dlg.celledit.flip"))
        self.btn_flip.setFocusPolicy(Qt.NoFocus)
        self.btn_flip.setAutoDefault(False)
        self.btn_flip.setToolTip(tr("dlg.celledit.flip_tip"))
        self.btn_flip.clicked.connect(self.flip_cell)
        bar2.addWidget(self.btn_flip)
        self.chk_mask = QCheckBox(tr("dlg.celledit.show_mask"))
        self.chk_mask.setFocusPolicy(Qt.NoFocus)
        self.chk_mask.setVisible(self.doc.has_mask)
        self.chk_mask.toggled.connect(self._on_show_mask)
        bar2.addWidget(self.chk_mask)
        self.lb_index.setVisible(self.doc.has_mask)
        self.cb_index.setVisible(self.doc.has_mask)
        bar.addStretch(1)
        self.chk_neighbors = QCheckBox(tr("dlg.celledit.neighbors"))
        self.chk_neighbors.setFocusPolicy(Qt.NoFocus)
        self.chk_neighbors.setChecked(self.show_neighbors)
        self.chk_neighbors.toggled.connect(self._on_neighbors)
        bar.addWidget(self.chk_neighbors)
        self.cb_zoom = QComboBox()
        self.cb_zoom.setFocusPolicy(Qt.NoFocus)
        for z in ZOOMS:
            self.cb_zoom.addItem(f"{z}%", z)
        self.cb_zoom.activated.connect(lambda _i: (self._set_zoom(int(self.cb_zoom.currentData())),
                                                   self.center_on_edit()))
        bar.addWidget(self.cb_zoom)
        lay.addLayout(bar)
        lay.addLayout(bar2)

        self.lb_state = QLabel()
        self.lb_state.setObjectName("CellEditState")
        self.lb_state.setStyleSheet("QLabel#CellEditState { font-weight: bold; padding: 2px 4px; }")
        self.lb_hover = QLabel()
        srow = QHBoxLayout()
        srow.addWidget(self.lb_state)
        srow.addStretch(1)
        srow.addWidget(self.lb_hover)
        lay.addLayout(srow)

        self.scroll = QScrollArea()
        self.scroll.setWidget(self.view)
        self.scroll.setAlignment(Qt.AlignCenter)
        self.scroll.setFocusPolicy(Qt.NoFocus)
        lay.addWidget(self.scroll, 1)

        hint = QLabel(tr("dlg.celledit.hint"))
        hint.setWordWrap(True)
        hint.setObjectName("HintLabel")
        lay.addWidget(hint)
        row = QHBoxLayout()
        self.btn_undo = QPushButton(tr("act.undo"))
        self.btn_redo = QPushButton(tr("act.redo"))
        for b in (self.btn_undo, self.btn_redo):
            b.setFocusPolicy(Qt.NoFocus)
            b.setAutoDefault(False)
        self.btn_undo.clicked.connect(self.undo)
        self.btn_redo.clicked.connect(self.redo)
        row.addWidget(self.btn_undo)
        row.addWidget(self.btn_redo)
        row.addStretch(1)
        self.btn_apply = QPushButton(tr("dlg.celledit.apply"))
        self.btn_cancel = QPushButton(tr("btn.cancel"))
        for b in (self.btn_apply, self.btn_cancel):
            b.setAutoDefault(False)
            b.setFocusPolicy(Qt.NoFocus)
        self.btn_apply.clicked.connect(self.apply)
        self.btn_cancel.clicked.connect(self.reject)
        row.addWidget(self.btn_apply)
        row.addWidget(self.btn_cancel)
        lay.addLayout(row)

        # 창 안에서만 동작하는 단축키
        keys = [("B", lambda: self._set_tool(TOOL_PAINT)), ("E", lambda: self._set_tool(TOOL_ERASE)),
                ("I", lambda: self._set_tool(TOOL_PICK)), ("V", lambda: self._set_tool(TOOL_MOVE)),
                ("G", lambda: self._set_tool(TOOL_FILL)), ("H", self.flip_cell),
                ("M", lambda: self.chk_mask.setChecked(not self.chk_mask.isChecked()) if self.doc.has_mask else None),
                ("X", self._swap_colors)]
        keys += [(str(i + 1), lambda i=i: self.select_slot(i)) for i in range(SLOT_COUNT)]
        for d, (dx, dy) in (("Left", (-1, 0)), ("Right", (1, 0)), ("Up", (0, -1)), ("Down", (0, 1))):
            keys.append((d, lambda dx=dx, dy=dy: self.nudge(dx, dy)))
            keys.append(("Shift+" + d, lambda dx=dx, dy=dy: self.nudge(dx * 5, dy * 5)))
        for seq, fn in keys + [
                        ("[", lambda: self.sp_brush.setValue(self.sp_brush.value() - 1)),
                        ("]", lambda: self.sp_brush.setValue(self.sp_brush.value() + 1)),
                        ("Ctrl+Z", self.undo), ("Ctrl+Y", self.redo), ("Ctrl+Shift+Z", self.redo),
                        ("Ctrl+Return", self.apply), ("Ctrl+Enter", self.apply)]:
            sc = QShortcut(QKeySequence(seq), self)
            sc.setContext(Qt.WindowShortcut)
            sc.activated.connect(fn)
        self._refresh_colors()
        self._update_undo_buttons()

    # -- 도구 / 색 -----------------------------------------------------------
    def effective_tool(self) -> str:
        if self.tool != TOOL_PICK and self._temp_pick:
            return TOOL_PICK
        return self.tool

    def _set_tool(self, tool: str) -> None:
        self.tool = tool
        self.btn_paint.setChecked(tool == TOOL_PAINT)
        self.btn_erase.setChecked(tool == TOOL_ERASE)
        self.btn_pick.setChecked(tool == TOOL_PICK)
        self.btn_move.setChecked(tool == TOOL_MOVE)
        self.btn_fill.setChecked(tool == TOOL_FILL)
        self._refresh_tool_state()

    def _refresh_tool_state(self) -> None:
        tool = self.effective_tool()
        self.view.tool = tool
        self.view.brush = self.sp_brush.value()
        if tool == TOOL_PICK:
            self.view.setCursor(make_pick_cursor())
        elif tool == TOOL_MOVE:
            self.view.setCursor(Qt.SizeAllCursor)
        elif tool == TOOL_FILL:
            self.view.setCursor(Qt.CrossCursor)
        else:
            self.view.setCursor(make_square_cursor(self.view.brush * self.view.scale, tool == TOOL_ERASE))
        rgb, idx = self.colors[self.slot]
        hexc = "#%02X%02X%02X" % rgb
        if tool == TOOL_MOVE:
            text = tr("dlg.celledit.state_move")
        elif tool == TOOL_FILL:
            text = tr("dlg.celledit.state_fill", color=hexc, slot=self.slot + 1)
        elif tool == TOOL_PICK:
            key = "dlg.celledit.state_pick_temp" if self.tool != TOOL_PICK else "dlg.celledit.state_pick"
            text = tr(key, back=tr(f"dlg.celledit.state_name_{self.tool}"))
        elif tool == TOOL_ERASE:
            text = tr("dlg.celledit.state_erase", size=self.view.brush)
        else:
            text = tr("dlg.celledit.state_paint", size=self.view.brush, color=hexc, slot=self.slot + 1)
            if self.doc.has_mask and idx is not None:
                text += tr("dlg.celledit.state_index", index=idx)
        self.lb_state.setText(text)
        self.view.update()

    def _on_brush(self, v: int) -> None:
        self.config.set("cell_edit.brush", int(v))
        self._refresh_tool_state()

    def _load_slots(self) -> list[tuple[tuple[int, int, int], int | None]]:
        saved = self.config.get("cell_edit.colors", []) or []
        out = []
        for i in range(SLOT_COUNT):
            hexc, idx = DEFAULT_SLOTS[i]
            if i < len(saved) and isinstance(saved[i], (list, tuple)) and len(saved[i]) == 2:
                hexc, idx = saved[i]
            try:
                rgb = tuple(int(str(hexc).lstrip("#")[k:k + 2], 16) for k in (0, 2, 4))
            except ValueError:
                rgb = (0, 0, 0)
            out.append((rgb, int(idx) if self.doc.has_mask else None))
        return out

    def _save_slots(self) -> None:
        self.config.set("cell_edit.colors", [["#%02X%02X%02X" % rgb, idx if idx is not None else saved_idx]
                                             for (rgb, idx), (_h, saved_idx) in zip(self.colors, DEFAULT_SLOTS)])

    def _refresh_colors(self) -> None:
        for i, (b, (rgb, _i)) in enumerate(zip(self.slot_buttons, self.colors)):
            light = (rgb[0] * 299 + rgb[1] * 587 + rgb[2] * 114) / 1000 > 140
            border = "3px solid #FFD700" if i == self.slot else "1px solid #888"
            b.setStyleSheet("QPushButton { background-color: #%02X%02X%02X; color: %s; border: %s; font-weight: bold; }"
                            % (*rgb, "#000" if light else "#FFF", border))
        idx = self.colors[self.slot][1]
        if idx is not None:
            self.cb_index.setCurrentIndex(self.cb_index.findData(idx))
        self._refresh_tool_state()

    def select_slot(self, i: int) -> None:
        """색 칸 i(0~4)를 칠할 색으로 (숫자키 1~5)."""
        if not 0 <= i < SLOT_COUNT or i == self.slot:
            return
        self._prev_slot, self.slot = self.slot, i
        self._refresh_colors()

    def _on_slot_clicked(self, i: int) -> None:
        if i == self.slot:
            self._choose_color(i)  # 이미 고른 칸을 한 번 더 누르면 색 선택
        else:
            self.select_slot(i)

    def _choose_color(self, i: int) -> None:
        rgb, idx = self.colors[i]
        c = QColorDialog.getColor(QColor(*rgb), self, tr("dlg.celledit.choose_color"))
        if c.isValid():
            self.colors[i] = ((c.red(), c.green(), c.blue()), idx)
            self._save_slots()
            self._refresh_colors()

    def _swap_colors(self) -> None:
        """X: 직전에 쓰던 색 칸으로."""
        self.select_slot(self._prev_slot)

    def _on_index(self, _i: int) -> None:
        rgb, _old = self.colors[self.slot]
        self.colors[self.slot] = (rgb, int(self.cb_index.currentData()))
        self._save_slots()
        self._refresh_tool_state()

    def _on_neighbors(self, on: bool) -> None:
        self.show_neighbors = bool(on)
        self.view.show_neighbors = self.show_neighbors
        self.config.set("cell_edit.neighbors", self.show_neighbors)
        self.view.update()

    def _set_zoom(self, z: int) -> None:
        self.cb_zoom.setCurrentIndex(self.cb_zoom.findData(z))
        self.view.set_scale(max(1, z // 100))
        self._refresh_tool_state()

    def center_on_edit(self) -> None:
        """편집 범위의 가운데가 화면 가운데 오도록 스크롤 (창을 열 때·줌을 고를 때)."""
        s = self.view.scale
        ey0, ey1, ex0, ex1 = self.edit_box
        vp = self.scroll.viewport()
        self.scroll.horizontalScrollBar().setValue(int(PAD + (ex0 + ex1) / 2 * s - vp.width() / 2))
        self.scroll.verticalScrollBar().setValue(int(PAD + (ey0 + ey1) / 2 * s - vp.height() / 2))

    def showEvent(self, e) -> None:
        super().showEvent(e)
        if not getattr(self, "_centered", False):
            self._centered = True
            QTimer.singleShot(0, self.center_on_edit)

    def _on_zoom_wheel(self, direction: int, pos: QPoint) -> None:
        cur = int(self.cb_zoom.currentData())
        i = min(range(len(ZOOMS)), key=lambda k: abs(ZOOMS[k] - cur))
        i = max(0, min(len(ZOOMS) - 1, i + direction))
        if ZOOMS[i] == cur:
            return
        old_s = self.view.scale
        hb, vb = self.scroll.horizontalScrollBar(), self.scroll.verticalScrollBar()
        vp_pos = self.view.mapTo(self.scroll.viewport(), pos)
        ix, iy = (pos.x() - PAD) / old_s, (pos.y() - PAD) / old_s
        self._set_zoom(ZOOMS[i])
        s = self.view.scale
        hb.setValue(int(PAD + ix * s - vp_pos.x()))
        vb.setValue(int(PAD + iy * s - vp_pos.y()))

    # -- Alt 임시 스포이트 ----------------------------------------------------
    def keyPressEvent(self, e) -> None:
        if e.key() == Qt.Key_Alt and not e.isAutoRepeat():
            self._temp_pick = True
            self._refresh_tool_state()
            e.accept()
            return
        if e.key() == Qt.Key_Escape:
            self.reject()
            return
        if e.key() in (Qt.Key_Return, Qt.Key_Enter) and not (e.modifiers() & Qt.ControlModifier):
            e.accept()  # 그냥 Enter로 창이 닫히지 않게
            return
        super().keyPressEvent(e)

    def keyReleaseEvent(self, e) -> None:
        if e.key() == Qt.Key_Alt and not e.isAutoRepeat():
            self._temp_pick = False
            self._refresh_tool_state()
            e.accept()
            return
        super().keyReleaseEvent(e)

    def set_temp_pick(self, on: bool) -> None:
        """테스트·외부용: Alt 누름/뗌과 같은 효과."""
        self._temp_pick = bool(on)
        self._refresh_tool_state()

    # -- 칠하기 --------------------------------------------------------------
    def _bounds(self):
        return self.edit_box

    def _mask(self):
        return self.doc.mask if self.doc.has_mask else None

    def _pick_at(self, x: int, y: int) -> None:
        if not self.view.in_edit(x, y) and not self.show_neighbors:
            return
        rgba, idx = pt.pick(self.doc.arr, self._mask(), x, y)
        if rgba[3] == 0:
            return  # 투명은 고르지 않는다
        self.colors[self.slot] = (rgba[:3], idx if self.doc.has_mask else None)
        self._save_slots()
        self._refresh_colors()

    def _on_press(self, x: int, y: int, alt: bool, right: bool = False, shift: bool = False) -> None:
        tool = TOOL_PICK if alt else self.effective_tool()  # Alt+클릭도 스포이트
        if tool == TOOL_PICK:
            self._pick_at(x, y)
            self._stroke = {"tool": TOOL_PICK}
            return
        if tool == TOOL_MOVE:
            box = self.cell_box_at(x, y)
            if box is None:
                return
            y0, y1, x0, x1 = box
            self._stroke = {"tool": TOOL_MOVE, "rec": pt.PixelRecorder(self.doc), "box": box, "start": (x, y),
                            "block": self.doc.arr[y0:y1, x0:x1].copy(),
                            "mblock": self.doc.mask[y0:y1, x0:x1].copy() if self.doc.has_mask else None}
            return
        if tool == TOOL_FILL:
            rec = pt.PixelRecorder(self.doc)
            rgb, idx = self.colors[self.slot]
            rec.add(pt.fill_region(self.doc.arr, self._mask(), x, y, rgb, idx, self._bounds(), erase=right))
            self._stroke = {"tool": TOOL_FILL, "rec": rec}
            self.view.update()
            return
        if right:
            tool = TOOL_ERASE  # 오른쪽 버튼 = 지우기
        rec = pt.PixelRecorder(self.doc)
        start = self._last_point if (shift and self._last_point is not None) else (x, y)
        self._stroke = {"tool": tool, "rec": rec, "last": start}
        self._apply_segment(start, (x, y))

    def _apply_segment(self, p0, p1) -> None:
        st = self._stroke
        size = self.sp_brush.value()
        if st["tool"] == TOOL_ERASE:
            d = pt.erase_line(self.doc.arr, self._mask(), p0, p1, size, self._bounds())
        else:
            rgb, idx = self.colors[self.slot]
            d = pt.paint_line(self.doc.arr, self._mask(), p0, p1, size, rgb, idx, self._bounds())
        st["rec"].add(d)
        st["last"] = p1
        self._last_point = p1
        if d is not None:
            self.view.update()

    def _on_move(self, x: int, y: int, alt: bool) -> None:
        st = self._stroke
        if st is None:
            return
        if st["tool"] == TOOL_PICK:
            self._pick_at(x, y)
            return
        if st["tool"] == TOOL_MOVE:
            self._move_preview(x - st["start"][0], y - st["start"][1])
            return
        if st["tool"] == TOOL_FILL:
            return
        self._apply_segment(st["last"], (x, y))

    def cell_box_at(self, x: int, y: int) -> tuple[int, int, int, int] | None:
        """작업 문서 (x, y)가 든 칸의 사각형 (편집 범위 안만). 이동 도구의 대상."""
        if not self.view.in_edit(x, y):
            return None
        ch, cw = self.grid.cell_h, self.grid.cell_w
        oy, ox = self.origin
        y0 = (y + oy) // ch * ch - oy
        x0 = (x + ox) // cw * cw - ox
        return y0, y0 + ch, x0, x0 + cw

    def _move_preview(self, dx: int, dy: int) -> None:
        st = self._stroke
        y0, y1, x0, x1 = st["box"]
        block, mblock = pt.shift_block(st["block"], st["mblock"], dx, dy)
        self.doc.arr[y0:y1, x0:x1] = block
        if mblock is not None:
            self.doc.mask[y0:y1, x0:x1] = mblock
        st["rec"].add(st["box"])
        self.view.update()

    def _target_cell(self) -> tuple[int, int, int, int]:
        """마우스가 있는 칸, 없으면 편집 범위 첫 칸."""
        if self.view.hover and self.cell_box_at(*self.view.hover):
            return self.cell_box_at(*self.view.hover)
        return self.cell_box_at(self.edit_box[2], self.edit_box[0])

    def flip_cell(self) -> None:
        """H: 마우스가 있는 칸의 그림을 좌우 반전 (마스크도 같이) — 실행 취소 한 단계."""
        if self._stroke is not None:
            return
        y0, y1, x0, x1 = self._target_cell()
        rec = pt.PixelRecorder(self.doc)
        self.doc.arr[y0:y1, x0:x1] = self.doc.arr[y0:y1, x0:x1][:, ::-1].copy()
        if self.doc.has_mask:
            self.doc.mask[y0:y1, x0:x1] = self.doc.mask[y0:y1, x0:x1][:, ::-1].copy()
        rec.add((y0, y1, x0, x1))
        edit = rec.finish("edit.cell_flip")
        if edit is not None:
            self.history.do(self.doc, edit)
            self.changed = True
        self.view.update()
        self._update_undo_buttons()

    def palette_colors(self) -> list:
        """편집 범위에서 많이 쓴 색 (최대 16)."""
        return pt.palette(self.doc.arr, self._mask(), self.edit_box, 16)

    def set_slot_color(self, rgb, idx) -> None:
        self.colors[self.slot] = (tuple(rgb), idx if self.doc.has_mask else None)
        self._save_slots()
        self._refresh_colors()

    def _show_palette(self) -> None:
        menu = QMenu(self)
        cols = self.palette_colors()
        if not cols:
            menu.addAction(tr("dlg.celledit.palette_empty")).setEnabled(False)
        for rgb, idx, n in cols:
            pm = QPixmap(16, 16)
            pm.fill(QColor(*rgb))
            text = "#%02X%02X%02X" % rgb + (f"  · {tr(f'mask.idx{idx}')}" if idx is not None else "") + f"  ({n})"
            act = menu.addAction(QIcon(pm), text)
            act.triggered.connect(lambda _c=False, rgb=rgb, idx=idx: self.set_slot_color(rgb, idx))
        menu.exec(self.btn_palette.mapToGlobal(self.btn_palette.rect().bottomLeft()))

    def _on_show_mask(self, on: bool) -> None:
        self.view.show_mask = bool(on)
        self.view.update()

    def nudge(self, dx: int, dy: int) -> None:
        """방향키: 이동 도구일 때 마우스가 있는 칸(없으면 편집 범위 첫 칸)을 dx, dy 만큼."""
        if self.tool != TOOL_MOVE or self._stroke is not None:
            return
        hx, hy = self.view.hover if self.view.hover else (self.edit_box[2], self.edit_box[0])
        box = self.cell_box_at(hx, hy) or self.cell_box_at(self.edit_box[2], self.edit_box[0])
        y0, y1, x0, x1 = box
        self._stroke = {"tool": TOOL_MOVE, "rec": pt.PixelRecorder(self.doc), "box": box, "start": (0, 0),
                        "block": self.doc.arr[y0:y1, x0:x1].copy(),
                        "mblock": self.doc.mask[y0:y1, x0:x1].copy() if self.doc.has_mask else None}
        self._move_preview(dx, dy)
        self._on_release(0, 0)

    def _on_release(self, x: int, y: int) -> None:
        st, self._stroke = self._stroke, None
        if st is None or st["tool"] == TOOL_PICK:
            self._refresh_tool_state()
            return
        edit = st["rec"].finish({TOOL_MOVE: "edit.cell_move", TOOL_FILL: "edit.cell_fill"}.get(st["tool"], "edit.cell_edit"))
        if edit is not None:
            # 이미 작업 문서에 반영돼 있으므로 기록만 남긴다 (History.do는 다시 적용해도 같은 값)
            self.history.do(self.doc, edit)
            self.changed = True
        self._update_undo_buttons()

    def _on_hover(self, x: int, y: int) -> None:
        if x < 0:
            self.lb_hover.setText("")
            return
        rgba, idx = pt.pick(self.doc.arr, self._mask(), x, y)
        sx, sy = x + self.origin[1], y + self.origin[0]
        text = tr("dlg.celledit.hover", x=sx, y=sy, color="#%02X%02X%02X" % rgba[:3], a=rgba[3])
        if idx is not None:
            text += tr("dlg.celledit.state_index", index=idx)
        self.lb_hover.setText(text)

    # -- 실행 취소 / 적용 ------------------------------------------------------
    def undo(self) -> None:
        if self.history.undo(self.doc) is not None:
            self.view.update()
        self._update_undo_buttons()

    def redo(self) -> None:
        if self.history.redo(self.doc) is not None:
            self.view.update()
        self._update_undo_buttons()

    def _update_undo_buttons(self) -> None:
        self.btn_undo.setEnabled(self.history.can_undo)
        self.btn_redo.setEnabled(self.history.can_redo)

    def is_modified(self) -> bool:
        return not self.history.is_clean()

    def apply(self) -> None:
        ey0, ey1, ex0, ex1 = self.edit_box
        self.result_block = self.doc.arr[ey0:ey1, ex0:ex1].copy()
        self.result_mask = self.doc.mask[ey0:ey1, ex0:ex1].copy() if self.doc.has_mask else None
        self.changed = self.is_modified()
        self.accept()

    def reject(self) -> None:
        if self.is_modified():
            box = QMessageBox(QMessageBox.Question, tr("dlg.celledit.discard_title"),
                              tr("dlg.celledit.discard_msg"), parent=self)
            b_discard = box.addButton(tr("dlg.celledit.discard_ok"), QMessageBox.DestructiveRole)
            b_cancel = box.addButton(tr("btn.cancel"), QMessageBox.RejectRole)
            box.setDefaultButton(b_cancel)
            box.exec()
            if box.clickedButton() is not b_discard:
                return
        self.result_block = self.result_mask = None
        self.changed = False
        super().reject()

    def closeEvent(self, e) -> None:
        if self.result_block is None and self.is_modified():
            e.ignore()
            self.reject()
            return
        super().closeEvent(e)


def _rect_label(rect: CellRect) -> str:
    if rect.r0 == rect.r1 and rect.c0 == rect.c1:
        return f"R{rect.r0 + 1}C{rect.c0 + 1}"
    return f"R{rect.r0 + 1}C{rect.c0 + 1}–R{rect.r1 + 1}C{rect.c1 + 1}"
