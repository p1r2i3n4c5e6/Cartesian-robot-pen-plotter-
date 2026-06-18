"""Drawing canvas (graphics view + scene).

Optimised so the grid / rulers / origin are painted via
``drawBackground`` instead of being individual scene items — a huge
speedup on the Raspberry Pi 4.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from PyQt5.QtCore import QLineF, QPointF, QRectF, Qt, QTimer, pyqtSignal
from PyQt5.QtGui import (
    QBrush, QColor, QFont, QImage, QPainter, QPainterPath, QPen, QPixmap,
)
from PyQt5.QtWidgets import (
    QDialog, QGraphicsEllipseItem, QGraphicsItem, QGraphicsItemGroup,
    QGraphicsLineItem, QGraphicsPathItem, QGraphicsPixmapItem,
    QGraphicsRectItem, QGraphicsScene, QGraphicsTextItem, QGraphicsView,
)

from .config import IS_RPI, PEN_PRESETS
from .text_dialog import TextPropertiesDialog


class DrawingCanvas(QGraphicsView):
    mouse_position_changed = pyqtSignal(float, float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.scene = QGraphicsScene(self)
        self.setScene(self.scene)
        self.plot_width_mm = 200
        self.plot_height_mm = 150
        self.px_per_mm = 4.0
        self._update_canvas_size()
        self.setBackgroundBrush(QBrush(QColor(45, 48, 55)))

        self.drawing = False
        self.current_path: Optional[QPainterPath] = None
        self.current_path_item: Optional[QGraphicsPathItem] = None
        self.last_point = QPointF()
        self.pen_color = QColor(0, 0, 0)
        self.pen_width = 2
        self.current_tool = "pen"
        self.strokes = []
        self.temp_item = None
        self.start_point: Optional[QPointF] = None

        if IS_RPI:
            self.setRenderHint(QPainter.Antialiasing, False)
            self.setRenderHint(QPainter.SmoothPixmapTransform, False)
        else:
            self.setRenderHint(QPainter.Antialiasing)
            self.setRenderHint(QPainter.SmoothPixmapTransform)
            self.setRenderHint(QPainter.TextAntialiasing)

        self.setViewportUpdateMode(QGraphicsView.SmartViewportUpdate)
        self.setOptimizationFlag(QGraphicsView.DontAdjustForAntialiasing, True)
        self.setCacheMode(QGraphicsView.CacheBackground)
        self.setDragMode(QGraphicsView.NoDrag)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setMouseTracking(True)

        self.show_grid = True
        self.show_axes = True
        self.show_rulers = True
        self.show_origin = True
        self.show_frame = True

        # Animated live machine cursor (updated from the controller's
        # WPos / MPos status reports while a job is running).
        self._machine_cursor: Optional[QGraphicsItemGroup] = None
        self._cursor_label: Optional[QGraphicsTextItem] = None
        self._cursor_ring: Optional[QGraphicsEllipseItem] = None
        self._machine_pen_down = False
        # Smooth interpolation between status reports.  GRBL ships them
        # at ~4 Hz; we tween at 30 Hz so the dot glides instead of
        # teleporting between samples.
        self._cursor_target = QPointF(0, 0)
        self._cursor_have_target = False
        self._cursor_timer = QTimer(self)
        self._cursor_timer.setInterval(33)        # ~30 fps
        self._cursor_timer.timeout.connect(self._tween_cursor)
        self._build_machine_cursor()
        self.set_machine_cursor_visible(False)

        # Live activity banner that floats above the machine cursor —
        # shows messages like "Picking up Red pen" / "Drawing with Blue"
        # so the user can see what the head is doing without watching
        # the console.  Updated via ``set_activity_text``.
        self._activity_banner: Optional[QGraphicsTextItem] = None
        self._build_activity_banner()

        # Visual pen rack — coloured circles at each saved slot
        # position, drawn directly on the scene so the user can see
        # WHERE on the work area each pen lives.  Populated by
        # ``update_pen_rack`` whenever the PenChangerPanel emits a
        # ``settings_changed`` signal.
        self._pen_rack_group: Optional[QGraphicsItemGroup] = None
        self._pen_rack_items: dict = {}
        self._build_pen_rack_layer()

        # Top-margin colour palette strip — shows every available pen
        # in PEN_PRESETS as a coloured swatch with its tool number.
        # The currently-active pen is highlighted with a yellow border
        # while a job is streaming so the user knows which pen the
        # plotter is supposed to be holding right now.
        self._pen_strip_group: Optional[QGraphicsItemGroup] = None
        self._pen_strip_items: dict = {}
        self._active_pen_tool: Optional[int] = None
        self._build_pen_strip()

    # ------------------------------------------------------------------
    def _update_canvas_size(self) -> None:
        margin_mm = 30
        self.margin_px = margin_mm * self.px_per_mm
        self.plot_width_px = self.plot_width_mm * self.px_per_mm
        self.plot_height_px = self.plot_height_mm * self.px_per_mm
        self.scene_width = self.plot_width_px + 2 * self.margin_px
        self.scene_height = self.plot_height_px + 2 * self.margin_px
        self.origin_x = self.margin_px
        self.origin_y = self.margin_px + self.plot_height_px
        if hasattr(self, "scene") and self.scene is not None:
            self.scene.setSceneRect(0, 0, self.scene_width, self.scene_height)
            self.resetCachedContent()

    def set_plot_size(self, w: float, h: float) -> None:
        self.plot_width_mm = w
        self.plot_height_mm = h
        self._update_canvas_size()
        self.viewport().update()

    # ------------------------------------------------------------------
    # Background painting
    # ------------------------------------------------------------------
    def drawBackground(self, painter: QPainter, rect: QRectF) -> None:  # noqa: N802
        super().drawBackground(painter, rect)
        plot = QRectF(self.origin_x, self.margin_px,
                      self.plot_width_px, self.plot_height_px)
        painter.fillRect(plot, QColor(255, 255, 255))

        if self.show_grid:
            self._paint_grid(painter)
        if self.show_frame:
            self._paint_frame(painter)
        if self.show_axes:
            self._paint_axes(painter)
        if self.show_rulers:
            self._paint_rulers(painter)
        if self.show_origin:
            self._paint_origin(painter)

    def _paint_grid(self, painter: QPainter) -> None:
        scale = self.transform().m11()
        show_minor = (self.px_per_mm * scale) >= 3.0

        if show_minor and not IS_RPI:
            painter.setPen(QPen(QColor(220, 225, 235), 0))
            for mm in range(0, int(self.plot_width_mm) + 1):
                if mm % 10 == 0:
                    continue
                x = self.origin_x + mm * self.px_per_mm
                painter.drawLine(QLineF(x, self.margin_px, x, self.origin_y))
            for mm in range(0, int(self.plot_height_mm) + 1):
                if mm % 10 == 0:
                    continue
                y = self.origin_y - mm * self.px_per_mm
                painter.drawLine(QLineF(self.origin_x, y,
                                        self.origin_x + self.plot_width_px, y))

        painter.setPen(QPen(QColor(180, 190, 210), 0))
        for mm in range(0, int(self.plot_width_mm) + 1, 10):
            if mm % 50 == 0:
                continue
            x = self.origin_x + mm * self.px_per_mm
            painter.drawLine(QLineF(x, self.margin_px, x, self.origin_y))
        for mm in range(0, int(self.plot_height_mm) + 1, 10):
            if mm % 50 == 0:
                continue
            y = self.origin_y - mm * self.px_per_mm
            painter.drawLine(QLineF(self.origin_x, y,
                                    self.origin_x + self.plot_width_px, y))

        painter.setPen(QPen(QColor(140, 155, 180), 0))
        for mm in range(0, int(self.plot_width_mm) + 1, 50):
            x = self.origin_x + mm * self.px_per_mm
            painter.drawLine(QLineF(x, self.margin_px, x, self.origin_y))
        for mm in range(0, int(self.plot_height_mm) + 1, 50):
            y = self.origin_y - mm * self.px_per_mm
            painter.drawLine(QLineF(self.origin_x, y,
                                    self.origin_x + self.plot_width_px, y))

    def _paint_frame(self, painter: QPainter) -> None:
        painter.setPen(QPen(QColor(30, 144, 255), 2.0))
        painter.setBrush(Qt.NoBrush)
        painter.drawRect(QRectF(self.origin_x, self.margin_px,
                                self.plot_width_px, self.plot_height_px))
        size = 6
        painter.setBrush(QColor(30, 144, 255))
        for cx, cy in (
            (self.origin_x, self.margin_px),
            (self.origin_x + self.plot_width_px, self.margin_px),
            (self.origin_x, self.origin_y),
            (self.origin_x + self.plot_width_px, self.origin_y),
        ):
            painter.drawRect(
                QRectF(cx - size / 2, cy - size / 2, size, size)
            )

    def _paint_axes(self, painter: QPainter) -> None:
        painter.setPen(QPen(QColor(220, 50, 50), 1.5))
        x_end = self.origin_x + self.plot_width_px + 20
        painter.drawLine(QLineF(self.origin_x, self.origin_y,
                                x_end, self.origin_y))
        arrow = QPainterPath()
        arrow.moveTo(x_end, self.origin_y)
        arrow.lineTo(x_end - 8, self.origin_y - 4)
        arrow.lineTo(x_end - 8, self.origin_y + 4)
        arrow.closeSubpath()
        painter.fillPath(arrow, QColor(220, 50, 50))
        painter.setFont(QFont("Arial", 10, QFont.Bold))
        painter.drawText(QPointF(x_end + 4, self.origin_y - 4), "X+")

        painter.setPen(QPen(QColor(50, 180, 50), 1.5))
        y_end = self.origin_y - self.plot_height_px - 20
        painter.drawLine(QLineF(self.origin_x, self.origin_y,
                                self.origin_x, y_end))
        arrow = QPainterPath()
        arrow.moveTo(self.origin_x, y_end)
        arrow.lineTo(self.origin_x - 4, y_end + 8)
        arrow.lineTo(self.origin_x + 4, y_end + 8)
        arrow.closeSubpath()
        painter.fillPath(arrow, QColor(50, 180, 50))
        painter.drawText(QPointF(self.origin_x - 18, y_end - 2), "Y+")

    def _paint_rulers(self, painter: QPainter) -> None:
        painter.setPen(QPen(QColor(150, 165, 195), 0.8))
        painter.setFont(QFont("Arial", 7))
        step = 10 if self.plot_width_mm <= 300 else 20
        ruler_y = self.origin_y + 4
        for mm in range(0, int(self.plot_width_mm) + 1, step):
            x = self.origin_x + mm * self.px_per_mm
            painter.drawLine(QLineF(x, ruler_y, x, ruler_y + 5))
            painter.drawText(QPointF(x - 6, ruler_y + 16), str(mm))
        ruler_x = self.origin_x - 4
        for mm in range(0, int(self.plot_height_mm) + 1, step):
            y = self.origin_y - mm * self.px_per_mm
            painter.drawLine(QLineF(ruler_x - 5, y, ruler_x, y))
            painter.drawText(QPointF(ruler_x - 22, y + 3), str(mm))
        painter.drawText(QPointF(self.origin_x - 25, self.origin_y + 22),
                         "[mm]")

    def _paint_origin(self, painter: QPainter) -> None:
        radius = 10
        painter.setPen(QPen(QColor(255, 140, 0), 1.5))
        painter.setBrush(QColor(255, 140, 0, 80))
        painter.drawEllipse(QPointF(self.origin_x, self.origin_y), radius, radius)
        painter.setPen(QPen(QColor(255, 140, 0), 1.2))
        painter.drawLine(QLineF(self.origin_x - radius - 3, self.origin_y,
                                self.origin_x + radius + 3, self.origin_y))
        painter.drawLine(QLineF(self.origin_x, self.origin_y - radius - 3,
                                self.origin_x, self.origin_y + radius + 3))
        painter.setFont(QFont("Arial", 8, QFont.Bold))
        painter.drawText(
            QPointF(self.origin_x + radius + 4, self.origin_y + radius - 1),
            "ORIGIN (0,0)"
        )

    # ------------------------------------------------------------------
    # Coordinate helpers
    # ------------------------------------------------------------------
    def scene_to_mm(self, scene_pos: QPointF):
        x_mm = (scene_pos.x() - self.origin_x) / self.px_per_mm
        y_mm = (self.origin_y - scene_pos.y()) / self.px_per_mm
        return x_mm, y_mm

    def mm_to_scene(self, x_mm: float, y_mm: float) -> QPointF:
        return QPointF(self.origin_x + x_mm * self.px_per_mm,
                       self.origin_y - y_mm * self.px_per_mm)

    def is_in_plot_area(self, scene_pos: QPointF) -> bool:
        return (self.origin_x <= scene_pos.x() <= self.origin_x + self.plot_width_px
                and self.margin_px <= scene_pos.y() <= self.origin_y)

    # ------------------------------------------------------------------
    # Pen / tool API
    # ------------------------------------------------------------------
    def set_pen_color(self, color: QColor) -> None:
        self.pen_color = QColor(color)

    def set_pen_width(self, w: int) -> None:
        self.pen_width = w

    def set_tool(self, tool: str) -> None:
        self.current_tool = tool
        if tool == "pan":
            self.setDragMode(QGraphicsView.ScrollHandDrag)
            self.setCursor(Qt.OpenHandCursor)
        elif tool == "select":
            self.setDragMode(QGraphicsView.RubberBandDrag)
            self.setCursor(Qt.ArrowCursor)
        else:
            self.setDragMode(QGraphicsView.NoDrag)
            self.setCursor(Qt.CrossCursor)

    # ------------------------------------------------------------------
    # Mouse events
    # ------------------------------------------------------------------
    def mousePressEvent(self, event):  # noqa: N802
        if event.button() == Qt.LeftButton and self.current_tool not in ("pan", "select"):
            pos = self.mapToScene(event.pos())
            if (not self.is_in_plot_area(pos)
                    and self.current_tool in ("pen", "line", "rect", "circle")):
                super().mousePressEvent(event)
                return
            self.start_point = pos
            if self.current_tool == "pen":
                self.drawing = True
                self.current_path = QPainterPath()
                self.current_path.moveTo(pos)
                self.current_path_item = QGraphicsPathItem()
                pen = QPen(self.pen_color, self.pen_width, Qt.SolidLine,
                           Qt.RoundCap, Qt.RoundJoin)
                self.current_path_item.setPen(pen)
                self.current_path_item.setPath(self.current_path)
                self.current_path_item.setFlag(QGraphicsItem.ItemIsSelectable, True)
                self.scene.addItem(self.current_path_item)
                self.last_point = pos
            elif self.current_tool == "eraser":
                for item in self.scene.items(pos):
                    if isinstance(item, (QGraphicsPathItem, QGraphicsTextItem)):
                        if item.zValue() < 0:
                            continue
                        self.scene.removeItem(item)
                        self.strokes = [s for s in self.strokes
                                        if s.get("item") is not item]
                        break
            elif self.current_tool == "text":
                self._insert_text_at(pos)
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):  # noqa: N802
        pos = self.mapToScene(event.pos())
        x_mm, y_mm = self.scene_to_mm(pos)
        self.mouse_position_changed.emit(x_mm, y_mm)
        if self.drawing and self.current_tool == "pen":
            if self.is_in_plot_area(pos):
                dx = pos.x() - self.last_point.x()
                dy = pos.y() - self.last_point.y()
                if dx * dx + dy * dy >= 4:
                    self.current_path.lineTo(pos)
                    self.current_path_item.setPath(self.current_path)
                    self.last_point = pos
        elif (self.current_tool in ("line", "rect", "circle")
              and event.buttons() & Qt.LeftButton):
            if self.temp_item:
                self.scene.removeItem(self.temp_item)
            pen = QPen(self.pen_color, self.pen_width, Qt.SolidLine,
                       Qt.RoundCap, Qt.RoundJoin)
            path = QPainterPath()
            if self.current_tool == "line":
                path.moveTo(self.start_point)
                path.lineTo(pos)
            elif self.current_tool == "rect":
                rect = QRectF(self.start_point, pos).normalized()
                path.addRect(rect)
            elif self.current_tool == "circle":
                rect = QRectF(self.start_point, pos).normalized()
                path.addEllipse(rect)
            self.temp_item = QGraphicsPathItem(path)
            self.temp_item.setPen(pen)
            self.scene.addItem(self.temp_item)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):  # noqa: N802
        if self.drawing and self.current_tool == "pen":
            self.drawing = False
            if self.current_path_item:
                self.current_path_item.setFlag(QGraphicsItem.ItemIsMovable, True)
                self.current_path_item.setFlag(QGraphicsItem.ItemIsSelectable, True)
                self.strokes.append({
                    "type": "path",
                    "color": QColor(self.pen_color),
                    "path": QPainterPath(self.current_path),
                    "width": self.pen_width,
                    "item": self.current_path_item,
                })
            self.current_path = None
            self.current_path_item = None
        elif (self.current_tool in ("line", "rect", "circle")
              and self.temp_item is not None):
            self.temp_item.setFlag(QGraphicsItem.ItemIsMovable, True)
            self.temp_item.setFlag(QGraphicsItem.ItemIsSelectable, True)
            self.strokes.append({
                "type": self.current_tool,
                "color": QColor(self.pen_color),
                "path": self.temp_item.path(),
                "width": self.pen_width,
                "item": self.temp_item,
            })
            self.temp_item = None
        super().mouseReleaseEvent(event)

    # ------------------------------------------------------------------
    def _insert_text_at(self, pos: QPointF) -> None:
        dialog = TextPropertiesDialog(self, self.pen_color)
        if dialog.exec_() != QDialog.Accepted:
            return
        props = dialog.get_properties()
        if not props["text"]:
            return
        text_item = QGraphicsTextItem(props["text"])
        text_item.setDefaultTextColor(props["color"])
        text_item.setFont(props["font"])
        text_item.setPos(pos)
        text_item.setRotation(props["rotation"])
        text_item.setFlag(QGraphicsTextItem.ItemIsMovable, True)
        text_item.setFlag(QGraphicsTextItem.ItemIsSelectable, True)
        self.scene.addItem(text_item)
        self.strokes.append({
            "type": "text",
            "color": props["color"],
            "text": props["text"],
            "pos": pos,
            "font": props["font"],
            "rotation": props["rotation"],
            "letter_spacing": props["letter_spacing"],
            "item": text_item,
        })

    def wheelEvent(self, event):  # noqa: N802
        if event.modifiers() & Qt.ControlModifier:
            factor = 1.15 if event.angleDelta().y() > 0 else 1 / 1.15
            self.scale(factor, factor)
            self.resetCachedContent()
        else:
            super().wheelEvent(event)

    def keyPressEvent(self, event):  # noqa: N802
        if event.key() == Qt.Key_Delete:
            for item in self.scene.selectedItems():
                if item.zValue() >= 0:
                    self.scene.removeItem(item)
                    self.strokes = [s for s in self.strokes
                                    if s.get("item") is not item]
        super().keyPressEvent(event)

    # ------------------------------------------------------------------
    def clear_canvas(self) -> None:
        for stroke in self.strokes:
            item = stroke.get("item")
            if item is not None and item in self.scene.items():
                self.scene.removeItem(item)
        self.strokes = []

    def toggle_grid(self, show):    self._toggle("show_grid", show)
    def toggle_axes(self, show):    self._toggle("show_axes", show)
    def toggle_rulers(self, show):  self._toggle("show_rulers", show)
    def toggle_origin(self, show):  self._toggle("show_origin", show)

    def _toggle(self, attr: str, value: bool) -> None:
        setattr(self, attr, bool(value))
        self.resetCachedContent()
        self.viewport().update()

    def fit_to_view(self) -> None:
        rect = QRectF(self.margin_px / 2, self.margin_px / 2,
                      self.plot_width_px + self.margin_px,
                      self.plot_height_px + self.margin_px)
        self.fitInView(rect, Qt.KeepAspectRatio)
        self.resetCachedContent()

    # ------------------------------------------------------------------
    def import_image(self, filepath: str) -> bool:
        ext = Path(filepath).suffix.lower()
        center = self.mm_to_scene(self.plot_width_mm / 2, self.plot_height_mm / 2)
        if ext in (".png", ".jpg", ".jpeg", ".bmp", ".gif"):
            pixmap = QPixmap(filepath)
            if pixmap.isNull():
                return False
            item = QGraphicsPixmapItem(pixmap)
            item.setPos(center.x() - pixmap.width() / 2,
                        center.y() - pixmap.height() / 2)
            item.setFlag(QGraphicsPixmapItem.ItemIsMovable, True)
            item.setFlag(QGraphicsPixmapItem.ItemIsSelectable, True)
            self.scene.addItem(item)
            self.strokes.append({
                "type": "image", "pixmap": pixmap,
                "item": item, "filepath": filepath,
            })
            return True
        if ext == ".svg":
            try:
                from PyQt5.QtSvg import QGraphicsSvgItem
            except ImportError:
                return False
            item = QGraphicsSvgItem(filepath)
            item.setPos(center)
            item.setFlag(QGraphicsSvgItem.ItemIsMovable, True)
            item.setFlag(QGraphicsSvgItem.ItemIsSelectable, True)
            self.scene.addItem(item)
            self.strokes.append({"type": "svg", "item": item, "filepath": filepath})
            return True
        return False

    # ------------------------------------------------------------------
    # Live machine-position cursor
    # ------------------------------------------------------------------
    def _build_machine_cursor(self) -> None:
        """Build a large, easy-to-see plotter-head indicator.

        Layout (centred on the actual pen X/Y):

        * faint outer halo ring (radius 22) for visibility against
          dark / light backgrounds
        * solid medium ring (radius 14) tinted by pen state
        * thin crosshair extending to ±26 px
        * inner solid dot (radius 4) marking the exact pen-tip
        * floating text label showing the current position in mm
        """
        group = QGraphicsItemGroup()

        # Outer halo — helps the eye spot the cursor on white paper.
        halo = QGraphicsEllipseItem(-22, -22, 44, 44)
        halo.setPen(QPen(QColor(255, 220, 0, 90), 1.5))
        halo.setBrush(QBrush(QColor(255, 220, 0, 35)))
        group.addToGroup(halo)

        # Main pen-state ring (re-coloured on pen up/down).
        ring = QGraphicsEllipseItem(-14, -14, 28, 28)
        ring.setPen(QPen(QColor(70, 200, 90), 2.5))
        ring.setBrush(QBrush(QColor(70, 200, 90, 70)))
        group.addToGroup(ring)
        self._cursor_ring = ring

        # Long crosshair so the user can sight along an axis.
        for x1, y1, x2, y2 in (
            (-26, 0, -6, 0), (6, 0, 26, 0),
            (0, -26, 0, -6), (0, 6, 0, 26),
        ):
            line = QGraphicsLineItem(x1, y1, x2, y2)
            pen = QPen(QColor(40, 40, 40, 230), 1.6)
            pen.setCapStyle(Qt.RoundCap)
            line.setPen(pen)
            group.addToGroup(line)

        # Centre dot — the actual pen tip.
        dot = QGraphicsEllipseItem(-4, -4, 8, 8)
        dot.setPen(QPen(QColor(20, 20, 20), 1))
        dot.setBrush(QBrush(QColor(255, 50, 50)))
        group.addToGroup(dot)

        group.setZValue(1000)  # always on top
        group.setFlag(QGraphicsItem.ItemIsMovable, False)
        group.setFlag(QGraphicsItem.ItemIsSelectable, False)
        self.scene.addItem(group)

        # Floating label — sits to the lower-right of the cursor and
        # always shows the live coordinates so the user knows where the
        # head is even if the cursor leaves the visible viewport.
        label = QGraphicsTextItem("")
        label.setDefaultTextColor(QColor(20, 20, 20))
        label.setFont(QFont("Courier New", 8, QFont.Bold))
        label.setHtml(
            "<div style='background: rgba(255,235,120,220);"
            " padding: 1px 4px; border: 1px solid #888;'>X: 0.00&nbsp;&nbsp;Y: 0.00</div>"
        )
        label.setZValue(1001)
        label.setFlag(QGraphicsItem.ItemIsMovable, False)
        label.setFlag(QGraphicsItem.ItemIsSelectable, False)
        self.scene.addItem(label)
        self._cursor_label = label

        # Park everything at work-zero until we receive a real position.
        center = self.mm_to_scene(0, 0)
        group.setPos(center)
        label.setPos(center.x() + 26, center.y() + 6)
        self._cursor_target = center
        self._machine_cursor = group

    def set_machine_cursor_visible(self, visible: bool) -> None:
        if self._machine_cursor is not None:
            self._machine_cursor.setVisible(bool(visible))
        if self._cursor_label is not None:
            self._cursor_label.setVisible(bool(visible))

    def update_machine_cursor(self, position: dict) -> None:
        """Slot connected to ``SerialWorker.machine_position_changed``.

        Sets a *target* position; the actual on-screen cursor glides
        toward it on a 30 Hz timer so movement looks smooth even though
        GRBL only ships status updates 4-10 times per second.
        """
        if self._machine_cursor is None:
            return
        x = position.get("X")
        y = position.get("Y")
        if x is None or y is None:
            return
        self.set_machine_cursor_visible(True)
        self._cursor_target = self.mm_to_scene(float(x), float(y))
        self._cursor_have_target = True
        if not self._cursor_timer.isActive():
            self._cursor_timer.start()

        # Pen-state colour swap (green = up, red = down)
        z = position.get("Z")
        pen_down = (z is not None) and (z <= 0.5)
        if pen_down != self._machine_pen_down and self._cursor_ring is not None:
            self._machine_pen_down = pen_down
            if pen_down:
                self._cursor_ring.setPen(QPen(QColor(255, 60, 60), 2.5))
                self._cursor_ring.setBrush(QBrush(QColor(255, 60, 60, 110)))
            else:
                self._cursor_ring.setPen(QPen(QColor(70, 200, 90), 2.5))
                self._cursor_ring.setBrush(QBrush(QColor(70, 200, 90, 70)))

        # Live coordinate label
        if self._cursor_label is not None:
            state = position.get("state", "")
            self._cursor_label.setHtml(
                "<div style='background: rgba(255,235,120,230);"
                " padding: 1px 5px; border: 1px solid #555;"
                " font-family: Courier New; font-size: 9pt;"
                " font-weight: bold; color: #111;'>"
                f"X: {float(x):7.2f}&nbsp;&nbsp;Y: {float(y):7.2f}"
                f"<br>{state}</div>"
            )

    def _tween_cursor(self) -> None:
        """Smoothly slide the cursor toward ``self._cursor_target``.

        Uses simple exponential easing — each tick covers 35 % of the
        remaining distance, which produces a snappy but un-jarring
        glide.  Stops the timer once we are within 0.4 px to save CPU.
        """
        if self._machine_cursor is None or not self._cursor_have_target:
            return
        cur = self._machine_cursor.pos()
        tgt = self._cursor_target
        dx = tgt.x() - cur.x()
        dy = tgt.y() - cur.y()
        if abs(dx) < 0.4 and abs(dy) < 0.4:
            self._machine_cursor.setPos(tgt)
            if self._cursor_label is not None:
                self._cursor_label.setPos(tgt.x() + 26, tgt.y() + 6)
            self._reposition_activity_banner(tgt)
            self._cursor_timer.stop()
            return
        new_pt = QPointF(cur.x() + dx * 0.35, cur.y() + dy * 0.35)
        self._machine_cursor.setPos(new_pt)
        if self._cursor_label is not None:
            self._cursor_label.setPos(new_pt.x() + 26, new_pt.y() + 6)
        self._reposition_activity_banner(new_pt)

    # ==================================================================
    # Activity banner (floats above the machine cursor)
    # ==================================================================
    def _build_activity_banner(self) -> None:
        """Big floating message that follows the head cursor.

        The user wants to see *what the plotter is doing right now*
        directly on the canvas, not buried in a console log.  The
        banner sits ~50 px above the cursor and is updated by
        ``set_activity_text`` whenever the streaming code transitions
        between phases (drawing → pen swap → drawing again, etc.).
        """
        banner = QGraphicsTextItem("")
        banner.setDefaultTextColor(QColor(255, 255, 255))
        banner.setFont(QFont("Arial", 11, QFont.Bold))
        banner.setZValue(1002)        # above cursor (1000) and label (1001)
        banner.setFlag(QGraphicsItem.ItemIsMovable, False)
        banner.setFlag(QGraphicsItem.ItemIsSelectable, False)
        banner.setVisible(False)
        self.scene.addItem(banner)
        self._activity_banner = banner

    def _reposition_activity_banner(self, cursor_pos: QPointF) -> None:
        if self._activity_banner is None or not self._activity_banner.isVisible():
            return
        # Banner anchored ~50 px above the cursor, centred horizontally.
        rect = self._activity_banner.boundingRect()
        bx = cursor_pos.x() - rect.width() / 2
        by = cursor_pos.y() - rect.height() - 38
        self._activity_banner.setPos(bx, by)

    def set_activity_text(self, text: str,
                          rgb: Optional[tuple] = None) -> None:
        """Update the floating banner that hovers above the head cursor.

        ``rgb`` is the colour the banner background should take — pass
        the active pen's RGB so the banner literally turns red while
        the plotter is drawing in red, blue while in blue, etc.  Pass
        ``None`` to clear the banner.
        """
        if self._activity_banner is None:
            return
        if not text:
            self._activity_banner.setVisible(False)
            return
        if rgb is None:
            rgb = (40, 40, 50)
        # Pick a readable foreground based on background luminance.
        lum = 0.299 * rgb[0] + 0.587 * rgb[1] + 0.114 * rgb[2]
        fg = "#1e1e2e" if lum > 160 else "#ffffff"
        self._activity_banner.setHtml(
            f"<div style='background: rgba({rgb[0]},{rgb[1]},{rgb[2]},235);"
            f" color: {fg}; padding: 5px 12px; border: 2px solid #ffffff;"
            f" border-radius: 6px; font-weight: bold; font-size: 11pt;"
            f" font-family: Arial;'>{text}</div>"
        )
        self._activity_banner.setVisible(True)
        # Anchor immediately so it doesn't blink at 0,0 first.
        if self._machine_cursor is not None:
            self._reposition_activity_banner(self._machine_cursor.pos())

    # ==================================================================
    # Pen rack overlay (coloured circles at each saved slot position)
    # ==================================================================
    def _build_pen_rack_layer(self) -> None:
        group = QGraphicsItemGroup()
        group.setZValue(900)            # below cursor (1000) but above strokes
        group.setFlag(QGraphicsItem.ItemIsMovable, False)
        group.setFlag(QGraphicsItem.ItemIsSelectable, False)
        self.scene.addItem(group)
        self._pen_rack_group = group

    def update_pen_rack(self, slot_data: dict, enabled: bool = True) -> None:
        """Redraw the pen-rack circles based on the configuration the
        user saved in PenChangerPanel.

        ``slot_data`` is the same dict the panel exposes as
        ``self.slot_data`` — keyed by tool number, each value carrying
        ``{name, x, y, z}`` in millimetres.  ``enabled`` reflects the
        panel's "Enable automatic pen changer" checkbox: if False, all
        rack markers are hidden so the work area isn't cluttered.
        """
        if self._pen_rack_group is None:
            return
        # Drop existing items
        for it in list(self._pen_rack_items.values()):
            self._pen_rack_group.removeFromGroup(it)
            self.scene.removeItem(it)
        self._pen_rack_items.clear()
        if not enabled or not slot_data:
            self._pen_rack_group.setVisible(False)
            return
        # Build a fresh marker per saved slot
        preset_by_tool = {p["tool"]: p for p in PEN_PRESETS}
        for tool, info in slot_data.items():
            try:
                x_mm = float(info.get("x", 0))
                y_mm = float(info.get("y", 0))
            except (TypeError, ValueError):
                continue
            preset = preset_by_tool.get(int(tool), {})
            rgb = preset.get("rgb", (180, 180, 180))
            name = info.get("name") or preset.get("name", f"Pen {tool}")

            scene_pos = self.mm_to_scene(x_mm, y_mm)
            marker = QGraphicsItemGroup()

            # Outer halo
            halo = QGraphicsEllipseItem(-18, -18, 36, 36)
            halo.setPen(QPen(QColor(0, 0, 0, 0), 0))
            halo.setBrush(QBrush(QColor(0, 0, 0, 70)))
            marker.addToGroup(halo)
            # Pen colour disc
            disc = QGraphicsEllipseItem(-12, -12, 24, 24)
            disc.setPen(QPen(QColor(20, 20, 20), 2))
            disc.setBrush(QBrush(QColor(rgb[0], rgb[1], rgb[2])))
            marker.addToGroup(disc)
            # Tool number label centred on disc
            lum = 0.299 * rgb[0] + 0.587 * rgb[1] + 0.114 * rgb[2]
            txt_color = QColor(20, 20, 20) if lum > 160 else QColor(255, 255, 255)
            num = QGraphicsTextItem(f"T{tool}")
            num.setDefaultTextColor(txt_color)
            num.setFont(QFont("Arial", 9, QFont.Bold))
            br = num.boundingRect()
            num.setPos(-br.width() / 2, -br.height() / 2)
            marker.addToGroup(num)
            # Pen name caption beneath the disc
            cap = QGraphicsTextItem(name)
            cap.setDefaultTextColor(QColor(20, 20, 20))
            cap.setFont(QFont("Arial", 7, QFont.Bold))
            cap.setHtml(
                f"<div style='background: rgba(255,235,120,230);"
                f" padding: 1px 4px; border: 1px solid #555;"
                f" font-family: Arial; font-size: 7pt; font-weight: bold;"
                f" color: #111;'>{name}</div>"
            )
            cb = cap.boundingRect()
            cap.setPos(-cb.width() / 2, 16)
            marker.addToGroup(cap)

            marker.setPos(scene_pos)
            self._pen_rack_group.addToGroup(marker)
            self._pen_rack_items[int(tool)] = marker

        self._pen_rack_group.setVisible(True)

    # ==================================================================
    # Top palette strip (every available pen, active one highlighted)
    # ==================================================================
    def _build_pen_strip(self) -> None:
        group = QGraphicsItemGroup()
        group.setZValue(950)
        group.setFlag(QGraphicsItem.ItemIsMovable, False)
        group.setFlag(QGraphicsItem.ItemIsSelectable, False)
        self.scene.addItem(group)
        self._pen_strip_group = group

        # Layout: a horizontal strip across the top of the work area,
        # ABOVE the plot frame, inside the canvas margin.  Each preset
        # gets a coloured rectangle + tool label; the active pen gets a
        # bright yellow outline.
        n = len(PEN_PRESETS)
        if n == 0:
            return
        strip_y = self.margin_px - 30        # 30 px above the work area
        strip_h = 22
        strip_total = self.plot_width_px
        sw = strip_total / n

        for idx, preset in enumerate(PEN_PRESETS):
            rgb = preset["rgb"]
            tool = preset["tool"]
            name = preset["name"]
            x = self.origin_x + idx * sw
            swatch = QGraphicsRectItem(x + 1, strip_y, sw - 2, strip_h)
            swatch.setBrush(QBrush(QColor(rgb[0], rgb[1], rgb[2])))
            swatch.setPen(QPen(QColor(70, 70, 70), 1))
            group.addToGroup(swatch)

            lum = 0.299 * rgb[0] + 0.587 * rgb[1] + 0.114 * rgb[2]
            fg = QColor(20, 20, 20) if lum > 160 else QColor(255, 255, 255)
            label = QGraphicsTextItem(f"T{tool} {name}")
            label.setDefaultTextColor(fg)
            label.setFont(QFont("Arial", 8, QFont.Bold))
            br = label.boundingRect()
            label.setPos(x + (sw - br.width()) / 2,
                         strip_y + (strip_h - br.height()) / 2)
            group.addToGroup(label)

            self._pen_strip_items[tool] = {
                "swatch": swatch, "label": label, "rgb": rgb, "name": name,
            }

    def set_active_pen(self, tool: Optional[int]) -> None:
        """Highlight the currently-active pen swatch in the top strip.

        Call with ``None`` to clear the highlight.  This is what
        produces the yellow outline so the user can see at a glance
        which pen the plotter is supposed to be using right now.
        """
        # Reset previous highlight
        if (self._active_pen_tool is not None
                and self._active_pen_tool in self._pen_strip_items):
            sw = self._pen_strip_items[self._active_pen_tool]["swatch"]
            sw.setPen(QPen(QColor(70, 70, 70), 1))
        # Apply new highlight
        self._active_pen_tool = tool
        if tool is not None and tool in self._pen_strip_items:
            sw = self._pen_strip_items[tool]["swatch"]
            sw.setPen(QPen(QColor(255, 215, 0), 3))

    # ------------------------------------------------------------------
    def export_image(self, filepath: str) -> bool:
        rect = QRectF(self.origin_x, self.margin_px,
                      self.plot_width_px, self.plot_height_px)
        image = QImage(int(rect.width()), int(rect.height()),
                       QImage.Format_ARGB32)
        image.fill(Qt.white)
        painter = QPainter(image)
        painter.setRenderHint(QPainter.Antialiasing)
        self.scene.render(painter, QRectF(image.rect()), rect)
        painter.end()
        return image.save(filepath)
