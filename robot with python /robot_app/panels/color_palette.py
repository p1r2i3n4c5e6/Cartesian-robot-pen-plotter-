"""Pen colour selection panel.

Per-button stylesheet now ALWAYS sets foreground and a hover state, so
the yellow / cyan presets stay readable on the dark theme.
"""

from PyQt5.QtGui import QColor
from PyQt5.QtWidgets import (
    QColorDialog, QFrame, QLabel, QPushButton, QVBoxLayout, QWidget,
)

from ..config import PEN_PRESETS


class ColorPalette(QWidget):
    def __init__(self, canvas, parent=None):
        super().__init__(parent)
        self.canvas = canvas
        self.current_color = QColor(0, 0, 0)
        self._build()

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)

        title = QLabel("Pen Colors")
        title.setStyleSheet(
            "background: transparent; color: #89b4fa; "
            "font-weight: bold; font-size: 13px;"
        )
        layout.addWidget(title)

        for preset in PEN_PRESETS:
            color = QColor(*preset["rgb"])
            tool = preset["tool"]
            name = preset["name"]
            text_color = "white" if color.lightness() < 128 else "#1e1e2e"
            btn = QPushButton(f"  Pen {tool}: {name}")
            btn.setStyleSheet(
                f"QPushButton {{"
                f"  background-color: {color.name()};"
                f"  color: {text_color};"
                f"  border: 2px solid #1e1e2e;"
                f"  padding: 8px;"
                f"  font-weight: bold;"
                f"  text-align: left;"
                f"}}"
                f"QPushButton:hover {{"
                f"  border: 3px solid #89b4fa;"
                f"  color: {text_color};"
                f"}}"
            )
            btn.clicked.connect(lambda _ch, c=color: self.select_color(c))
            layout.addWidget(btn)

        custom_btn = QPushButton("Custom Color...")
        custom_btn.setStyleSheet(
            "QPushButton { background: #45475a; color: #cdd6f4; "
            "padding: 8px; font-weight: bold; border: 1px solid #585b70; }"
            "QPushButton:hover { background: #585b70; color: #ffffff; "
            "border: 1px solid #89b4fa; }"
        )
        custom_btn.clicked.connect(self._pick_custom)
        layout.addWidget(custom_btn)

        self.current_label = QLabel("Current: Black")
        self.current_label.setStyleSheet("color: #cdd6f4; background: transparent;")
        self.current_display = QFrame()
        self.current_display.setFixedHeight(28)
        self.current_display.setStyleSheet(
            "background-color: black; border: 2px solid #585b70; border-radius: 3px;"
        )
        layout.addWidget(self.current_label)
        layout.addWidget(self.current_display)
        layout.addStretch()

    def select_color(self, color: QColor) -> None:
        self.current_color = color
        self.canvas.set_pen_color(color)
        self.current_display.setStyleSheet(
            f"background-color: {color.name()}; "
            f"border: 2px solid #585b70; border-radius: 3px;"
        )
        self.current_label.setText(f"Current: {color.name()}")

    def _pick_custom(self) -> None:
        color = QColorDialog.getColor(self.current_color, self, "Select Pen Colour")
        if color.isValid():
            self.select_color(color)
