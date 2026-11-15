"""
cell_editor.py
----------------
4단계(v0.4.0) **셀 편집 창** — 선택한 셀(다중 셀이면 그 범위 전체)을 팝업으로 크게 띄우고
도트를 칠하기(B) / 지우기(E) / 스포이트(I) 한다. 기획서 EDT-ETC.6 §4.2 (추가 8).

- 같은 범위로 자른 Base Body·참조 레이어를 아래(또는 위)에 깔아 배경에 맞춰 작업한다.
- 범위 바깥 이웃 셀은 반투명으로 보여준다(읽기 전용). 표시 여부·불투명도는 환경설정 `cell_edit.*`.
- 칠하기·지우기 중 **Alt를 누르고 있는 동안 임시 스포이트**, `[` `]` 브러시 크기, `X` 주/보조 색 교체.
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
    QBrush, QColor, QCursor, QImage, QKeySequence, QPainter, QPen, QPixmap, QPolygon, QShortcut,
)
from PySide6.QtWidgets import (
    QCheckBox, QColorDialog, QComboBox, QDialog, QHBoxLayout, QLabel, QMessageBox,
    QPushButton, QScrollArea, QSpinBox, QToolButton, QVBoxLayout, QWidget,
)

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

    pressed = Signal(int, int, bool)  # x, y, alt
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
        if self.hover is not None and self.tool != TOOL_PICK:
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
        if e.button() != Qt.LeftButton:
            return
        px = self.pixel_at(e.position().toPoint())
        if px is None:
            return
        self._drag = True
        self.pressed.emit(px[0], px[1], bool(e.modifiers() & Qt.AltModifier))

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
        if e.button() != Qt.LeftButton or not self._drag:
            return
        self._drag = False
        cp = self.pixel_at(e.position().toPoint(), clamp=True)
        self.released.emit(cp[0], cp[1])

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
        self.colors = [((59, 76, 117), 1 if self.doc.has_mask else None),
                       ((255, 255, 255), 1 if self.doc.has_mask else None)]  # (rgb, 마스크 인덱스)
        self._stroke: dict | None = None

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
        for b in (self.btn_paint, self.btn_erase, self.btn_pick):
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
        self.btn_c1 = QPushButton()
        self.btn_c2 = QPushButton()
        for i, b in enumerate((self.btn_c1, self.btn_c2)):
            b.setFixedSize(34, 26) if i == 0 else b.setFixedSize(26, 20)
            b.setFocusPolicy(Qt.NoFocus)
            b.clicked.connect(lambda _c=False, i=i: self._choose_color(i))
        bar.addWidget(self.btn_c1)
        bar.addWidget(self.btn_c2)
        self.btn_swap = QToolButton()
        self.btn_swap.setText("⇄")
        self.btn_swap.setToolTip(tr("dlg.celledit.swap"))
        self.btn_swap.setFocusPolicy(Qt.NoFocus)
        self.btn_swap.clicked.connect(self._swap_colors)
        bar.addWidget(self.btn_swap)
        self.lb_index = QLabel(tr("dlg.celledit.index"))
        self.cb_index = QComboBox()
        self.cb_index.setFocusPolicy(Qt.NoFocus)
        for n in range(5):
            self.cb_index.addItem(tr(f"mask.idx{n}"), n)
        self.cb_index.activated.connect(self._on_index)
        bar.addWidget(self.lb_index)
        bar.addWidget(self.cb_index)
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
        for seq, fn in (("B", lambda: self._set_tool(TOOL_PAINT)), ("E", lambda: self._set_tool(TOOL_ERASE)),
                        ("I", lambda: self._set_tool(TOOL_PICK)), ("X", self._swap_colors),
                        ("[", lambda: self.sp_brush.setValue(self.sp_brush.value() - 1)),
                        ("]", lambda: self.sp_brush.setValue(self.sp_brush.value() + 1)),
                        ("Ctrl+Z", self.undo), ("Ctrl+Y", self.redo), ("Ctrl+Shift+Z", self.redo),
                        ("Ctrl+Return", self.apply), ("Ctrl+Enter", self.apply)):
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
        self._refresh_tool_state()

    def _refresh_tool_state(self) -> None:
        tool = self.effective_tool()
        self.view.tool = tool
        self.view.brush = self.sp_brush.value()
        if tool == TOOL_PICK:
            self.view.setCursor(make_pick_cursor())
        else:
            self.view.setCursor(make_square_cursor(self.view.brush * self.view.scale, tool == TOOL_ERASE))
        rgb, idx = self.colors[0]
        hexc = "#%02X%02X%02X" % rgb
        if tool == TOOL_PICK:
            key = "dlg.celledit.state_pick_temp" if self.tool != TOOL_PICK else "dlg.celledit.state_pick"
            text = tr(key, back=tr(f"dlg.celledit.state_name_{self.tool}"))
        elif tool == TOOL_ERASE:
            text = tr("dlg.celledit.state_erase", size=self.view.brush)
        else:
            text = tr("dlg.celledit.state_paint", size=self.view.brush, color=hexc)
            if self.doc.has_mask and idx is not None:
                text += tr("dlg.celledit.state_index", index=idx)
        self.lb_state.setText(text)
        self.view.update()

    def _on_brush(self, v: int) -> None:
        self.config.set("cell_edit.brush", int(v))
        self._refresh_tool_state()

    def _refresh_colors(self) -> None:
        for b, (rgb, _i) in zip((self.btn_c1, self.btn_c2), self.colors):
            b.setStyleSheet("background-color: #%02X%02X%02X; border: 1px solid #888;" % rgb)
        idx = self.colors[0][1]
        if idx is not None:
            self.cb_index.setCurrentIndex(self.cb_index.findData(idx))
        self._refresh_tool_state()

    def _choose_color(self, i: int) -> None:
        rgb, idx = self.colors[i]
        c = QColorDialog.getColor(QColor(*rgb), self, tr("dlg.celledit.choose_color"))
        if c.isValid():
            self.colors[i] = ((c.red(), c.green(), c.blue()), idx)
            self._refresh_colors()

    def _swap_colors(self) -> None:
        self.colors.reverse()
        self._refresh_colors()

    def _on_index(self, _i: int) -> None:
        rgb, _old = self.colors[0]
        self.colors[0] = (rgb, int(self.cb_index.currentData()))
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
        self.colors[0] = (rgba[:3], idx if self.doc.has_mask else None)
        self._refresh_colors()

    def _on_press(self, x: int, y: int, alt: bool) -> None:
        tool = TOOL_PICK if alt else self.effective_tool()  # Alt+클릭도 스포이트
        if tool == TOOL_PICK:
            self._pick_at(x, y)
            self._stroke = {"tool": TOOL_PICK}
            return
        rec = pt.PixelRecorder(self.doc)
        self._stroke = {"tool": tool, "rec": rec, "last": (x, y)}
        self._apply_segment((x, y), (x, y))

    def _apply_segment(self, p0, p1) -> None:
        st = self._stroke
        size = self.sp_brush.value()
        if st["tool"] == TOOL_ERASE:
            d = pt.erase_line(self.doc.arr, self._mask(), p0, p1, size, self._bounds())
        else:
            rgb, idx = self.colors[0]
            d = pt.paint_line(self.doc.arr, self._mask(), p0, p1, size, rgb, idx, self._bounds())
        st["rec"].add(d)
        st["last"] = p1
        if d is not None:
            self.view.update()

    def _on_move(self, x: int, y: int, alt: bool) -> None:
        st = self._stroke
        if st is None:
            return
        if st["tool"] == TOOL_PICK:
            self._pick_at(x, y)
            return
        self._apply_segment(st["last"], (x, y))

    def _on_release(self, x: int, y: int) -> None:
        st, self._stroke = self._stroke, None
        if st is None or st["tool"] == TOOL_PICK:
            self._refresh_tool_state()
            return
        edit = st["rec"].finish("edit.cell_edit")
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
            ans = QMessageBox.question(self, tr("dlg.celledit.discard_title"), tr("dlg.celledit.discard_msg"),
                                       QMessageBox.Discard | QMessageBox.Cancel, QMessageBox.Cancel)
            if ans != QMessageBox.Discard:
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
