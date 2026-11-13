"""
canvas_widget.py
------------------
메인 캔버스 (기획서 §4.1): 줌(100~800%, 정수 배율 = **Nearest**) / 팬 / 그리드 오버레이 /
셀 선택(Source·Target·다중 셀) / 배경(투명 체크무늬·흰·검·도트 격자) /
참조 레이어(Base Body 등, 읽기 전용·색조 표시) / 편집 레이어 윤곽선.

- 보간 없음: QPainter.drawImage를 정수 배율 + SmoothPixmapTransform 꺼짐으로 그린다.
- 화면에 보이는 부분만 그린다 (800%에서도 가볍다).
- 시트 배열은 SheetModel.arr 를 QImage가 **메모리를 공유**해서 본다. 편집 후에는 update()만 부르면 된다.
- 열/행 머리글(가로 C1 C2 C3 / 세로 R1 R2 …)은 스크롤해도 화면 가장자리에 고정된다.

조작: 클릭(드래그) = Source 선택 · Shift/Ctrl+클릭(드래그) = Target 지정 · 휠 = 줌 ·
      휠 클릭 드래그 / Space+드래그 = 이동.
"""

from __future__ import annotations

import math

from PySide6.QtCore import QPoint, QRect, Qt, Signal
from PySide6.QtGui import (
    QBrush, QColor, QCursor, QImage, QPainter, QPen, QPixmap,
)
from PySide6.QtWidgets import QAbstractScrollArea

from layer_model import RefLayer, stack_order
from sheet_model import CellRect, SheetModel
from theme import canvas_colors

ZOOM_STEPS = (100, 200, 300, 400, 500, 600, 700, 800)
HDR_W = 38  # 세로 머리글(R1…) 폭
HDR_H = 22  # 가로 머리글(C1…) 높이
PAD = 16  # 시트 주변 여백

BG_CHECKER = "checker"
BG_WHITE = "white"
BG_BLACK = "black"
BG_DOTS = "dots"
BACKGROUND_MODES = (BG_CHECKER, BG_WHITE, BG_BLACK, BG_DOTS)


def numpy_to_qimage(arr) -> QImage:
    """(H,W,4) uint8 RGBA 배열 → QImage (메모리 공유, arr를 살려 두어야 함)."""
    h, w = arr.shape[:2]
    return QImage(arr.data, w, h, w * 4, QImage.Format_RGBA8888)


