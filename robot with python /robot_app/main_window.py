"""The main application window — wires every panel together."""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

from PyQt5.QtCore import QSize, Qt, QTimer
from PyQt5.QtGui import QColor, QKeySequence
from PyQt5.QtWidgets import (
    QAction, QDockWidget, QFileDialog, QFrame, QLabel, QMainWindow,
    QMessageBox, QScrollArea, QStatusBar, QStyle, QToolBar, QVBoxLayout,
    QWidget,
)

from . import APP_NAME, APP_TAGLINE, APP_VERSION
from .canvas import DrawingCanvas
from .config import GCODE_DIR, IS_RPI, SUPPORTED_IMPORT_FORMATS
from .gcode.generator import GCodeGenerator
from .logging_setup import get_logger
from .panels import (
    CalibrationPanel, ColorPalette, ConsolePanel, HomingPanel, JogPanel,
    MultiTextPanel, PenChangerPanel, SafetyControlPanel, SerialPanel,
    SettingsPanel, ToolPanel,
)
from .serial_worker import SOCKETIO_AVAILABLE, SerialWorker, autodetect_port
from .settings import load_settings, save_settings


log = get_logger("ui")


def std_icon(widget, sp_enum):
    return widget.style().standardIcon(sp_enum)


def _safe_is_alive(qobj) -> bool:
    """True if a Qt C++ object is still alive (not deleteLater-ed).

    Qt sometimes deletes the underlying C++ widget while the Python
    handle survives — touching attributes on it then raises
    ``RuntimeError: wrapped C/C++ object has been deleted``.  This
    helper lets us detect that case before re-using a cached window.
    """
    try:
        qobj.objectName()
        return True
    except Exception:
        return False


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"{APP_NAME} - {APP_TAGLINE} v{APP_VERSION}")
        self.resize(1200, 760)
        self.setMinimumSize(900, 540)

        self.gcode_folder: Path = GCODE_DIR
        self.serial_worker = SerialWorker()

        self._build_ui()
        self.gcode_generator = GCodeGenerator(self.canvas)

        self._build_menus()
        self._build_toolbar()

        self._build_status_bar()

        # --- Wire signals -----------------------------------------
        self.canvas.mouse_position_changed.connect(self._update_coords)
        self.serial_worker.connection_status.connect(self._on_connection_change)
        self.serial_worker.server_status_changed.connect(self._on_server_status_change)
        self.serial_worker.streaming_finished.connect(self._on_streaming_finished)
        # Live machine cursor on the canvas — follows GRBL WPos in real
        # time so the user can see exactly where the head is.
        self.serial_worker.machine_position_changed.connect(
            self.canvas.update_machine_cursor
        )
        # Live state badge on the right side of the status bar.
        self.serial_worker.machine_position_changed.connect(
            self._on_machine_state_update
        )
        self.serial_worker.connection_status.connect(
            self._on_connection_state_for_badge
        )
        # Streaming progress drives the canvas activity banner so the
        # user sees "Drawing with Red" / "Picking up Blue pen" /
        # "Putting back Red pen" on the canvas while a job runs.
        self.serial_worker.progress_updated.connect(
            self._on_stream_progress
        )

        # Internal state for the live progress narrator.
        self._stream_lines: list = []
        self._last_announced_tool = None

        self.settings_panel.plot_size_changed.connect(self._update_plot_size)
        self.settings_panel.feed_changed.connect(
            lambda v: setattr(self.gcode_generator, "feed_rate", int(v))
        )
        self.settings_panel.pen_up_changed.connect(
            lambda v: setattr(self.gcode_generator, "pen_up_z", float(v))
        )
        self.settings_panel.pen_down_changed.connect(
            lambda v: setattr(self.gcode_generator, "pen_down_z", float(v))
        )
        self.settings_panel.settings_changed.connect(self._sync_worker_settings)
        self.settings_panel.start_cncjs_requested.connect(self._handle_start_cncjs)
        self.settings_panel.open_browser_requested.connect(self._open_cncjs_browser)
        self.settings_panel.detect_btn.clicked.connect(self._auto_find_cncjs)

        self.jog_panel.position_save_requested.connect(
            self.pen_changer_panel.save_position_for_slot
        )
        self.pen_changer_panel.settings_changed.connect(self._sync_tool_changer)
        # When the user clicks "Read settings" in the Homing panel and
        # the firmware sends back its $$ dump, mirror $130/$131/$132
        # into the Jog panel's Travel-Envelope group so the user sees
        # the *actual* current soft-limit values.
        self.homing_panel.grbl_settings_loaded.connect(
            self.jog_panel.update_envelope_from_grbl
        )

        # --- Load + initial sync ---------------------------------
        self._load_settings()
        self._sync_worker_settings()
        self._sync_tool_changer()

        QTimer.singleShot(200, self.canvas.fit_to_view)
        QTimer.singleShot(800, self._auto_connect_on_launch)

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        self.canvas = DrawingCanvas(self)
        self.setCentralWidget(self.canvas)

        # ---------- Build every panel up-front ------------------------
        # Panels that LIVE in docks (always visible while the app is up):
        self.serial_panel = SerialPanel(self.serial_worker)
        self.tool_panel = ToolPanel(self.canvas)
        self.color_palette = ColorPalette(self.canvas)
        self.multi_text_panel = MultiTextPanel(self.canvas)
        # Panels that are launched on-demand from the "Windows" menu:
        self.settings_panel = SettingsPanel(self.gcode_folder)
        self.pen_changer_panel = PenChangerPanel(self.serial_worker)
        self.console = ConsolePanel(self.serial_worker)
        self.safety_panel = SafetyControlPanel(self.serial_worker)
        self.jog_panel = JogPanel(self.serial_worker)
        self.homing_panel = HomingPanel(self.serial_worker)
        self.calibration_panel = CalibrationPanel(self.serial_worker)

        # Detached-window registry — populated lazily, persists for the
        # lifetime of the main window so panel state is preserved
        # between open/close cycles.
        self._panel_windows: dict = {}

        # ---------- LEFT dock: Connect + Tools + Colors --------------
        left_container = QWidget()
        left_layout = QVBoxLayout(left_container)
        left_layout.setContentsMargins(4, 4, 4, 4)
        left_layout.setSpacing(6)

        connect_label = QLabel("CONNECTIVITY")
        connect_label.setStyleSheet(
            "QLabel { color: #89b4fa; font-weight: bold;"
            " font-size: 11px; padding: 2px 4px; }"
        )
        left_layout.addWidget(connect_label)
        left_layout.addWidget(self.serial_panel)

        sep1 = QFrame()
        sep1.setFrameShape(QFrame.HLine)
        sep1.setStyleSheet("color: #45475a;")
        left_layout.addWidget(sep1)
        left_layout.addWidget(self.tool_panel)

        sep2 = QFrame()
        sep2.setFrameShape(QFrame.HLine)
        sep2.setStyleSheet("color: #45475a;")
        left_layout.addWidget(sep2)
        left_layout.addWidget(self.color_palette)
        left_layout.addStretch(1)

        left_scroll = QScrollArea()
        left_scroll.setWidget(left_container)
        left_scroll.setWidgetResizable(True)
        left_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        self.left_dock = QDockWidget("Connect / Tools / Colors", self)
        self.left_dock.setAllowedAreas(
            Qt.LeftDockWidgetArea | Qt.RightDockWidgetArea
        )
        self.left_dock.setWidget(left_scroll)
        self.left_dock.setMinimumWidth(250)
        self.left_dock.setMaximumWidth(340)
        self.addDockWidget(Qt.LeftDockWidgetArea, self.left_dock)

        # ---------- RIGHT dock: Multiple text input ------------------
        right_scroll = QScrollArea()
        right_scroll.setWidget(self.multi_text_panel)
        right_scroll.setWidgetResizable(True)
        right_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        self.right_dock = QDockWidget("Text Inputs", self)
        self.right_dock.setAllowedAreas(
            Qt.LeftDockWidgetArea | Qt.RightDockWidgetArea
        )
        self.right_dock.setWidget(right_scroll)
        self.right_dock.setMinimumWidth(300)
        self.right_dock.setMaximumWidth(420)
        self.addDockWidget(Qt.RightDockWidgetArea, self.right_dock)

        # Keep aliases the rest of the code (and existing settings)
        # expects, even though the old tool_dock / right_dock / bottom_dock
        # layout no longer exists in three pieces.
        self.tool_dock = self.left_dock
        self.bottom_dock = self.right_dock

    # ------------------------------------------------------------------
    def _build_menus(self) -> None:
        menubar = self.menuBar()

        file_menu = menubar.addMenu("&File")
        file_menu.addAction("New", self._new_canvas, QKeySequence.New)
        file_menu.addAction("Import File...", self._import_file, "Ctrl+I")
        file_menu.addAction("Export Image...", self._export_image, "Ctrl+E")
        file_menu.addSeparator()
        file_menu.addAction("Save G-code...", self._save_gcode, "Ctrl+S")
        file_menu.addAction("Open G-code Folder", self._open_gcode_folder)
        file_menu.addSeparator()
        file_menu.addAction("Stream to Machine", self._stream_to_machine, "F5")
        file_menu.addAction("Save For cncjs", self._send_to_cncjs)
        file_menu.addSeparator()
        file_menu.addAction("Exit", self.close, "Ctrl+Q")

        edit_menu = menubar.addMenu("&Edit")
        edit_menu.addAction("Clear Canvas", self._clear_canvas)
        edit_menu.addAction("Delete Selected", self._delete_selected, "Delete")

        view_menu = menubar.addMenu("&View")
        view_menu.addAction("Fit to View", self.canvas.fit_to_view, "Ctrl+0")
        view_menu.addAction("Zoom In", lambda: self.canvas.scale(1.2, 1.2),
                            "Ctrl++")
        view_menu.addAction("Zoom Out", lambda: self.canvas.scale(1/1.2, 1/1.2),
                            "Ctrl+-")
        view_menu.addSeparator()
        # Dock toggles — let the user fold the side panels away.
        left_toggle = self.left_dock.toggleViewAction()
        left_toggle.setText("Show Left Panel (Connect / Tools / Colors)")
        view_menu.addAction(left_toggle)
        right_toggle = self.right_dock.toggleViewAction()
        right_toggle.setText("Show Right Panel (Text Inputs)")
        view_menu.addAction(right_toggle)
        view_menu.addSeparator()
        view_menu.addAction("Reset Panel Layout", self._reset_panel_layout)

        # NEW: every former tab is now its own menu-launched window so
        # the canvas isn't crowded by panels the user only needs
        # occasionally.  Each entry opens the same panel widget into a
        # detachable QMainWindow that remembers its own size + position.
        windows_menu = menubar.addMenu("&Windows")
        windows_menu.addAction(
            "Settings...",
            lambda: self._open_panel_window(
                "settings", "Settings", self.settings_panel, (640, 560)
            ),
        )
        windows_menu.addAction(
            "Pen Mapping...",
            lambda: self._open_panel_window(
                "pen_mapping", "Pen Mapping",
                self.pen_changer_panel, (640, 520),
            ),
        )
        windows_menu.addSeparator()
        windows_menu.addAction(
            "Safety Control...",
            lambda: self._open_panel_window(
                "safety", "Safety Control", self.safety_panel, (440, 600)
            ),
        )
        windows_menu.addAction(
            "Jog Control...",
            lambda: self._open_panel_window(
                "jog", "Jog Control", self.jog_panel, (380, 520)
            ),
        )
        windows_menu.addAction(
            "Homing...",
            lambda: self._open_panel_window(
                "homing", "Homing", self.homing_panel, (640, 560)
            ),
        )
        windows_menu.addAction(
            "Steps/mm Calibration...",
            lambda: self._open_panel_window(
                "calibration", "Motion Calibration",
                self.calibration_panel, (720, 620)
            ),
        )
        windows_menu.addSeparator()
        windows_menu.addAction(
            "Console...",
            lambda: self._open_panel_window(
                "console", "Console", self.console, (820, 460)
            ),
        )

        machine_menu = menubar.addMenu("&Machine")
        machine_menu.addAction("Home ($H)", self.serial_worker.home)
        machine_menu.addAction("Unlock ($X)", self.serial_worker.unlock_alarm)
        machine_menu.addAction("Emergency Stop", self.serial_worker.emergency_stop)
        machine_menu.addAction("Status Query (?)", self.serial_worker.get_status)

        help_menu = menubar.addMenu("&Help")
        help_menu.addAction("About", self._show_about)

    def _build_toolbar(self) -> None:
        toolbar = QToolBar("Main")
        toolbar.setIconSize(QSize(20, 20) if IS_RPI else QSize(24, 24))
        toolbar.setMovable(False)
        toolbar.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.addToolBar(toolbar)

        toolbar.addAction(QAction(
            std_icon(self, QStyle.SP_FileIcon), "New", self,
            triggered=self._new_canvas, shortcut=QKeySequence.New
        ))
        toolbar.addAction(QAction(
            std_icon(self, QStyle.SP_DialogOpenButton), "Import", self,
            triggered=self._import_file, shortcut="Ctrl+I"
        ))
        toolbar.addAction(QAction(
            std_icon(self, QStyle.SP_DialogSaveButton), "Export", self,
            triggered=self._export_image, shortcut="Ctrl+E"
        ))
        toolbar.addSeparator()
        toolbar.addAction(QAction(
            std_icon(self, QStyle.SP_DriveHDIcon), "Save G-code", self,
            triggered=self._save_gcode, shortcut="Ctrl+S"
        ))
        toolbar.addSeparator()

        self.draw_serial_action = QAction(
            std_icon(self, QStyle.SP_MediaPlay), "STREAM TO MACHINE", self
        )
        self.draw_serial_action.setShortcut("F5")
        self.draw_serial_action.triggered.connect(self._stream_to_machine)
        toolbar.addAction(self.draw_serial_action)
        btn = toolbar.widgetForAction(self.draw_serial_action)
        if btn:
            btn.setStyleSheet(
                "QToolButton { background-color: #4CAF50; color: white;"
                " font-weight: bold; padding: 6px 14px; border-radius: 4px; }"
                "QToolButton:hover { background-color: #45a049; color: white; }"
            )

        toolbar.addAction(QAction(
            std_icon(self, QStyle.SP_FileDialogToParent), "Save For cncjs",
            self, triggered=self._send_to_cncjs
        ))
        toolbar.addSeparator()

        # "Open cncjs" — cncjs already runs in the background; this
        # button just opens the web UI in the default browser so the
        # user can confirm the server is up and inspect the running job.
        launch_action = QAction(
            std_icon(self, QStyle.SP_ComputerIcon), "Open cncjs", self
        )
        launch_action.setToolTip(
            "<b>Open cncjs Web UI</b><br>"
            "cncjs runs in the background as soon as the app starts.<br>"
            "Click here to launch the cncjs dashboard in your browser "
            "so you can confirm the server is up and watch the job "
            "alongside this canvas."
        )
        launch_action.triggered.connect(lambda: self._handle_start_cncjs(True))
        toolbar.addAction(launch_action)
        btn = toolbar.widgetForAction(launch_action)
        if btn:
            btn.setStyleSheet(
                "QToolButton { background-color: #7c3aed; color: white;"
                " font-weight: bold; padding: 6px 12px; border-radius: 4px; }"
                "QToolButton:hover { background-color: #6d28d9; color: white; }"
            )

        toolbar.addSeparator()
        # ------------------------------------------------------------------
        # Multi-Color toggle — always visible on the main toolbar so the
        # user can flip between per-colour pen swaps and single-pen mode
        # without digging into the Settings window.  Default ON.
        # ------------------------------------------------------------------
        self.multi_color_action = QAction(
            std_icon(self, QStyle.SP_DialogYesButton),
            "Multi-Color: ON", self,
        )
        self.multi_color_action.setCheckable(True)
        self.multi_color_action.setChecked(True)
        self.multi_color_action.setToolTip(
            "<b>Multi-Color G-code</b><br>"
            "When <b>ON</b>: strokes are grouped by colour and the "
            "controller pauses for a pen swap between groups (or runs "
            "the auto pen changer if configured).<br>"
            "When <b>OFF</b>: all strokes stream in canvas order using "
            "whatever pen is currently loaded \u2014 useful for single-pen "
            "plotters that don't need swaps."
        )
        self.multi_color_action.toggled.connect(self._toggle_multi_color)
        toolbar.addAction(self.multi_color_action)
        mc_btn = toolbar.widgetForAction(self.multi_color_action)
        if mc_btn:
            mc_btn.setStyleSheet(
                "QToolButton { background-color: #4caf50; color: white;"
                " font-weight: bold; padding: 6px 12px; border-radius: 4px; }"
                "QToolButton:checked { background-color: #4caf50; }"
                "QToolButton:!checked { background-color: #b71c1c; }"
                "QToolButton:hover { background-color: #66bb6a; color: white; }"
            )

        toolbar.addSeparator()
        # ------------------------------------------------------------------
        # EMERGENCY STOP — biggest, reddest, scariest button in the bar.
        # Sends GRBL 0x18 (soft-reset), halting motion immediately,
        # flushing the planner buffer and putting the controller into
        # ALARM state.  Use when the head is about to crash, paper is
        # mis-fed, a pen has snapped, etc.  After E-STOP the user must
        # press KILL ALARM (next button) and re-home before another job.
        # ------------------------------------------------------------------
        self.estop_action = QAction(
            std_icon(self, QStyle.SP_BrowserStop),
            "🛑 E-STOP", self,
        )
        self.estop_action.setShortcut("F12")
        self.estop_action.setToolTip(
            "<b>EMERGENCY STOP  (shortcut: F12)</b><br>"
            "Sends GRBL real-time soft-reset (<code>0x18</code>).<br>"
            "Halts motion immediately, clears the planner buffer, and "
            "puts the controller into ALARM state.<br>"
            "After triggering, press <b>KILL ALARM</b> and "
            "<b>re-home</b> before running another job."
        )
        self.estop_action.triggered.connect(self._emergency_stop)
        # Always allowed (even when "connected" status is stale) so the
        # user's panic button never grays out at the worst moment.
        toolbar.addAction(self.estop_action)
        es_btn = toolbar.widgetForAction(self.estop_action)
        if es_btn:
            es_btn.setStyleSheet(
                "QToolButton { background-color: #b00020; color: white;"
                " font-weight: 900; font-size: 11pt; padding: 6px 16px;"
                " border-radius: 4px; border: 3px solid #6d0010; }"
                "QToolButton:hover { background-color: #8b0000; color: white;"
                " border: 3px solid #5a000d; }"
                "QToolButton:pressed { background-color: #5a000d; color: white; }"
            )

        # ------------------------------------------------------------------
        # KILL ALARM — clears GRBL Alarm/Door state and pulls the
        # controller back to Idle without re-homing.  This is the single
        # most useful "I don't know what's wrong, just unstick it"
        # button on a plotter.  Sends:
        #   1) ~     -> in case GRBL is in Hold rather than Alarm
        #   2) $X    -> unlock alarm
        #   3) ?     -> refresh status so the badge updates immediately
        # ------------------------------------------------------------------
        self.kill_alarm_action = QAction(
            std_icon(self, QStyle.SP_DialogResetButton),
            "KILL ALARM", self,
        )
        self.kill_alarm_action.setShortcut("F8")
        self.kill_alarm_action.setToolTip(
            "<b>Kill the alarm  (shortcut: F8)</b><br>"
            "Pulls the controller back to <b>Idle</b> after a limit hit, "
            "soft-limit violation or door event.<br>"
            "Sends in order:<ul>"
            "<li><code>~</code> &nbsp; (in case it's actually Hold)</li>"
            "<li><code>$X</code> (clear alarm)</li>"
            "<li><code>?</code> &nbsp; (refresh status)</li></ul>"
            "After clearing you should still <b>re-home</b> before "
            "running another job — coordinates can't be trusted "
            "after an alarm."
        )
        self.kill_alarm_action.triggered.connect(self._kill_alarm)
        # Disabled until we know we're connected AND in alarm.
        self.kill_alarm_action.setEnabled(False)
        toolbar.addAction(self.kill_alarm_action)
        ka_btn = toolbar.widgetForAction(self.kill_alarm_action)
        if ka_btn:
            ka_btn.setStyleSheet(
                "QToolButton { background-color: #d32f2f; color: white;"
                " font-weight: bold; padding: 6px 14px; border-radius: 4px;"
                " border: 2px solid #b71c1c; }"
                "QToolButton:hover { background-color: #b71c1c; color: white; }"
                "QToolButton:disabled { background-color: #5e2020;"
                " color: #bbbbbb; border: 2px solid #3a1010; }"
            )

        toolbar.addSeparator()
        toolbar.addAction(QAction(
            std_icon(self, QStyle.SP_TrashIcon), "Clear",
            self, triggered=self._clear_canvas
        ))
        toolbar.addSeparator()
        toolbar.addAction(QAction(
            std_icon(self, QStyle.SP_ArrowUp), "Zoom +",
            self, triggered=lambda: self.canvas.scale(1.2, 1.2)
        ))
        toolbar.addAction(QAction(
            std_icon(self, QStyle.SP_ArrowDown), "Zoom -",
            self, triggered=lambda: self.canvas.scale(1 / 1.2, 1 / 1.2)
        ))
        toolbar.addAction(QAction(
            std_icon(self, QStyle.SP_BrowserReload), "Fit",
            self, triggered=self.canvas.fit_to_view
        ))

    def _build_status_bar(self) -> None:
        status = QStatusBar()
        self.setStatusBar(status)
        self.coord_label = QLabel("X:    0.00 mm   Y:    0.00 mm")
        self.coord_label.setStyleSheet(
            "QLabel { background-color: #1e1e2e; color: #89b4fa;"
            "  padding: 3px 10px; border-radius: 3px;"
            "  font-family: 'Courier New', monospace; font-weight: bold; }"
        )
        status.addPermanentWidget(self.coord_label)
        self.plot_size_label = QLabel("216 × 279 mm")
        self.plot_size_label.setStyleSheet(
            "QLabel { background-color: #1e1e2e; color: #a6e3a1;"
            "  padding: 3px 10px; border-radius: 3px; font-weight: bold; }"
        )
        status.addPermanentWidget(self.plot_size_label)

        # ----- Machine state badge (right-most) ---------------------------
        # GRBL pushes its state through ``machine_position_changed`` every
        # ~250 ms; we paint it here in colour-coded form so the user can
        # see at a glance whether the controller is Idle / Run / Hold /
        # Alarm / Door / Home / Sleep / Check / Jog without scanning the
        # console log.
        self.state_badge = QLabel("● OFFLINE")
        self.state_badge.setMinimumWidth(140)
        self.state_badge.setAlignment(Qt.AlignCenter)
        self._set_state_badge("Offline")
        status.addPermanentWidget(self.state_badge)

        status.showMessage(f"Ready | G-code folder: {self.gcode_folder}")
        self.status = status

    # ------------------------------------------------------------------
    def _set_state_badge(self, state: str) -> None:
        """Update the right-corner state badge with a GRBL state string.

        Accepts the raw state token from GRBL status reports
        (``Idle``, ``Run``, ``Hold:0``, ``Hold:1``, ``Alarm``,
        ``Door:1``, ``Home``, ``Sleep``, ``Check``, ``Jog``).
        """
        # GRBL appends ``:N`` for sub-states (Hold:0 = parking,
        # Hold:1 = held, Door:1 etc.) — strip it for the lookup but
        # keep the full string for display.
        token = (state or "Unknown").split(":", 1)[0]
        palette = {
            "Idle":    ("#4caf50", "white",  "● IDLE"),
            "Run":     ("#2196f3", "white",  "▶ RUN"),
            "Jog":     ("#03a9f4", "white",  "↔ JOG"),
            "Hold":    ("#ff9800", "black",  "⏸ HOLD"),
            "Alarm":   ("#d32f2f", "white",  "⚠ ALARM"),
            "Door":    ("#ff5722", "white",  "✋ DOOR"),
            "Home":    ("#9c27b0", "white",  "⌂ HOMING"),
            "Sleep":   ("#607d8b", "white",  "🌙 SLEEP"),
            "Check":   ("#795548", "white",  "✓ CHECK"),
            "Offline": ("#424242", "#bbbbbb", "● OFFLINE"),
        }
        bg, fg, label = palette.get(token, ("#424242", "#bbbbbb",
                                            f"? {token.upper()}"))
        # Tack the sub-state on (e.g. "HOLD:0") so the user sees the
        # exact GRBL reply.
        if state and ":" in state:
            label = f"{label} ({state.split(':', 1)[1]})"
        self.state_badge.setText(label)
        self.state_badge.setToolTip(
            f"<b>GRBL state:</b> {state}<br>"
            "Updated live from <code>?</code> status reports.<br>"
            "If this stays on <b>HOLD</b> when you didn't press pause, "
            "click <b>KILL ALARM</b> on the toolbar to clear it."
        )
        self.state_badge.setStyleSheet(
            f"QLabel {{ background-color: {bg}; color: {fg};"
            f"  padding: 3px 12px; border-radius: 3px;"
            f"  font-weight: bold; font-family: 'Courier New', monospace;"
            f"  font-size: 12px; border: 1px solid #1e1e2e; }}"
        )

    # ------------------------------------------------------------------
    # Slots
    # ------------------------------------------------------------------
    def _update_coords(self, x_mm: float, y_mm: float) -> None:
        self.coord_label.setText(f"X: {x_mm:7.2f} mm   Y: {y_mm:7.2f} mm")

    def _on_machine_state_update(self, position: dict) -> None:
        """Called every time GRBL pushes a status report.  Updates the
        right-corner state badge and turns the ESTOP / Home / Unlock
        buttons on or off depending on whether the controller is in a
        usable state."""
        state = position.get("state", "")
        if state:
            self._set_state_badge(state)
        # Sync the ALARM kill button: enable only when an alarm is
        # actually pending — otherwise it's a no-op that confuses people.
        if hasattr(self, "kill_alarm_action"):
            in_alarm = state.startswith("Alarm") or state.startswith("Door")
            self.kill_alarm_action.setEnabled(in_alarm)

    def _on_connection_state_for_badge(self, connected: bool, _msg: str) -> None:
        if not connected:
            self._set_state_badge("Offline")
            if hasattr(self, "kill_alarm_action"):
                self.kill_alarm_action.setEnabled(False)

    def _update_plot_size(self, w: int, h: int) -> None:
        self.canvas.set_plot_size(w, h)
        self.gcode_generator.plot_width = w
        self.gcode_generator.plot_height = h
        self.gcode_generator.max_x = w
        self.gcode_generator.max_y = h
        self.plot_size_label.setText(f"{w} × {h} mm")
        QTimer.singleShot(100, self.canvas.fit_to_view)

    # ------------------------------------------------------------------
    def _sync_worker_settings(self) -> None:
        sp = self.settings_panel
        self.serial_worker.configure_cncjs(
            host=sp.cncjs_host_input.text().strip() or "127.0.0.1",
            port=sp.cncjs_port_input.value(),
            controller_type="Grbl",
            token=sp.cncjs_token_input.text(),
            username=sp.cncjs_user_input.text(),
            password=sp.cncjs_password_input.text(),
            command_path=sp.path_input.text(),
            watch_directory=str(self.gcode_folder),
            auto_open_browser=sp.auto_browser_check.isChecked(),
        )

    def _sync_tool_changer(self) -> None:
        cfg = self.pen_changer_panel.get_tool_changer_config()
        self.gcode_generator.configure_tool_changer(**cfg)
        # Push slot positions onto the canvas so the user can SEE
        # exactly where each pen is supposed to live.  When the
        # changer is disabled we hide the markers to keep the work
        # area uncluttered.
        try:
            self.canvas.update_pen_rack(
                dict(self.pen_changer_panel.slot_data),
                enabled=bool(cfg.get("enabled", False)),
            )
        except Exception:
            log.exception("Failed to refresh canvas pen rack")

    def _auto_find_cncjs(self) -> None:
        detected = self.serial_worker.resolve_cncjs_command()
        if detected:
            self.settings_panel.path_input.setText(detected)
            self._sync_worker_settings()
            self.status.showMessage(f"Found cncjs: {detected}")
        else:
            QMessageBox.warning(
                self, "cncjs Not Found",
                "cncjs not found in PATH.\n\n"
                "Install with:\n  npm install -g cncjs"
            )

    def _handle_start_cncjs(self, force_open_browser: bool = False) -> None:
        self._sync_worker_settings()
        if not SOCKETIO_AVAILABLE and self.serial_worker.mode == "cncjs":
            QMessageBox.warning(
                self, "cncjs not available",
                "Install python-socketio to use cncjs mode:\n"
                "  pip install 'python-socketio[client]'"
            )
            return
        ok = self.serial_worker.start_cncjs_server(
            open_browser=force_open_browser
        )
        if not ok:
            return
        if force_open_browser:
            self.serial_worker.open_cncjs_browser()

    def _open_cncjs_browser(self) -> None:
        self._sync_worker_settings()
        if self.serial_worker.start_cncjs_server(open_browser=True):
            self.serial_worker.open_cncjs_browser()

    def _auto_connect_on_launch(self) -> None:
        sp = self.settings_panel
        # Always make sure cncjs is running in the background when the
        # user is in cncjs mode — they shouldn't have to remember to
        # tick a checkbox.  start_cncjs_server is idempotent so calling
        # it when cncjs is already up is a no-op.  Browser is NOT opened
        # automatically; user clicks the toolbar "Open cncjs" button if
        # they want to inspect it.
        if self.serial_worker.mode == "cncjs" and SOCKETIO_AVAILABLE:
            self.serial_worker.start_cncjs_server(
                open_browser=sp.auto_browser_check.isChecked()
            )
            self.status.showMessage(
                "✨ cncjs starting in background — click 'Open cncjs' to view"
            )
        if not sp.auto_connect_check.isChecked():
            return
        port = self.serial_panel.selected_port() or autodetect_port()
        if not port:
            return
        baud = self.serial_panel.selected_baudrate()
        self.serial_worker.connect_serial(port, baud)

    # ------------------------------------------------------------------
    def _on_connection_change(self, connected: bool, message: str) -> None:
        prefix = "✅" if connected else "⚫"
        self.status.showMessage(f"{prefix} {message}")

    def _on_server_status_change(self, running: bool, message: str) -> None:
        self.settings_panel.update_grbl_status(running, message)

    # ------------------------------------------------------------------
    # Live progress narrator — drives the canvas activity banner and
    # the top-strip active-pen highlight while a job streams.
    # ------------------------------------------------------------------
    def _on_stream_progress(self, line_idx: int, total: int) -> None:
        if not self._stream_lines:
            return
        # Scan ALL G-code lines from the start up to the current line.
        # We're looking for the most recent colour-group marker emitted
        # by the generator.  The markers use a tiny, fixed grammar so a
        # regex isn't needed.
        upto = min(line_idx + 1, len(self._stream_lines))
        latest_swap_start = None         # "; ===== Color RGB(...) -> Tool T..."
        latest_drawing = None            # "; -- Drawing with ... --"
        for i in range(upto - 1, -1, -1):
            ln = self._stream_lines[i].strip()
            if (latest_drawing is None
                    and ln.startswith("; -- Drawing with ")):
                latest_drawing = ln
                if latest_swap_start is not None:
                    break
            if (latest_swap_start is None
                    and ln.startswith("; ===== Color RGB")):
                latest_swap_start = ln
                if latest_drawing is not None:
                    break

        # If we never saw any marker, leave the banner alone.
        if latest_drawing is None and latest_swap_start is None:
            return

        # Most recent event wins.  If the swap-start comes AFTER the
        # last "Drawing with", we're mid-swap; otherwise we're drawing.
        drawing_idx = -1
        swap_idx = -1
        for i in range(upto - 1, -1, -1):
            ln = self._stream_lines[i].strip()
            if drawing_idx < 0 and ln.startswith("; -- Drawing with "):
                drawing_idx = i
            if swap_idx < 0 and ln.startswith("; ===== Color RGB"):
                swap_idx = i
            if drawing_idx >= 0 and swap_idx >= 0:
                break

        if swap_idx > drawing_idx and latest_swap_start:
            # Mid-swap: "Picking up <Pen> (T<n>)"
            try:
                tool_part = latest_swap_start.split("-> Tool ", 1)[1]
                tool_token = tool_part.split()[0]      # "T2"
                tool = int(tool_token.lstrip("T"))
                name = tool_part.split("(", 1)[1].rstrip(") =")
                rgb_part = latest_swap_start.split("RGB(", 1)[1].split(")", 1)[0]
                rgb = tuple(int(v) for v in rgb_part.split(","))
            except Exception:
                return
            if self._last_announced_tool != ("swap", tool):
                self.canvas.set_active_pen(tool)
                self.canvas.set_activity_text(
                    f"🛠 Picking up {name} pen (T{tool})", rgb
                )
                self._last_announced_tool = ("swap", tool)
        elif latest_drawing:
            # Currently drawing — extract pen name and announce.
            name = latest_drawing.split("Drawing with ", 1)[1].rstrip("-").strip()
            # Resolve tool number + rgb from PEN_PRESETS by name match.
            from .config import PEN_PRESETS
            tool = None
            rgb = None
            for p in PEN_PRESETS:
                if p["name"].lower() == name.lower():
                    tool = p["tool"]
                    rgb = p["rgb"]
                    break
            if self._last_announced_tool != ("draw", tool):
                if tool is not None:
                    self.canvas.set_active_pen(tool)
                self.canvas.set_activity_text(
                    f"▶ Drawing with {name}" + (f" (T{tool})" if tool else ""),
                    rgb,
                )
                self._last_announced_tool = ("draw", tool)

    def _on_streaming_finished(self, success: bool, message: str) -> None:
        # Drop the live progress narrator's bookkeeping so the next job
        # starts from a clean slate.  Banner is faded out, top-strip
        # highlight is removed.
        self._stream_lines = []
        self._last_announced_tool = None
        try:
            if success:
                self.canvas.set_activity_text(
                    "✅ Job complete", (76, 175, 80)
                )
                # Auto-hide the success banner after a couple of seconds.
                QTimer.singleShot(2500,
                                  lambda: self.canvas.set_activity_text(""))
            else:
                self.canvas.set_activity_text(
                    f"⚠ {message[:40]}", (211, 47, 47)
                )
                QTimer.singleShot(3500,
                                  lambda: self.canvas.set_activity_text(""))
            self.canvas.set_active_pen(None)
        except Exception:
            log.exception("Failed to clear canvas activity overlay")

        if success:
            self.status.showMessage(f"✅ {message}")
            QMessageBox.information(self, "Job Complete",
                                    f"Drawing finished.\n\n{message}")
        else:
            self.status.showMessage(f"⚠ {message}")
        # Keep the live cursor visible after the job so the user can see
        # where the head parked, but do not hide it — it'll be re-aimed
        # by the next status report.

    # ------------------------------------------------------------------
    # File operations
    # ------------------------------------------------------------------
    def _new_canvas(self) -> None:
        if self.canvas.strokes:
            reply = QMessageBox.question(
                self, "New Canvas", "Clear and start new?",
                QMessageBox.Yes | QMessageBox.No,
            )
            if reply != QMessageBox.Yes:
                return
        self.canvas.clear_canvas()

    def _clear_canvas(self) -> None:
        if not self.canvas.strokes:
            return
        reply = QMessageBox.question(
            self, "Clear Canvas", "Clear all drawings?",
            QMessageBox.Yes | QMessageBox.No,
        )
        if reply == QMessageBox.Yes:
            self.canvas.clear_canvas()

    def _delete_selected(self) -> None:
        for item in self.canvas.scene.selectedItems():
            if item.zValue() >= 0:
                self.canvas.scene.removeItem(item)
                self.canvas.strokes = [s for s in self.canvas.strokes
                                       if s.get("item") is not item]

    def _import_file(self) -> None:
        filt = ";;".join([f"{name} ({ext})"
                          for name, ext in SUPPORTED_IMPORT_FORMATS.items()])
        path, _ = QFileDialog.getOpenFileName(
            self, "Import drawing", "", filt
        )
        if not path:
            return
        ext = Path(path).suffix.lower()
        allowed = {".svg", ".dxf", ".png", ".jpg", ".jpeg", ".bmp",
                   ".gif", ".hpgl", ".plt", ".gcode", ".nc", ".ngc", ".csv"}
        if ext not in allowed:
            QMessageBox.warning(self, "Unsupported",
                                f"{ext} is not supported.")
            return
        if self.canvas.import_image(path):
            self.status.showMessage(f"Imported: {os.path.basename(path)}")
        else:
            QMessageBox.warning(
                self, "Import error",
                f"Cannot load {ext}.\nConvert DXF / HPGL to SVG first."
            )

    def _export_image(self) -> None:
        default = str(self.gcode_folder.parent / "drawing.png")
        path, _ = QFileDialog.getSaveFileName(
            self, "Export", default,
            "PNG (*.png);;JPEG (*.jpg);;BMP (*.bmp)"
        )
        if path and self.canvas.export_image(path):
            self.status.showMessage(f"Exported: {path}")
            QMessageBox.information(self, "Success", f"Exported:\n{path}")

    def _save_gcode(self) -> None:
        if not self.canvas.strokes:
            QMessageBox.warning(self, "Empty", "Draw something first!")
            return
        self._sync_tool_changer()
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        default = str(self.gcode_folder / f"drawing_{ts}.gcode")
        path, _ = QFileDialog.getSaveFileName(
            self, "Save G-code", default,
            "G-code (*.gcode *.nc *.ngc);;All Files (*)"
        )
        if not path:
            return
        gcode = self.gcode_generator.generate(self.canvas.strokes)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(gcode)
        self.status.showMessage(f"Saved: {path}")
        QMessageBox.information(self, "Saved", f"G-code saved:\n{path}")

    def _save_gcode_snapshot(self):
        self._sync_tool_changer()
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        filepath = self.gcode_folder / f"drawing_{ts}.gcode"
        gcode = self.gcode_generator.generate(self.canvas.strokes)
        with open(filepath, "w", encoding="utf-8") as fh:
            fh.write(gcode)
        return filepath, gcode

    def _stream_to_machine(self) -> None:
        if not self.canvas.strokes:
            QMessageBox.warning(self, "Empty", "Draw something first!")
            return
        self._sync_worker_settings()
        self._sync_tool_changer()

        missing = self.gcode_generator.missing_pen_slots(self.canvas.strokes)
        if missing:
            names = ", ".join(self.gcode_generator._tool_name(t) for t in missing)
            reply = QMessageBox.warning(
                self, "Missing Pen Slots",
                f"Auto pen changer is enabled but no positions saved for:\n\n"
                f"{names}\n\n"
                f"Continue anyway?  These pens will use a manual "
                f"M0 pause.",
                QMessageBox.Yes | QMessageBox.No,
            )
            if reply != QMessageBox.Yes:
                return

        # Make sure cncjs is alive when the user is in cncjs mode so the
        # job appears in the cncjs web UI alongside our own canvas.
        if self.serial_worker.mode == "cncjs" and SOCKETIO_AVAILABLE:
            self.serial_worker.start_cncjs_server(open_browser=False)

        if not self.serial_worker.is_connected:
            port = self.serial_panel.selected_port() or autodetect_port()
            baud = self.serial_panel.selected_baudrate()
            self.serial_worker.connect_serial(port, baud)

        if not self.serial_worker.is_connected:
            self._show_not_connected_dialog()
            return

        filepath, gcode = self._save_gcode_snapshot()
        colors = set()
        for s in self.canvas.strokes:
            color = s.get("color")
            if color is None:
                continue
            colors.add((color.red(), color.green(), color.blue()))

        msg = QMessageBox(self)
        msg.setIcon(QMessageBox.Question)
        msg.setWindowTitle("Start Drawing?")
        msg.setText("<h3>Ready to stream G-code</h3>")
        info = (
            f"<b>Strokes:</b> {len(self.canvas.strokes)}<br>"
            f"<b>Colors:</b> {len(colors)}<br>"
            f"<b>Plot:</b> {self.canvas.plot_width_mm}×{self.canvas.plot_height_mm} mm<br>"
            f"<b>File:</b> {filepath.name}<br>"
            f"<b>Auto pen change:</b> "
            f"{'ENABLED' if self.gcode_generator.tool_change_enabled else 'disabled'}<br>"
            f"<b>Mode:</b> {self.serial_worker.mode}<br><br>"
            "<b>SAFETY CHECK</b><br>"
            "• Machine homed?<br>"
            "• Path clear?<br>"
            "• Correct pens loaded in rack?<br>"
            "• Paper secured?<br>"
            "• Emergency stop accessible?"
        )
        msg.setInformativeText(info)
        msg.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
        if msg.exec_() != QMessageBox.Yes:
            return

        # ------- Stash the G-code lines so the live progress hook can
        # ------- announce colour-group transitions on the canvas.
        self._stream_lines = gcode.splitlines()
        self._last_announced_tool = None
        self._announce_first_pen_from_strokes()

        if self.serial_worker.send_gcode(gcode, filepath.name):
            self.status.showMessage(f"Streaming... ({filepath.name})")
        else:
            self.canvas.set_activity_text("")
            self.canvas.set_active_pen(None)
            QMessageBox.critical(self, "Error", "Stream failed.")

    # ------------------------------------------------------------------
    def _announce_first_pen_from_strokes(self) -> None:
        """Highlight the first colour we're about to draw with so the
        user can see the active pen on the top palette strip the
        moment streaming starts.
        """
        first_color = None
        for s in self.canvas.strokes:
            c = s.get("color")
            if c is not None:
                first_color = (c.red(), c.green(), c.blue())
                break
        if first_color is None:
            return
        tool = self.gcode_generator.color_to_tool(QColor(*first_color))
        from .config import TOOL_COLOR_MAP
        name = TOOL_COLOR_MAP.get(tool, {}).get("name", f"Pen {tool}")
        self.canvas.set_active_pen(tool)
        self.canvas.set_activity_text(
            f"▶ Drawing with {name} (T{tool})", first_color
        )
        self._last_announced_tool = tool

    def _show_not_connected_dialog(self) -> None:
        msg = QMessageBox(self)
        msg.setIcon(QMessageBox.Warning)
        msg.setWindowTitle("Not Connected")
        msg.setText("<h3>Machine not connected</h3>")
        msg.setInformativeText(
            "1. Choose Connection Mode (Direct / cncjs)<br>"
            "2. Select the correct USB port in the Connect tab<br>"
            "3. Click CONNECT MACHINE<br><br>"
            "You can still save the file to the cncjs watch folder."
        )
        send_btn = msg.addButton("Save For cncjs", QMessageBox.AcceptRole)
        msg.addButton("Try Again", QMessageBox.ActionRole)
        msg.addButton(QMessageBox.Cancel)
        msg.exec_()
        if msg.clickedButton() == send_btn:
            self._send_to_cncjs()

    def _send_to_cncjs(self) -> None:
        if not self.canvas.strokes:
            QMessageBox.warning(self, "Empty", "Draw something first!")
            return
        filepath, _ = self._save_gcode_snapshot()
        self._sync_worker_settings()
        self.serial_worker.start_cncjs_server(open_browser=True)
        self.serial_worker.open_cncjs_browser()
        QMessageBox.information(
            self, "Saved For cncjs",
            f"G-code saved to:\n{filepath}\n\n"
            f"Watch folder:\n{self.gcode_folder}\n\n"
            "If the file does not appear in the cncjs browser, click "
            "Refresh in the cncjs UI."
        )
        self.status.showMessage(f"Saved for cncjs: {filepath.name}")

    def _open_gcode_folder(self) -> None:
        self.settings_panel._open_folder()

    # ------------------------------------------------------------------
    def _reset_panel_layout(self) -> None:
        """Re-dock the two main side panels and close every detached
        Window-menu popup so the user is back to a known-good layout.
        """
        for dock, area in (
            (self.left_dock, Qt.LeftDockWidgetArea),
            (self.right_dock, Qt.RightDockWidgetArea),
        ):
            if dock.isFloating():
                dock.setFloating(False)
            self.addDockWidget(area, dock)
            dock.show()
            dock.raise_()
        # Close any free-floating panel windows the user opened from the
        # Windows menu — they'll re-open in their default position next
        # time the user picks the menu item.
        for win in list(self._panel_windows.values()):
            try:
                win.close()
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Detached-window plumbing
    # ------------------------------------------------------------------
    def _open_panel_window(self, key: str, title: str,
                           panel_widget: QWidget,
                           default_size: tuple = (640, 520)) -> None:
        """Show ``panel_widget`` inside its own top-level window.

        The panel is REPARENTED into a freshly-built ``QMainWindow`` the
        first time this is called for ``key`` — subsequent calls just
        re-show the existing window.  Closing the window does NOT
        destroy the panel; it stays alive (and keeps all its state)
        ready to be reopened.
        """
        win = self._panel_windows.get(key)
        if win is None or not _safe_is_alive(win):
            win = QMainWindow(self)
            win.setWindowFlag(Qt.Window, True)
            win.setWindowTitle(title)
            scroll = QScrollArea(win)
            scroll.setWidgetResizable(True)
            scroll.setWidget(panel_widget)
            win.setCentralWidget(scroll)
            try:
                w, h = default_size
                win.resize(int(w), int(h))
            except Exception:
                win.resize(640, 520)
            self._panel_windows[key] = win

        win.show()
        win.raise_()
        win.activateWindow()

    # ------------------------------------------------------------------
    # Multi-color toggle
    # ------------------------------------------------------------------
    def _toggle_multi_color(self, enabled: bool) -> None:
        self.gcode_generator.multi_color = bool(enabled)
        self.multi_color_action.setText(
            f"Multi-Color: {'ON' if enabled else 'OFF'}"
        )
        self.status.showMessage(
            "Multi-color G-code: "
            + ("ENABLED — pen will swap between colours"
               if enabled else
               "DISABLED — single-pen mode (no swaps)")
        )

    # ------------------------------------------------------------------
    # Emergency-stop one-shot
    # ------------------------------------------------------------------
    def _emergency_stop(self) -> None:
        """Slam the brakes — GRBL 0x18 soft-reset.

        Always available (no connection / state guard) because if the
        head is about to crash you don't want a stale "disconnected"
        flag to swallow the click.  We still log a warning if there's
        nothing to send to.
        """
        try:
            self.serial_worker.emergency_stop()
        except Exception:
            log.exception("emergency_stop() raised")
        # Mark the running job as aborted so any in-flight progress
        # signals don't keep narrating into a dead stream.
        self._stream_lines = []
        self._last_announced_tool = None
        self.canvas.set_active_pen(None)
        self.canvas.set_activity_text(
            "🛑 EMERGENCY STOP — controller halted, ALARM state.  "
            "Click KILL ALARM and re-home before running another job.",
            (176, 0, 32),
        )
        # Auto-clear the banner after 6 s — the state badge keeps
        # showing ALARM as long as that's true, so the user is still
        # informed even after the banner fades.
        QTimer.singleShot(6000, lambda: self.canvas.set_activity_text(""))
        self.status.showMessage(
            "🛑 EMERGENCY STOP sent — controller in ALARM. "
            "Click KILL ALARM and re-home before running another job."
        )
        log.warning("EMERGENCY STOP triggered by user")

    # ------------------------------------------------------------------
    # Kill-alarm one-shot
    # ------------------------------------------------------------------
    def _kill_alarm(self) -> None:
        """Bring GRBL out of Alarm / Door / Hold back to Idle.

        The sequence below works for every state we've seen in the wild:
        ``~`` un-holds, ``$X`` clears the alarm, ``?`` refreshes the
        status so the badge flips green within ~250 ms.
        """
        if not self.serial_worker.is_connected:
            QMessageBox.information(
                self, "KILL ALARM",
                "Not connected.  Connect to the machine first, then "
                "click KILL ALARM."
            )
            return
        # First: nudge out of Hold (no-op if not in Hold).
        try:
            self.serial_worker.resume()
        except Exception:
            log.exception("kill_alarm: resume() failed")
        # Then clear the alarm itself.
        try:
            self.serial_worker.unlock_alarm()
        except Exception:
            log.exception("kill_alarm: unlock_alarm() failed")
        # Finally: refresh status so the user sees Idle ASAP.
        QTimer.singleShot(150, self.serial_worker.get_status)
        self.status.showMessage(
            "🔓 KILL ALARM sent — controller should return to Idle. "
            "Re-home before running another job."
        )

    def _show_about(self) -> None:
        QMessageBox.about(
            self, "About",
            f"<h2>{APP_NAME}</h2>"
            f"<p>{APP_TAGLINE} v{APP_VERSION}</p>"
            "<p>Modular plotter controller with both Direct serial and "
            "cncjs Socket.IO backends.</p>"
            "<ul>"
            "<li>Pi-friendly rendering (grid in drawBackground)</li>"
            "<li>Direct pyserial backend with character-counting protocol</li>"
            "<li>cncjs Socket.IO backend (positional args, fixed)</li>"
            "<li>Automatic pen changer with X/Y/Z slot mapping</li>"
            "<li>Real text-to-paths conversion</li>"
            "<li>Industrial dark theme — no white-on-white text</li>"
            "</ul>"
        )

    # ------------------------------------------------------------------
    # Settings save/load
    # ------------------------------------------------------------------
    def _load_settings(self) -> None:
        s = load_settings()
        sp = self.settings_panel
        sp.cncjs_host_input.setText(s.get("cncjs_host", "127.0.0.1"))
        sp.cncjs_port_input.setValue(int(s.get("cncjs_port", 8000)))
        sp.path_input.setText(s.get("cncjs_command", ""))
        sp.cncjs_token_input.setText(s.get("cncjs_token", ""))
        sp.cncjs_user_input.setText(s.get("cncjs_username", ""))
        sp.cncjs_password_input.setText(s.get("cncjs_password", ""))
        sp.auto_launch_check.setChecked(bool(s.get("auto_launch_cncjs", False)))
        sp.auto_browser_check.setChecked(bool(s.get("auto_open_browser", False)))
        sp.auto_connect_check.setChecked(bool(s.get("auto_connect_machine", True)))
        sp.plot_width_input.setValue(int(s.get("plot_width", 216)))
        sp.plot_height_input.setValue(int(s.get("plot_height", 279)))
        sp.feed_input.setValue(int(s.get("feed_rate", 3000)))
        sp.pen_up_input.setValue(float(s.get("pen_up_z", 5.0)))
        sp.pen_down_input.setValue(float(s.get("pen_down_z", 0.0)))

        self.serial_panel.select_baudrate(s.get("baud_rate", "115200"))
        port = s.get("machine_port", "")
        if port:
            self.serial_panel.select_port(port)
        # Default to cncjs so the web UI is available out of the box.
        mode = s.get("connection_mode", "cncjs")
        self.serial_panel.select_mode(mode)
        self.serial_worker.set_mode(mode)

        if "tool_changer" in s:
            self.pen_changer_panel.load_from_settings(s["tool_changer"])

        if "homing" in s:
            self.homing_panel.load_homing_config(s["homing"])

        # ----- Calibration scales -----
        cal = s.get("calibration", {}) or {}
        x_s = float(cal.get("x_scale", 1.0))
        y_s = float(cal.get("y_scale", 1.0))
        z_s = float(cal.get("z_scale", 1.0))
        self.gcode_generator.set_calibration_scales(x_s, y_s, z_s)
        # CRITICAL: also push the same scales into the SerialWorker
        # so paper-setup jogs (which bypass the generator) physically
        # move the distance the user requested.  Without this, jogging
        # 1 mm in the Jog panel would actually move 10 mm when the
        # firmware has wrong steps/mm.
        self.serial_worker.set_calibration_scales(x_s, y_s, z_s)
        # Refresh the live-calibration banner shown in the Jog panel
        # so the user can see at a glance which axes are calibrated.
        self.jog_panel.refresh_calibration_display()
        self.calibration_panel.load_from_settings(cal)
        # When the user APPLYs in software-mode, push the new scales
        # into the live generator + persist to settings.json.
        self.calibration_panel.software_scales_changed.connect(
            self._on_software_calibration_changed
        )

        # Multi-color toggle state — defaults to ON if unset.
        multi_color = bool(s.get("multi_color", True))
        if hasattr(self, "multi_color_action"):
            self.multi_color_action.setChecked(multi_color)
            # The toggled signal will sync gcode_generator + label;
            # call the slot directly in case the value didn't change
            # (setChecked won't emit if it was already True).
            self._toggle_multi_color(multi_color)
        else:
            self.gcode_generator.multi_color = multi_color

    def _save_settings_to_disk(self) -> None:
        sp = self.settings_panel
        snapshot = {
            "connection_mode": self.serial_worker.mode,
            "machine_port": self.serial_panel.selected_port(),
            "baud_rate": str(self.serial_panel.selected_baudrate()),
            "cncjs_host": sp.cncjs_host_input.text(),
            "cncjs_port": sp.cncjs_port_input.value(),
            "cncjs_command": sp.path_input.text(),
            "cncjs_token": sp.cncjs_token_input.text(),
            "cncjs_username": sp.cncjs_user_input.text(),
            "cncjs_password": sp.cncjs_password_input.text(),
            "auto_launch_cncjs": sp.auto_launch_check.isChecked(),
            "auto_open_browser": sp.auto_browser_check.isChecked(),
            "auto_connect_machine": sp.auto_connect_check.isChecked(),
            "plot_width": sp.plot_width_input.value(),
            "plot_height": sp.plot_height_input.value(),
            "feed_rate": sp.feed_input.value(),
            "pen_up_z": sp.pen_up_input.value(),
            "pen_down_z": sp.pen_down_input.value(),
            "tool_changer": self.pen_changer_panel.get_tool_changer_config(),
            "homing": self.homing_panel.get_homing_config(),
            "calibration": self.calibration_panel.get_settings(),
            "multi_color": bool(
                getattr(self.gcode_generator, "multi_color", True)
            ),
        }
        save_settings(snapshot)

    def _on_software_calibration_changed(self, x: float, y: float,
                                          z: float, _last: dict) -> None:
        """Live-apply software calibration changes from the wizard.

        Pushes the new scales into the running generator (so the next
        STREAM TO MACHINE is corrected), into the serial worker (so
        paper-setup jogs scale correctly), and persists everything to
        ``settings.json`` immediately so a crash doesn't lose the work.
        """
        self.gcode_generator.set_calibration_scales(x, y, z)
        self.serial_worker.set_calibration_scales(x, y, z)
        # Update the live-calibration banner in the Jog panel so the
        # user immediately sees the new scales without having to
        # restart the app.
        self.jog_panel.refresh_calibration_display()
        self._save_settings_to_disk()
        self.status.showMessage(
            f"Software calibration: X×{x:.4f}  Y×{y:.4f}  Z×{z:.4f}  "
            "(saved to settings.json)"
        )
        # Visible feedback on the canvas too — bright green if all
        # axes are 1.0, otherwise yellow to indicate live compensation.
        active = self.gcode_generator.calibration_active
        bg = (249, 226, 175) if active else (166, 227, 161)
        msg = (f"📐 Calibration X×{x:.4f}  Y×{y:.4f}  Z×{z:.4f}"
               if active else "📐 Calibration cleared (×1.0000 on all axes)")
        self.canvas.set_activity_text(msg, bg)
        QTimer.singleShot(3500, lambda: self.canvas.set_activity_text(""))

    # ------------------------------------------------------------------
    def closeEvent(self, event):  # noqa: N802
        if self.serial_worker.is_connected:
            reply = QMessageBox.question(
                self, "Exit",
                "Disconnect machine and exit?",
                QMessageBox.Yes | QMessageBox.No,
            )
            if reply != QMessageBox.Yes:
                event.ignore()
                return
        try:
            self.serial_worker.shutdown()
        except Exception:
            log.exception("Error during serial worker shutdown")
        try:
            self._save_settings_to_disk()
        except Exception:
            log.exception("Failed to save settings")
        event.accept()
