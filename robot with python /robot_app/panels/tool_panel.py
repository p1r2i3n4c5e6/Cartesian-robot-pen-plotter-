"""Left-hand tool palette (pen / eraser / shapes / view toggles).

White-on-white bug from ``robot2.py`` is fixed: every QPushButton in
the toolbox sets *both* background and foreground.
"""

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QCheckBox, QGroupBox, QLabel, QPushButton, QSlider, QVBoxLayout, QWidget,
)


class ToolPanel(QWidget):
    def __init__(self, canvas, parent=None):
        super().__init__(parent)
        self.canvas = canvas
        self._build()

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)

        title = QLabel("Tools")
        title.setStyleSheet(
            "background: transparent; color: #89b4fa; "
            "font-weight: bold; font-size: 13px;"
        )
        layout.addWidget(title)

        tools = [
            ("Pen", "pen"),
            ("Eraser", "eraser"),
            ("Line", "line"),
            ("Rectangle", "rect"),
            ("Circle", "circle"),
            ("Text", "text"),
            ("Select / Move", "select"),
            ("Pan", "pan"),
        ]
        for name, tool in tools:
            btn = QPushButton(name)
            btn.setStyleSheet(
                "QPushButton {"
                "  background: #45475a; color: #cdd6f4;"
                "  padding: 8px; text-align: left; font-size: 12px;"
                "  border: 1px solid #585b70; border-radius: 4px;"
                "}"
                "QPushButton:hover {"
                "  background: #585b70; color: #ffffff;"
                "  border: 1px solid #89b4fa;"
                "}"
                "QPushButton:pressed {"
                "  background: #89b4fa; color: #1e1e2e;"
                "}"
            )
            btn.clicked.connect(lambda _checked, t=tool: self.canvas.set_tool(t))
            layout.addWidget(btn)

        layout.addWidget(QLabel("Pen Width:"))
        self.width_slider = QSlider(Qt.Horizontal)
        self.width_slider.setMinimum(1)
        self.width_slider.setMaximum(20)
        self.width_slider.setValue(2)
        self.width_label = QLabel("2 px")
        self.width_slider.valueChanged.connect(self._change_width)
        layout.addWidget(self.width_slider)
        layout.addWidget(self.width_label)

        view_group = QGroupBox("View")
        view_layout = QVBoxLayout()
        self.grid_check = QCheckBox("Show Grid")
        self.grid_check.setChecked(True)
        self.grid_check.toggled.connect(self.canvas.toggle_grid)
        self.axes_check = QCheckBox("Show Axes")
        self.axes_check.setChecked(True)
        self.axes_check.toggled.connect(self.canvas.toggle_axes)
        self.rulers_check = QCheckBox("Show Rulers")
        self.rulers_check.setChecked(True)
        self.rulers_check.toggled.connect(self.canvas.toggle_rulers)
        self.origin_check = QCheckBox("Show Origin")
        self.origin_check.setChecked(True)
        self.origin_check.toggled.connect(self.canvas.toggle_origin)
        for cb in (self.grid_check, self.axes_check,
                   self.rulers_check, self.origin_check):
            view_layout.addWidget(cb)
        view_group.setLayout(view_layout)
        layout.addWidget(view_group)
        layout.addStretch()

    def _change_width(self, value: int) -> None:
        self.canvas.set_pen_width(value)
        self.width_label.setText(f"{value} px")