class CanvasWidget(QAbstractScrollArea):
    selectionChanged = Signal()
    zoomChanged = Signal(int)
    hoverPixel = Signal(int, int)  # 시트 픽셀 좌표 (밖이면 -1, -1)
    contextMenuRequested = Signal(QPoint)  # 전역 좌표
    filesDropped = Signal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMouseTracking(True)
        self.viewport().setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setAcceptDrops(True)
        self.viewport().setAcceptDrops(True)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(
            lambda pos: self.contextMenuRequested.emit(self.viewport().mapToGlobal(pos)))

        self._model: SheetModel | None = None
        self._image: QImage | None = None
        self._arr_ref = None

        self._zoom = 200
        self._bg_mode = BG_CHECKER
        self._refs: list[RefLayer] = []
        self._tint_cache: dict[tuple, QImage] = {}
        self._reference_tint = True
        self._edit_visible = True
        self._show_grid = True
        self._grid_color = QColor("#00e5ff")
        self._grid_width = 1
        self._theme = "dark"
        self._placeholder = ""
        # 2단계: 표시 전용 그림 (저장과 무관). 배열 수명은 _view_bufs 로 붙잡아 둔다.
        self._display_image: QImage | None = None  # 편집 레이어 대신 그릴 그림 (색 미리보기 / 마스크만 보기)
        self._overlay_image: QImage | None = None  # 편집 레이어 위에 겹칠 그림 (마스크 의사색 / 누락 강조)
        self._overlay_opacity = 60
        self._view_bufs: list = []

        self._source: CellRect | None = None
        self._target: CellRect | None = None
        self._drag_mode: str | None = None  # "source" | "target" | "pan"
        self._drag_anchor: tuple[int, int] | None = None
        self._pan_last = QPoint()
        self._space_down = False

        self._checker = self._make_checker()
        self.horizontalScrollBar().valueChanged.connect(lambda _v: self.viewport().update())
        self.verticalScrollBar().valueChanged.connect(lambda _v: self.viewport().update())

    # ------------------------------------------------------------------ #
    #  공개 API                                                           #
    # ------------------------------------------------------------------ #
    def set_model(self, model: SheetModel | None) -> None:
        self._model = model
        self._source = self._target = None
        self._rebuild_image()
        self._update_scrollbars()
        self.horizontalScrollBar().setValue(0)
        self.verticalScrollBar().setValue(0)
        self.viewport().update()
        self.selectionChanged.emit()

    def refresh(self) -> None:
        """배열 내용이 바뀐 뒤(편집/undo) 다시 그리기."""
        self.viewport().update()

    def grid_changed(self) -> None:
        """그리드 규격이 바뀐 뒤: 선택 영역이 범위를 벗어나면 정리."""
        if self._model is not None:
            if self._source is not None and not self._model.contains(self._source):
                self._source = None
            if self._target is not None and not self._model.contains(self._target):
                self._target = None
        self.viewport().update()
        self.selectionChanged.emit()

    def set_theme(self, theme: str) -> None:
        self._theme = theme
        self._tint_cache.clear()
        self._checker = self._make_checker()
        self.viewport().update()

    def set_placeholder(self, text: str) -> None:
        self._placeholder = text
        self.viewport().update()

    def zoom(self) -> int:
        return self._zoom

    def set_zoom(self, percent: int, anchor: QPoint | None = None) -> None:
        percent = max(ZOOM_STEPS[0], min(ZOOM_STEPS[-1], int(round(percent / 100.0)) * 100))
        if percent == self._zoom:
            return
        vp = self.viewport()
        anchor = anchor if anchor is not None else QPoint(vp.width() // 2, vp.height() // 2)
        old_s = self._scale()
        ox, oy = self._origin()
        ix = (anchor.x() - ox) / old_s
        iy = (anchor.y() - oy) / old_s
        self._zoom = percent
        self._update_scrollbars()
        s = self._scale()
        # 커서 아래 픽셀이 그대로 있도록 스크롤 보정
        if self._model is not None:
            self.horizontalScrollBar().setValue(int(HDR_W + PAD + ix * s - anchor.x()))
            self.verticalScrollBar().setValue(int(HDR_H + PAD + iy * s - anchor.y()))
        vp.update()
        self.zoomChanged.emit(self._zoom)

    def zoom_step(self, direction: int, anchor: QPoint | None = None) -> None:
        idx = min(range(len(ZOOM_STEPS)), key=lambda i: abs(ZOOM_STEPS[i] - self._zoom))
        idx = max(0, min(len(ZOOM_STEPS) - 1, idx + direction))
        self.set_zoom(ZOOM_STEPS[idx], anchor)

    def set_background(self, mode: str) -> None:
        self._bg_mode = mode if mode in BACKGROUND_MODES else BG_CHECKER
        self.viewport().update()

    def background(self) -> str:
        return self._bg_mode

    def set_mask_view(self, display, overlay, overlay_opacity: int = 60) -> None:
        """표시 전용 그림 설정. display/overlay: (H,W,4) uint8 RGBA 배열 또는 None."""
        self._view_bufs = [b for b in (display, overlay) if b is not None]
        self._display_image = numpy_to_qimage(display) if display is not None else None
        self._overlay_image = numpy_to_qimage(overlay) if overlay is not None else None
        self._overlay_opacity = max(0, min(100, int(overlay_opacity)))
        self.viewport().update()

    def set_refs(self, refs: list[RefLayer]) -> None:
        self._refs = list(refs)
        self._tint_cache.clear()
        self.viewport().update()

    def set_reference_tint(self, on: bool) -> None:
        self._reference_tint = bool(on)
        self.viewport().update()

    def set_edit_visible(self, visible: bool) -> None:
        self._edit_visible = bool(visible)
        self.viewport().update()

    def edit_visible(self) -> bool:
        return self._edit_visible

    def set_show_grid(self, show: bool) -> None:
        self._show_grid = bool(show)
        self.viewport().update()

    def show_grid(self) -> bool:
        return self._show_grid

    def set_grid_style(self, color: str, width: int) -> None:
        c = QColor(color)
        self._grid_color = c if c.isValid() else QColor("#00e5ff")
        self._grid_width = max(1, min(3, int(width)))
        self.viewport().update()

    def source(self) -> CellRect | None:
        return self._source

    def target(self) -> CellRect | None:
        return self._target

    def set_source(self, rect: CellRect | None) -> None:
        self._source = rect
        self.viewport().update()
        self.selectionChanged.emit()

    def set_target(self, rect: CellRect | None) -> None:
        self._target = rect
        self.viewport().update()
        self.selectionChanged.emit()

    # ------------------------------------------------------------------ #
    #  좌표 계산                                                          #
    # ------------------------------------------------------------------ #
    def _scale(self) -> int:
        return max(1, self._zoom // 100)

    def _content_size(self) -> tuple[int, int]:
        if self._model is None:
            return 0, 0
        s = self._scale()
        return self._model.width * s, self._model.height * s

    def _origin(self) -> tuple[int, int]:
        """시트 왼쪽 위 픽셀의 뷰포트 좌표. 시트가 뷰포트보다 작으면 가운데 정렬."""
        vp = self.viewport()
        cw, ch = self._content_size()
        avail_w = vp.width() - HDR_W
        avail_h = vp.height() - HDR_H
        if cw + 2 * PAD <= avail_w:
            ox = HDR_W + (avail_w - cw) // 2
        else:
            ox = HDR_W + PAD - self.horizontalScrollBar().value()
        if ch + 2 * PAD <= avail_h:
            oy = HDR_H + (avail_h - ch) // 2
        else:
            oy = HDR_H + PAD - self.verticalScrollBar().value()
        return ox, oy

    def _update_scrollbars(self) -> None:
        vp = self.viewport()
        cw, ch = self._content_size()
        avail_w = max(1, vp.width() - HDR_W)
        avail_h = max(1, vp.height() - HDR_H)
        hb, vb = self.horizontalScrollBar(), self.verticalScrollBar()
        hb.setRange(0, max(0, cw + 2 * PAD - avail_w))
        hb.setPageStep(avail_w)
        hb.setSingleStep(self._scale() * 10)
        vb.setRange(0, max(0, ch + 2 * PAD - avail_h))
        vb.setPageStep(avail_h)
        vb.setSingleStep(self._scale() * 10)

    def pixel_at(self, pos: QPoint) -> tuple[int, int] | None:
        if self._model is None:
            return None
        ox, oy = self._origin()
        s = self._scale()
        ix = math.floor((pos.x() - ox) / s)
        iy = math.floor((pos.y() - oy) / s)
        if 0 <= ix < self._model.width and 0 <= iy < self._model.height:
            return ix, iy
        return None

    def cell_at(self, pos: QPoint, clamp: bool = False) -> tuple[int, int] | None:
        """뷰포트 좌표 → (행, 열). clamp=True면 시트 밖도 가장 가까운 셀로."""
        if self._model is None:
            return None
        g = self._model.grid
        ox, oy = self._origin()
        s = self._scale()
        ix = math.floor((pos.x() - ox) / s)
        iy = math.floor((pos.y() - oy) / s)
        c, r = ix // g.cell_w, iy // g.cell_h
        if clamp:
            return max(0, min(g.rows - 1, r)), max(0, min(g.cols - 1, c))
        if 0 <= r < g.rows and 0 <= c < g.cols and 0 <= ix < self._model.width and 0 <= iy < self._model.height:
            return r, c
        return None

    # ------------------------------------------------------------------ #
    #  그리기                                                             #
    # ------------------------------------------------------------------ #
    def _make_checker(self) -> QBrush:
        colors = canvas_colors(self._theme)
        pm = QPixmap(16, 16)
        pm.fill(QColor(colors["checker_a"]))
        p = QPainter(pm)
        p.fillRect(8, 0, 8, 8, QColor(colors["checker_b"]))
        p.fillRect(0, 8, 8, 8, QColor(colors["checker_b"]))
        p.end()
        return QBrush(pm)

    def _rebuild_image(self) -> None:
        if self._model is None:
            self._image = None
            self._arr_ref = None
            return
        self._arr_ref = self._model.arr
        self._image = numpy_to_qimage(self._arr_ref)

    def paintEvent(self, _event) -> None:
        vp = self.viewport()
        colors = canvas_colors(self._theme)
        p = QPainter(vp)
        p.setRenderHint(QPainter.SmoothPixmapTransform, False)
        p.setRenderHint(QPainter.Antialiasing, False)
        p.fillRect(vp.rect(), QColor(colors["workspace"]))

        if self._model is None or self._image is None:
            if self._placeholder:
                p.setPen(QColor(colors["hint"]))
                p.drawText(vp.rect(), Qt.AlignCenter | Qt.TextWordWrap, self._placeholder)
            p.end()
            return

        # 모델 배열이 교체됐으면(열기/복구) QImage 다시 연결
        if self._arr_ref is not self._model.arr:
            self._rebuild_image()

        s = self._scale()
        ox, oy = self._origin()
        W, H = self._model.width, self._model.height
        sheet_rect = QRect(ox, oy, W * s, H * s)
        visible = sheet_rect.intersected(vp.rect())

        if not visible.isEmpty():
            below, above = stack_order(self._refs)
            self._paint_backdrop(p, sheet_rect, visible, s, ox, oy)
            for layer in below:
                self._paint_ref(p, layer, visible, s, ox, oy, colors)
            sx0 = (visible.left() - ox) // s
            sy0 = (visible.top() - oy) // s
            sx1 = min(W, (visible.right() - ox) // s + 1)
            sy1 = min(H, (visible.bottom() - oy) // s + 1)
            src = QRect(sx0, sy0, sx1 - sx0, sy1 - sy0)
            dst = QRect(ox + sx0 * s, oy + sy0 * s, src.width() * s, src.height() * s)
            if self._edit_visible:
                # 편집 레이어(시트 본체, 보이는 부분만). 색 미리보기·마스크만 보기 중이면 표시용 그림으로 대체
                img = self._display_image if self._display_image is not None else self._image
                p.drawImage(dst, img, src)
            if self._overlay_image is not None and self._overlay_opacity > 0:
                # 마스크 의사색 / 누락 픽셀 강조 (표시 전용, 저장 안 됨)
                p.setOpacity(self._overlay_opacity / 100.0)
                p.drawImage(dst, self._overlay_image, src)
                p.setOpacity(1.0)
            for layer in above:
                self._paint_ref(p, layer, visible, s, ox, oy, colors)

        if self._show_grid:
            self._paint_grid(p, ox, oy, s, vp.rect())
        self._paint_selection(p, ox, oy, s, colors)
        self._paint_edit_outline(p, ox, oy, s, colors)
        self._paint_headers(p, ox, oy, s, colors)
        p.end()

    def _paint_backdrop(self, p: QPainter, sheet_rect: QRect, visible: QRect, s: int,
                        ox: int, oy: int) -> None:
        mode = self._bg_mode
        if mode == BG_WHITE:
            p.fillRect(visible, QColor("#ffffff"))
        elif mode == BG_BLACK:
            p.fillRect(visible, QColor("#000000"))
        elif mode == BG_DOTS:
            p.fillRect(visible, QColor("#20202a"))
            self._paint_pixel_grid(p, ox, oy, s, visible)
        else:  # checker
            p.setBrushOrigin(ox, oy)
            p.fillRect(visible, self._checker)

    def _ref_image(self, layer: RefLayer, colors: dict) -> QImage:
        """참조 레이어 그림. 색조 표시가 켜져 있으면 테마 색조를 입힌 사본(캐시)."""
        img = layer.image
        if not self._reference_tint or img is None:
            return img
        key = (id(img), colors["ref_tint"])
        cached = self._tint_cache.get(key)
        if cached is None:
            cached = img.convertToFormat(QImage.Format_ARGB32_Premultiplied)
            tp = QPainter(cached)
            tp.setCompositionMode(QPainter.CompositionMode_SourceAtop)
            tint = QColor(colors["ref_tint"])
            tint.setAlpha(70)
            tp.fillRect(cached.rect(), tint)
            tp.end()
            self._tint_cache[key] = cached
        return cached

    def _paint_ref(self, p: QPainter, layer: RefLayer, visible: QRect, s: int, ox: int, oy: int,
                   colors: dict) -> None:
        if not layer.visible or layer.image is None or layer.opacity <= 0:
            return
        img = self._ref_image(layer, colors)
        bw, bh = img.width(), img.height()
        bx0 = max(0, (visible.left() - ox) // s)
        by0 = max(0, (visible.top() - oy) // s)
        bx1 = min(bw, (visible.right() - ox) // s + 1)
        by1 = min(bh, (visible.bottom() - oy) // s + 1)
        if bx1 <= bx0 or by1 <= by0:
            return
        src = QRect(bx0, by0, bx1 - bx0, by1 - by0)
        dst = QRect(ox + bx0 * s, oy + by0 * s, src.width() * s, src.height() * s)
        p.setOpacity(layer.opacity / 100.0)
        p.drawImage(dst, img, src)
        p.setOpacity(1.0)

    def _paint_edit_outline(self, p: QPainter, ox: int, oy: int, s: int, colors: dict) -> None:
        """편집 레이어(=저장 대상 시트)를 둘러싸는 굵은 윤곽선. 참조 레이어에는 없다."""
        W, H = self._model.width, self._model.height
        rect = QRect(ox - 3, oy - 3, W * s + 6, H * s + 6)
        pen = QPen(QColor(colors["edit_outline"]), 3, Qt.SolidLine if self._edit_visible else Qt.DashLine)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        p.drawRect(rect)

    def _paint_pixel_grid(self, p: QPainter, ox: int, oy: int, s: int, visible: QRect) -> None:
        """도트 격자 배경: 배율이 높으면 픽셀마다, 낮으면 10픽셀마다 선."""
        step = 1 if s >= 4 else 10
        color = QColor(255, 255, 255, 38 if step == 1 else 30)
        p.setPen(QPen(color, 1))
        x0 = ((visible.left() - ox) // (s * step)) * s * step + ox
        x = x0
        while x <= visible.right():
            p.drawLine(x, visible.top(), x, visible.bottom())
            x += s * step
        y0 = ((visible.top() - oy) // (s * step)) * s * step + oy
        y = y0
        while y <= visible.bottom():
            p.drawLine(visible.left(), y, visible.right(), y)
            y += s * step

    def _paint_grid(self, p: QPainter, ox: int, oy: int, s: int, clip: QRect) -> None:
        g = self._model.grid
        pen = QPen(self._grid_color, self._grid_width)
        pen.setCosmetic(True)
        p.setPen(pen)
        top, bottom = oy, oy + g.sheet_h * s
        left, right = ox, ox + g.sheet_w * s
        for c in range(g.cols + 1):
            x = ox + c * g.cell_w * s
            if clip.left() - 2 <= x <= clip.right() + 2:
                p.drawLine(x, max(top, clip.top()), x, min(bottom, clip.bottom()))
        for r in range(g.rows + 1):
            y = oy + r * g.cell_h * s
            if clip.top() - 2 <= y <= clip.bottom() + 2:
                p.drawLine(max(left, clip.left()), y, min(right, clip.right()), y)

    def _cell_rect_px(self, rect: CellRect, ox: int, oy: int, s: int) -> QRect:
        g = self._model.grid
        return QRect(ox + rect.c0 * g.cell_w * s, oy + rect.r0 * g.cell_h * s,
                     rect.cols * g.cell_w * s, rect.rows * g.cell_h * s)

    def _paint_selection(self, p: QPainter, ox: int, oy: int, s: int, colors: dict) -> None:
        if self._target is not None and self._model.contains(self._target):
            r = self._cell_rect_px(self._target, ox, oy, s)
            fill = QColor(colors["target"])
            fill.setAlpha(40)
            p.fillRect(r, fill)
            pen = QPen(QColor(colors["target"]), 2, Qt.DashLine)
            p.setPen(pen)
            p.setBrush(Qt.NoBrush)
            p.drawRect(r.adjusted(1, 1, -1, -1))
        if self._source is not None and self._model.contains(self._source):
            r = self._cell_rect_px(self._source, ox, oy, s)
            fill = QColor(colors["source"])
            fill.setAlpha(36)
            p.fillRect(r, fill)
            p.setPen(QPen(QColor(colors["source"]), 2, Qt.SolidLine))
            p.setBrush(Qt.NoBrush)
            p.drawRect(r.adjusted(1, 1, -1, -1))

    def _paint_headers(self, p: QPainter, ox: int, oy: int, s: int, colors: dict) -> None:
        vp = self.viewport()
        g = self._model.grid
        bg = QColor(colors["header_bg"])
        fg = QColor(colors["header_text"])
        p.fillRect(0, 0, vp.width(), HDR_H, bg)
        p.fillRect(0, 0, HDR_W, vp.height(), bg)
        p.setPen(fg)
        font = p.font()
        font.setPointSize(8)
        p.setFont(font)
        for c in range(g.cols):
            x0 = ox + c * g.cell_w * s
            rect = QRect(x0, 0, g.cell_w * s, HDR_H)
            if rect.right() < HDR_W or rect.left() > vp.width():
                continue
            active = self._source is not None and self._source.c0 <= c <= self._source.c1
            p.setPen(QColor(colors["source"]) if active else fg)
            p.drawText(rect.intersected(QRect(HDR_W, 0, vp.width(), HDR_H)), Qt.AlignCenter, f"C{c + 1}")
        for r in range(g.rows):
            y0 = oy + r * g.cell_h * s
            rect = QRect(0, y0, HDR_W, g.cell_h * s)
            if rect.bottom() < HDR_H or rect.top() > vp.height():
                continue
            active = self._source is not None and self._source.r0 <= r <= self._source.r1
            p.setPen(QColor(colors["source"]) if active else fg)
            p.drawText(rect.intersected(QRect(0, HDR_H, HDR_W, vp.height())), Qt.AlignCenter, f"R{r + 1}")
        p.setPen(QPen(QColor(colors["header_text"]), 1))
        p.drawLine(HDR_W, 0, HDR_W, vp.height())
        p.drawLine(0, HDR_H, vp.width(), HDR_H)

    # ------------------------------------------------------------------ #
    #  이벤트                                                             #
    # ------------------------------------------------------------------ #
    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._update_scrollbars()

    def wheelEvent(self, event) -> None:
        if self._model is None:
            return
        delta = event.angleDelta().y()
        if delta:
            self.zoom_step(1 if delta > 0 else -1, event.position().toPoint())
        event.accept()

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key_Space and not event.isAutoRepeat():
            self._space_down = True
            self.viewport().setCursor(QCursor(Qt.OpenHandCursor))
            event.accept()
            return
        super().keyPressEvent(event)

    def keyReleaseEvent(self, event) -> None:
        if event.key() == Qt.Key_Space and not event.isAutoRepeat():
            self._space_down = False
            self.viewport().unsetCursor()
            event.accept()
            return
        super().keyReleaseEvent(event)

    def mousePressEvent(self, event) -> None:
        self.setFocus()
        if self._model is None:
            return
        pos = event.position().toPoint()
        if event.button() == Qt.MiddleButton or (event.button() == Qt.LeftButton and self._space_down):
            self._drag_mode = "pan"
            self._pan_last = pos
            self.viewport().setCursor(QCursor(Qt.ClosedHandCursor))
            return
        if event.button() != Qt.LeftButton:
            return
        # 머리글 영역은 무시
        if pos.x() < HDR_W or pos.y() < HDR_H:
            return
        cell = self.cell_at(pos)
        if cell is None:
            return
        if event.modifiers() & (Qt.ShiftModifier | Qt.ControlModifier):
            self._drag_mode = "target"
            self._drag_anchor = cell
            self.set_target(CellRect.single(*cell))
        else:
            self._drag_mode = "source"
            self._drag_anchor = cell
            self.set_source(CellRect.single(*cell))

    def mouseMoveEvent(self, event) -> None:
        pos = event.position().toPoint()
        if self._model is not None:
            px = self.pixel_at(pos)
            self.hoverPixel.emit(*(px if px else (-1, -1)))
        if self._drag_mode == "pan":
            d = pos - self._pan_last
            self._pan_last = pos
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - d.x())
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() - d.y())
            return
        if self._drag_mode in ("source", "target") and self._drag_anchor is not None:
            cell = self.cell_at(pos, clamp=True)
            if cell is None:
                return
            rect = CellRect.from_cells(self._drag_anchor, cell)
            if self._drag_mode == "source":
                if rect != self._source:
                    self.set_source(rect)
            elif rect != self._target:
                self.set_target(rect)

    def mouseReleaseEvent(self, _event) -> None:
        if self._drag_mode == "pan":
            if self._space_down:
                self.viewport().setCursor(QCursor(Qt.OpenHandCursor))
            else:
                self.viewport().unsetCursor()
        self._drag_mode = None
        self._drag_anchor = None

    def leaveEvent(self, event) -> None:
        self.hoverPixel.emit(-1, -1)
        super().leaveEvent(event)

    # -- 파일 끌어다 놓기 -----------------------------------------------------
    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dragMoveEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:
        paths = [u.toLocalFile() for u in event.mimeData().urls() if u.isLocalFile()]
        if paths:
            self.filesDropped.emit(paths)
            event.acceptProposedAction()
