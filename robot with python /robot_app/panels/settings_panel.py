"""Bottom-tab Settings panel: plot size, feed, cncjs, watch folder."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from PyQt5.QtCore import pyqtSignal
from PyQt5.QtWidgets import (
    QCheckBox, QDoubleSpinBox, QFileDialog, QFormLayout, QGroupBox,
    QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton, QSpinBox,
    QVBoxLayout, QWidget,
)


class SettingsPanel(QWidget):
    """Settings tab.  Emits *settings_changed* for important toggles so
    the main window can re-sync the worker config without polling."""

    settings_changed = pyqtSignal()
    plot_size_changed = pyqtSignal(int, int)
    feed_changed = pyqtSignal(int)
    pen_up_changed = pyqtSignal(float)
    pen_down_changed = pyqtSignal(float)
    start_cncjs_requested = pyqtSignal(bool)   # bool: force open browser
    open_browser_requested = pyqtSignal()

    def __init__(self, gcode_folder: Path, parent=None):
        super().__init__(parent)
        self.gcode_folder = gcode_folder
        self._build()

    # ------------------------------------------------------------------
    def _build(self) -> None:
        root = QHBoxLayout(self)

        # Plot area
        size_group = QGroupBox("Plot Area (Frame Size in mm)")
        size_layout = QFormLayout()
        self.plot_width_input = QSpinBox()
        self.plot_width_input.setRange(10, 2000)
        self.plot_width_input.setValue(216)
        self.plot_width_input.setSuffix(" mm")
        self.plot_width_input.valueChanged.connect(self._emit_plot_size)
        self.plot_height_input = QSpinBox()
        self.plot_height_input.setRange(10, 2000)
        self.plot_height_input.setValue(279)
        self.plot_height_input.setSuffix(" mm")
        self.plot_height_input.valueChanged.connect(self._emit_plot_size)
        size_layout.addRow("Width:", self.plot_width_input)
        size_layout.addRow("Height:", self.plot_height_input)
        preset_row = QHBoxLayout()
        for name, w, h in [("A5", 148, 210), ("A4", 210, 297),
                           ("A3", 297, 420), ("Letter", 216, 279)]:
            btn = QPushButton(name)
            btn.clicked.connect(
                lambda _ch, w=w, h=h: self._set_preset(w, h)
            )
            preset_row.addWidget(btn)
        size_layout.addRow("Presets:", preset_row)
        size_group.setLayout(size_layout)
        root.addWidget(size_group)

        # Feed / Z
        feed_group = QGroupBox("Feed Rates / Z heights")
        feed_layout = QFormLayout()
        self.feed_input = QSpinBox()
        self.feed_input.setRange(100, 10000)
        self.feed_input.setValue(3000)
        self.feed_input.setSuffix(" mm/min")
        self.feed_input.valueChanged.connect(self.feed_changed.emit)
        self.pen_up_input = QDoubleSpinBox()
        self.pen_up_input.setRange(0, 50)
        self.pen_up_input.setValue(5.0)
        self.pen_up_input.setSingleStep(0.5)
        self.pen_up_input.setSuffix(" mm")
        self.pen_up_input.valueChanged.connect(self.pen_up_changed.emit)
        self.pen_down_input = QDoubleSpinBox()
        self.pen_down_input.setRange(-20, 20)
        self.pen_down_input.setValue(0.0)
        self.pen_down_input.setSingleStep(0.5)
        self.pen_down_input.setSuffix(" mm")
        self.pen_down_input.valueChanged.connect(self.pen_down_changed.emit)
        feed_layout.addRow("Feed Rate:", self.feed_input)
        feed_layout.addRow("Pen Up Z:", self.pen_up_input)
        feed_layout.addRow("Pen Down Z:", self.pen_down_input)
        feed_group.setLayout(feed_layout)
        root.addWidget(feed_group)

        # cncjs / folder
        cncjs_group = QGroupBox("cncjs / G-code Folder")
        cncjs_layout = QVBoxLayout()

        host_row = QHBoxLayout()
        self.cncjs_host_input = QLineEdit("127.0.0.1")
        self.cncjs_host_input.editingFinished.connect(self.settings_changed.emit)
        self.cncjs_port_input = QSpinBox()
        self.cncjs_port_input.setRange(1, 65535)
        self.cncjs_port_input.setValue(8000)
        self.cncjs_port_input.valueChanged.connect(self.settings_changed.emit)
        host_row.addWidget(QLabel("Host"))
        host_row.addWidget(self.cncjs_host_input)
        host_row.addWidget(QLabel("Port"))
        host_row.addWidget(self.cncjs_port_input)
        cncjs_layout.addLayout(host_row)

        path_row = QHBoxLayout()
        self.path_input = QLineEdit()
        self.path_input.setPlaceholderText("Optional cncjs command path")
        self.path_input.editingFinished.connect(self.settings_changed.emit)
        browse_btn = QPushButton("Browse...")
        browse_btn.clicked.connect(self._browse_cncjs)
        detect_btn = QPushButton("Detect")
        detect_btn.setToolTip("Auto-detect cncjs in PATH")
        detect_btn.clicked.connect(lambda: self.settings_changed.emit())
        self.detect_btn = detect_btn  # main window connects an extra slot
        path_row.addWidget(self.path_input)
        path_row.addWidget(browse_btn)
        path_row.addWidget(detect_btn)
        cncjs_layout.addLayout(path_row)

        token_row = QHBoxLayout()
        self.cncjs_token_input = QLineEdit()
        self.cncjs_token_input.setPlaceholderText("Optional cncjs access token")
        self.cncjs_token_input.editingFinished.connect(self.settings_changed.emit)
        token_row.addWidget(QLabel("Token"))
        token_row.addWidget(self.cncjs_token_input)
        cncjs_layout.addLayout(token_row)

        login_row = QHBoxLayout()
        self.cncjs_user_input = QLineEdit()
        self.cncjs_user_input.setPlaceholderText("Username")
        self.cncjs_user_input.editingFinished.connect(self.settings_changed.emit)
        self.cncjs_password_input = QLineEdit()
        self.cncjs_password_input.setPlaceholderText("Password")
        self.cncjs_password_input.setEchoMode(QLineEdit.Password)
        self.cncjs_password_input.editingFinished.connect(self.settings_changed.emit)
        login_row.addWidget(QLabel("User"))
        login_row.addWidget(self.cncjs_user_input)
        login_row.addWidget(QLabel("Pass"))
        login_row.addWidget(self.cncjs_password_input)
        cncjs_layout.addLayout(login_row)

        self.auto_launch_check = QCheckBox("Auto-start cncjs on app launch")
        self.auto_launch_check.toggled.connect(self.settings_changed.emit)
        self.auto_browser_check = QCheckBox("Auto-open browser")
        self.auto_browser_check.toggled.connect(self.settings_changed.emit)
        self.auto_connect_check = QCheckBox("Auto-connect machine on launch")
        self.auto_connect_check.setChecked(True)
        self.auto_connect_check.toggled.connect(self.settings_changed.emit)
        cncjs_layout.addWidget(self.auto_launch_check)
        cncjs_layout.addWidget(self.auto_browser_check)
        cncjs_layout.addWidget(self.auto_connect_check)

        launch_row = QHBoxLayout()
        self.launch_grbl_btn = QPushButton("Start cncjs")
        self.launch_grbl_btn.setStyleSheet(
            "QPushButton { background: #7c3aed; color: white;"
            "  padding: 8px; font-weight: bold; border-radius: 4px; }"
            "QPushButton:hover { background: #6d28d9; color: white; }"
        )
        self.launch_grbl_btn.clicked.connect(
            lambda: self.start_cncjs_requested.emit(True)
        )
        self.open_browser_btn = QPushButton("Open cncjs Browser")
        self.open_browser_btn.clicked.connect(self.open_browser_requested.emit)
        self.grbl_status_label = QLabel("⚫ cncjs not started")
        self.grbl_status_label.setWordWrap(True)
        self.grbl_status_label.setStyleSheet(
            "color: #888; background: transparent; font-style: italic;"
        )
        launch_row.addWidget(self.launch_grbl_btn)
        launch_row.addWidget(self.open_browser_btn)
        launch_row.addWidget(self.grbl_status_label)
        cncjs_layout.addLayout(launch_row)

        folder_row = QHBoxLayout()
        folder_label = QLabel(f"Folder: {self.gcode_folder}")
        folder_label.setWordWrap(True)
        folder_label.setStyleSheet("background: transparent; color:#cdd6f4;")
        open_folder_btn = QPushButton("Open Folder")
        open_folder_btn.clicked.connect(self._open_folder)
        folder_row.addWidget(folder_label)
        folder_row.addWidget(open_folder_btn)
        cncjs_layout.addLayout(folder_row)
        cncjs_group.setLayout(cncjs_layout)
        root.addWidget(cncjs_group)

    # ------------------------------------------------------------------
    def _set_preset(self, w: int, h: int) -> None:
        self.plot_width_input.setValue(w)
        self.plot_height_input.setValue(h)

    def _emit_plot_size(self) -> None:
        self.plot_size_changed.emit(
            self.plot_width_input.value(),
            self.plot_height_input.value(),
        )

    def _browse_cncjs(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Select cncjs executable", "", "All Files (*)"
        )
        if path:
            self.path_input.setText(path)
            self.settings_changed.emit()

    def _open_folder(self) -> None:
        try:
            if sys.platform == "win32":
                os.startfile(str(self.gcode_folder))
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(self.gcode_folder)])
            else:
                subprocess.Popen(["xdg-open", str(self.gcode_folder)])
        except OSError as exc:
            QMessageBox.warning(
                self, "Cannot open folder",
                f"Failed to open {self.gcode_folder}:\n{exc}"
            )

    # ------------------------------------------------------------------
    def update_grbl_status(self, running: bool, message: str) -> None:
        prefix = "🟢" if running else "⚫"
        color = "#4CAF50" if running else "#888"
        self.grbl_status_label.setText(f"{prefix} {message}")
        self.grbl_status_label.setStyleSheet(
            f"color: {color}; background: transparent; font-weight: bold;"
        )
