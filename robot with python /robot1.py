#!/usr/bin/env python3
"""
CNCjs Plotter Bridge - Industrial Edition v3.1.0
Full working version with:
  - FIXED cncjs Socket.IO streaming (positional args, not tuples)
  - Automated pen changing with XYZ position mapping per pen
  - Servo A gripper open/close
  - Jog-and-save current machine position
  - Resume drawing after tool change
  - Raspberry Pi 4 optimized rendering (drawBackground for grid)
  - Standard Qt icons (cross-platform visible)
  - Scrollable panels for small Pi displays
"""

import sys
import os
import json
import socket
import subprocess
import threading
import time
import glob
import re
import shutil
import webbrowser
from datetime import datetime
from pathlib import Path
from urllib.error import URLError
from urllib.parse import urljoin
from urllib.request import Request, urlopen


def _reexec_with_clean_env_if_needed():
    if os.environ.get("ROBOT_ENV_CLEAN") == "1":
        return
    ld_library_path = os.environ.get("LD_LIBRARY_PATH", "")
    has_snap_vars = any(k == "SNAP" or k.startswith("SNAP_") for k in os.environ)
    has_snap_lib = bool(os.environ.get("SNAP_LIBRARY_PATH"))
    has_risky_lib_path = any(
        token in ld_library_path for token in ("/snap/", "/opt/ros/", "/opt/MVS/")
    )
    if not (has_snap_vars or has_snap_lib or has_risky_lib_path):
        return
    keep_keys = {
        "HOME", "USER", "LOGNAME", "PATH", "SHELL", "LANG", "LC_ALL",
        "DISPLAY", "WAYLAND_DISPLAY", "XDG_SESSION_TYPE", "XDG_CURRENT_DESKTOP",
        "DESKTOP_SESSION", "XAUTHORITY", "XDG_RUNTIME_DIR",
        "DBUS_SESSION_BUS_ADDRESS", "TERM", "COLORTERM", "QT_QPA_PLATFORM",
    }
    clean_env = {k: v for k, v in os.environ.items() if k in keep_keys and v}
    clean_env.setdefault("PATH", "/usr/local/bin:/usr/bin:/bin")
    clean_env["ROBOT_ENV_CLEAN"] = "1"
    os.execvpe(sys.executable, [sys.executable] + sys.argv, clean_env)


_reexec_with_clean_env_if_needed()


def _is_raspberry_pi():
    try:
        with open('/proc/cpuinfo', 'r') as f:
            content = f.read().lower()
            return 'raspberry pi' in content or 'bcm' in content
    except Exception:
        return False

IS_RPI = _is_raspberry_pi()

try:
    import serial
    import serial.tools.list_ports
    SERIAL_AVAILABLE = True
except ImportError:
    SERIAL_AVAILABLE = False
    print("WARNING: pyserial not installed. Run: pip install pyserial")

try:
    import socketio as socketio_client
    SOCKETIO_AVAILABLE = True
except ImportError:
    socketio_client = None
    SOCKETIO_AVAILABLE = False
    print("WARNING: python-socketio not installed. Run: pip install 'python-socketio[client]'")

from PyQt5.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout,
                              QHBoxLayout, QPushButton, QLabel, QColorDialog,
                              QFileDialog, QLineEdit, QComboBox, QSlider,
                              QGraphicsView, QGraphicsScene, QGraphicsPathItem,
                              QToolBar, QAction, QStatusBar, QDockWidget,
                              QListWidget, QListWidgetItem, QMessageBox,
                              QInputDialog, QSpinBox, QFrame, QSplitter,
                              QGraphicsTextItem, QFontDialog, QCheckBox,
                              QGroupBox, QFormLayout, QTabWidget, QTextEdit,
                              QProgressBar, QDoubleSpinBox, QGraphicsPixmapItem,
                              QDialog, QDialogButtonBox, QFontComboBox,
                              QGraphicsItem, QGraphicsRectItem, QGraphicsLineItem,
                              QGraphicsEllipseItem, QGraphicsSimpleTextItem,
                              QGridLayout, QStyle, QSizePolicy, QScrollArea)
from PyQt5.QtCore import (Qt, QPointF, QRectF, QSize, QTimer, pyqtSignal,
                          QThread, QObject, QLineF)
from PyQt5.QtGui import (QPainter, QPen, QColor, QBrush, QPainterPath,
                         QPixmap, QImage, QFont, QIcon, QCursor, QKeySequence,
                         QTransform, QPalette, QFontMetrics, QFontDatabase)


APP_NAME = "CNCjs Plotter Bridge - Industrial Edition"
APP_VERSION = "3.1.0"

SUPPORTED_IMPORT_FORMATS = {
    "All Supported": "*.svg *.dxf *.png *.jpg *.jpeg *.bmp *.gif *.hpgl *.plt *.gcode *.nc *.ngc *.csv",
    "SVG Vector": "*.svg",
    "DXF CAD": "*.dxf",
    "HPGL Plot": "*.hpgl *.plt",
    "Raster Images": "*.png *.jpg *.jpeg *.bmp *.gif",
    "G-Code": "*.gcode *.nc *.ngc",
    "CSV Data": "*.csv",
}

COMMON_BAUD_RATES = [9600, 19200, 38400, 57600, 115200, 230400, 250000, 460800, 921600]

PEN_PRESETS = [
    {"name": "Black",   "rgb": (0, 0, 0),     "tool": 1},
    {"name": "Red",     "rgb": (255, 0, 0),   "tool": 2},
    {"name": "Green",   "rgb": (0, 180, 0),   "tool": 3},
    {"name": "Blue",    "rgb": (0, 0, 255),   "tool": 4},
    {"name": "Yellow",  "rgb": (255, 220, 0), "tool": 5},
    {"name": "Magenta", "rgb": (255, 0, 255), "tool": 6},
    {"name": "Cyan",    "rgb": (0, 200, 255), "tool": 7},
]

TOOL_COLOR_MAP = {entry["tool"]: entry for entry in PEN_PRESETS}

GRBL_ALARMS = {
    1: "Hard limit triggered - Machine position lost",
    2: "Motion target exceeds machine travel",
    3: "Reset while in motion - Position lost",
    4: "Probe fail - Initial state not as expected",
    5: "Probe fail - Did not contact workpiece",
    6: "Homing fail - Reset during active homing",
    7: "Homing fail - Safety door opened",
    8: "Homing fail - Pull off failed",
    9: "Homing fail - Did not find limit switch",
    10: "EStop asserted - Reset required",
}

GRBL_ERRORS = {
    1: "G-code words consist of letter and value - letter not found",
    2: "Numeric value format is not valid",
    3: "'$' system command not recognized",
    9: "G-code locked out during alarm or jog state",
    24: "Two G-code commands that both require use of XYZ axis",
    25: "Repeated G-code word found",
}


def std_icon(widget, sp_enum):
    return widget.style().standardIcon(sp_enum)


# ============================================================
# ADVANCED TEXT DIALOG
# ============================================================

class TextPropertiesDialog(QDialog):
    def __init__(self, parent=None, initial_color=QColor(0, 0, 0)):
        super().__init__(parent)
        self.setWindowTitle("Insert Text - Font Properties")
        self.setMinimumSize(520, 430)
        self.text_color = initial_color
        self.selected_font = QFont("Arial", 24)
        self.setup_ui()

    def setup_ui(self):
        layout = QVBoxLayout(self)

        text_group = QGroupBox("Text Content")
        text_layout = QVBoxLayout()
        self.text_input = QTextEdit()
        self.text_input.setMaximumHeight(70)
        self.text_input.setPlaceholderText("Enter your text here...")
        self.text_input.textChanged.connect(self.update_preview)
        text_layout.addWidget(self.text_input)
        text_group.setLayout(text_layout)
        layout.addWidget(text_group)

        font_group = QGroupBox("Font Properties")
        font_layout = QFormLayout()

        self.font_combo = QFontComboBox()
        self.font_combo.setCurrentFont(QFont("Arial"))
        self.font_combo.currentFontChanged.connect(self.update_font)
        font_layout.addRow("Font Family:", self.font_combo)

        size_row = QHBoxLayout()
        self.size_spin = QSpinBox()
        self.size_spin.setRange(6, 200)
        self.size_spin.setValue(24)
        self.size_spin.setSuffix(" pt")
        self.size_spin.valueChanged.connect(self.update_font)
        self.size_slider = QSlider(Qt.Horizontal)
        self.size_slider.setRange(6, 200)
        self.size_slider.setValue(24)
        self.size_slider.valueChanged.connect(self.size_spin.setValue)
        self.size_spin.valueChanged.connect(self.size_slider.setValue)
        size_row.addWidget(self.size_spin)
        size_row.addWidget(self.size_slider)
        font_layout.addRow("Font Size:", size_row)

        style_row = QHBoxLayout()
        self.bold_check = QCheckBox("Bold")
        self.italic_check = QCheckBox("Italic")
        self.underline_check = QCheckBox("Underline")
        self.strikeout_check = QCheckBox("Strikeout")
        for cb in [self.bold_check, self.italic_check,
                   self.underline_check, self.strikeout_check]:
            cb.stateChanged.connect(self.update_font)
            style_row.addWidget(cb)
        font_layout.addRow("Style:", style_row)

        color_row = QHBoxLayout()
        self.color_preview = QFrame()
        self.color_preview.setFixedSize(60, 28)
        self.color_preview.setStyleSheet(
            f"background-color: {self.text_color.name()}; border: 2px solid #555; border-radius: 3px;")
        color_btn = QPushButton("Choose Color...")
        color_btn.clicked.connect(self.pick_color)
        color_row.addWidget(self.color_preview)
        color_row.addWidget(color_btn)
        color_row.addStretch()
        font_layout.addRow("Color:", color_row)

        self.letter_spacing = QDoubleSpinBox()
        self.letter_spacing.setRange(-5, 20)
        self.letter_spacing.setValue(0)
        self.letter_spacing.setSingleStep(0.5)
        self.letter_spacing.setSuffix(" px")
        self.letter_spacing.valueChanged.connect(self.update_font)
        font_layout.addRow("Letter Spacing:", self.letter_spacing)

        self.rotation_spin = QSpinBox()
        self.rotation_spin.setRange(-360, 360)
        self.rotation_spin.setValue(0)
        self.rotation_spin.setSuffix("°")
        font_layout.addRow("Rotation:", self.rotation_spin)

        font_group.setLayout(font_layout)
        layout.addWidget(font_group)

        preview_group = QGroupBox("Preview")
        preview_layout = QVBoxLayout()
        self.preview_label = QLabel("Sample Text")
        self.preview_label.setAlignment(Qt.AlignCenter)
        self.preview_label.setMinimumHeight(70)
        self.preview_label.setStyleSheet("""
            QLabel { background-color: white; color: black;
                border: 2px dashed #888; border-radius: 4px; padding: 10px; }
        """)
        preview_layout.addWidget(self.preview_label)
        preview_group.setLayout(preview_layout)
        layout.addWidget(preview_group)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.update_font()

    def pick_color(self):
        color = QColorDialog.getColor(self.text_color, self, "Select Text Color")
        if color.isValid():
            self.text_color = color
            self.color_preview.setStyleSheet(
                f"background-color: {color.name()}; border: 2px solid #555; border-radius: 3px;")
            self.update_preview()

    def update_font(self):
        font = self.font_combo.currentFont()
        font.setPointSize(self.size_spin.value())
        font.setBold(self.bold_check.isChecked())
        font.setItalic(self.italic_check.isChecked())
        font.setUnderline(self.underline_check.isChecked())
        font.setStrikeOut(self.strikeout_check.isChecked())
        font.setLetterSpacing(QFont.AbsoluteSpacing, self.letter_spacing.value())
        self.selected_font = font
        self.update_preview()

    def update_preview(self):
        text = self.text_input.toPlainText() or "Sample Text"
        self.preview_label.setText(text)
        self.preview_label.setFont(self.selected_font)
        self.preview_label.setStyleSheet(f"""
            QLabel {{ background-color: white; color: {self.text_color.name()};
                border: 2px dashed #888; border-radius: 4px; padding: 10px; }}
        """)

    def get_properties(self):
        return {
            'text': self.text_input.toPlainText(),
            'font': self.selected_font,
            'color': self.text_color,
            'rotation': self.rotation_spin.value(),
            'letter_spacing': self.letter_spacing.value(),
        }
# ============================================================
# CNCJS WORKER  (FIXED Socket.IO emit format)
# ============================================================

class SerialWorker(QObject):
    """Bridge PyQt GUI to local cncjs over Socket.IO.
    
    KEY FIX: cncjs expects positional args, NOT a tuple.
    Correct: emit('command', port, cmd_name, *args)
    Wrong:   emit('command', (cmd_name, port, *args))
    """

    data_received = pyqtSignal(str)
    connection_status = pyqtSignal(bool, str)
    server_status_changed = pyqtSignal(bool, str)
    machine_position_changed = pyqtSignal(dict)
    alarm_triggered = pyqtSignal(str)
    error_triggered = pyqtSignal(str)
    progress_updated = pyqtSignal(int, int)
    streaming_finished = pyqtSignal(bool, str)

    def __init__(self):
        super().__init__()
        self.is_connected = False
        self.is_running = False
        self.is_paused = False
        self.should_stop = False
        self.current_line = 0
        self.total_lines = 0
        self._grbl_version = ""
        self.socket_client = None
        self.is_socket_connected = False
        self.active_port = ""
        self.active_baudrate = 115200
        self.machine_state = "Unknown"
        self.machine_position = {"X": None, "Y": None, "Z": None, "A": None}
        self.controller_type = "Grbl"
        self.cncjs_host = "127.0.0.1"
        self.cncjs_port = 8000
        self.cncjs_token = ""
        self.cncjs_username = ""
        self.cncjs_password = ""
        self.cncjs_command = ""
        self.watch_directory = ""
        self.auto_open_browser = True
        self._browser_opened = False
        self._started_cncjs = False
        self._cncjs_process = None
        self._session_token = ""
        self._authenticated_user = ""
        self._connect_event = threading.Event()
        self._close_event = threading.Event()
        self._last_error = ""
        self._last_loaded_job = ""
        self._job_finished_emitted = False
        self._server_lock = threading.Lock()

    def configure_cncjs(self, host, port, controller_type="Grbl", token="",
                        username="", password="", command_path="",
                        watch_directory="", auto_open_browser=True):
        self.cncjs_host = (host or "127.0.0.1").strip()
        self.cncjs_port = int(port or 8000)
        self.controller_type = controller_type or "Grbl"
        self.cncjs_token = token.strip()
        self.cncjs_username = username.strip()
        self.cncjs_password = password
        self.cncjs_command = command_path.strip()
        self.watch_directory = str(watch_directory or "")
        self.auto_open_browser = bool(auto_open_browser)

    def server_url(self):
        return f"http://{self.cncjs_host}:{self.cncjs_port}/"

    def _server_http_ready(self):
        try:
            with urlopen(self.server_url(), timeout=2):
                return True
        except (URLError, OSError, ValueError):
            return False

    def _signin_url(self):
        return urljoin(self.server_url(), "api/signin")

    def _authenticate_cncjs(self):
        attempts = []
        if self._session_token:
            attempts.append({"token": self._session_token})
        if self.cncjs_token and self.cncjs_token != self._session_token:
            attempts.append({"token": self.cncjs_token})
        if self.cncjs_username and self.cncjs_password:
            attempts.append({"name": self.cncjs_username, "password": self.cncjs_password})
        if not attempts:
            attempts.append({})

        last_error = None
        for payload in attempts:
            data = json.dumps(payload).encode("utf-8")
            request = Request(
                self._signin_url(), data=data,
                headers={"Content-Type": "application/json", "Accept": "application/json"},
                method="POST",
            )
            try:
                with urlopen(request, timeout=8) as response:
                    body = json.loads(response.read().decode("utf-8"))
            except Exception as exc:
                last_error = exc
                continue

            token = body.get("token", "")
            if not token:
                last_error = "No token returned"
                continue

            self._session_token = token
            self._authenticated_user = body.get("name", "")
            enabled = body.get("enabled", True)
            if enabled:
                name = self._authenticated_user or self.cncjs_username or "user"
                self.server_status_changed.emit(True, f"Authenticated with cncjs as {name}")
            else:
                self.server_status_changed.emit(True, "Authenticated with cncjs (no accounts)")
            return True

        self.server_status_changed.emit(False, "cncjs authentication failed")
        if last_error:
            self.data_received.emit(f"⚠ cncjs signin failed: {last_error}")
        return False

    def _resolve_cncjs_command(self):
        candidates = []
        if self.cncjs_command:
            candidates.append(self.cncjs_command)
        for name in ("cncjs", "cnc"):
            found = shutil.which(name)
            if found:
                candidates.append(found)
        for candidate in candidates:
            if not candidate:
                continue
            if os.path.isabs(candidate):
                if os.path.exists(candidate):
                    return candidate
            elif shutil.which(candidate):
                return candidate
        return ""

    def _emit_machine_position(self):
        payload = dict(self.machine_position)
        payload["state"] = self.machine_state
        self.machine_position_changed.emit(payload)

    def _parse_status_report(self, line):
        if not (line.startswith("<") and line.endswith(">")):
            return False
        body = line[1:-1]
        parts = body.split("|")
        if not parts:
            return False
        self.machine_state = parts[0] or self.machine_state
        for part in parts[1:]:
            if ":" not in part:
                continue
            key, value = part.split(":", 1)
            if key not in ("MPos", "WPos"):
                continue
            coords = [t.strip() for t in value.split(",")]
            for axis, tok in zip(("X", "Y", "Z", "A"), coords):
                try:
                    self.machine_position[axis] = float(tok)
                except ValueError:
                    continue
            self._emit_machine_position()
            return True
        self._emit_machine_position()
        return True

    def open_browser(self):
        if self._browser_opened or not self.auto_open_browser:
            return
        try:
            webbrowser.open(self.server_url(), new=1, autoraise=True)
            self._browser_opened = True
        except Exception as exc:
            self.data_received.emit(f"⚠ Could not open cncjs in browser: {exc}")

    def start_cncjs_server(self, open_browser=None):
        if open_browser is None:
            open_browser = self.auto_open_browser
        with self._server_lock:
            if self._server_http_ready():
                self.server_status_changed.emit(True, f"cncjs ready at {self.server_url()}")
                if open_browser:
                    self.open_browser()
                return True

            command = self._resolve_cncjs_command()
            if not command:
                self.server_status_changed.emit(False,
                    "cncjs command not found. Install with: npm install -g cncjs")
                return False

            launch_cmd = [command, "--host", self.cncjs_host,
                         "--port", str(self.cncjs_port),
                         "--controller", self.controller_type]
            if self.watch_directory:
                launch_cmd.extend(["--watch-directory", self.watch_directory])

            try:
                self._cncjs_process = subprocess.Popen(
                    launch_cmd, stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL, start_new_session=True)
                self._started_cncjs = True
            except Exception as exc:
                self.server_status_changed.emit(False, f"Failed to start cncjs: {exc}")
                return False

            deadline = time.time() + 25
            while time.time() < deadline:
                if self._cncjs_process.poll() is not None:
                    break
                if self._server_http_ready():
                    self.server_status_changed.emit(True,
                        f"cncjs running at {self.server_url()} (PID: {self._cncjs_process.pid})")
                    if open_browser:
                        self.open_browser()
                    return True
                time.sleep(0.4)

            exit_code = self._cncjs_process.poll()
            self.server_status_changed.emit(False,
                f"cncjs did not become ready (exit={exit_code})")
            return False

    def _install_socket_handlers(self):
        if self.socket_client is None:
            return

        @self.socket_client.event
        def connect():
            self.is_socket_connected = True
            self.server_status_changed.emit(True, f"Socket connected to cncjs")
            self.data_received.emit(f"Connected to cncjs websocket at {self.server_url()}")
            self.request_port_list()

        @self.socket_client.event
        def disconnect():
            self.is_socket_connected = False
            was_connected = self.is_connected
            self.is_connected = False
            self.is_running = False
            self.is_paused = False
            self.server_status_changed.emit(False, "Disconnected from cncjs websocket")
            if was_connected:
                self.connection_status.emit(False, "Machine disconnected")

        @self.socket_client.event
        def connect_error(data):
            self._last_error = str(data)
            self.server_status_changed.emit(False, f"cncjs websocket error: {data}")

        @self.socket_client.on("serialport:list")
        def on_serialport_list(ports):
            count = len(ports) if isinstance(ports, list) else 0
            self.data_received.emit(f"cncjs sees {count} serial port(s)")

        @self.socket_client.on("serialport:open")
        def on_serialport_open(options):
            options = options or {}
            self.active_port = options.get("port") or options.get("path") or self.active_port
            self.active_baudrate = options.get("baudrate", self.active_baudrate)
            self.is_connected = True
            self.should_stop = False
            self._connect_event.set()
            self.connection_status.emit(True,
                f"Connected through cncjs: {self.active_port} @ {self.active_baudrate}")
            QTimer.singleShot(250, self.get_status)

        @self.socket_client.on("serialport:close")
        def on_serialport_close(options):
            options = options or {}
            port = options.get("port") or options.get("path") or self.active_port
            self.is_connected = False
            self.is_running = False
            self.is_paused = False
            self._close_event.set()
            self.connection_status.emit(False, f"Disconnected from {port or 'machine'}")

        @self.socket_client.on("serialport:error")
        def on_serialport_error(payload):
            payload = payload or {}
            err = payload.get("err", payload)
            port = payload.get("port", self.active_port)
            message = f"{err}"
            self._last_error = message
            self.error_triggered.emit(message)
            self.data_received.emit(f"⚠ cncjs serial error on {port}: {message}")

        @self.socket_client.on("serialport:read")
        def on_serialport_read(data):
            text = "" if data is None else str(data)
            for raw_line in text.splitlines() or [text]:
                line = raw_line.strip()
                if not line:
                    continue
                self.data_received.emit(line)
                if line.startswith("<") and line.endswith(">"):
                    self._parse_status_report(line)
                elif line.startswith("Grbl") or line.startswith("GrblHAL"):
                    self._grbl_version = line
                elif line.startswith("ALARM:"):
                    code = line.replace("ALARM:", "").strip()
                    try:
                        alarm_msg = GRBL_ALARMS.get(int(code), f"Unknown alarm {code}")
                    except ValueError:
                        alarm_msg = f"Alarm: {code}"
                    self.alarm_triggered.emit(alarm_msg)
                elif line.startswith("error:"):
                    code = line.replace("error:", "").strip()
                    try:
                        err_msg = GRBL_ERRORS.get(int(code), f"Error code {code}")
                    except ValueError:
                        err_msg = f"Error: {code}"
                    self.error_triggered.emit(err_msg)

        @self.socket_client.on("serialport:write")
        def on_serialport_write(*args):
            # cncjs may send (data) or (data, context) - handle safely
            if not args:
                return
            data = args[0]
            if isinstance(data, dict):
                return  # Skip context-only emissions
            text = "" if data is None else str(data).rstrip()
            if text:
                self.data_received.emit(f"> {text}")

        @self.socket_client.on("gcode:load")
        def on_gcode_load(*args):
            if not args:
                return
            name = args[0]
            if isinstance(name, dict):
                name = name.get("name", "")
            if name:
                self._last_loaded_job = str(name)
                self.data_received.emit(f"cncjs loaded job: {self._last_loaded_job}")

        @self.socket_client.on("sender:status")
        def on_sender_status(status):
            status = status or {}
            total = int(status.get("total") or 0)
            sent = int(status.get("sent") or status.get("received") or 0)
            if total > 0:
                self.current_line = min(sent, total)
                self.total_lines = total
                self.progress_updated.emit(self.current_line, total)
            if self.is_running and status.get("finishTime") and not self._job_finished_emitted:
                self._job_finished_emitted = True
                self.is_running = False
                self.is_paused = False
                self.streaming_finished.emit(not self.should_stop, "Completed cncjs job")
                self.should_stop = False

        @self.socket_client.on("workflow:state")
        def on_workflow_state(state):
            state_value = state.get("state") if isinstance(state, dict) else str(state)
            lowered = (state_value or "").lower()
            if lowered == "paused":
                self.is_paused = True
            elif lowered == "running":
                self.is_running = True
                self.is_paused = False
            elif lowered in ("idle", "stopped"):
                if (self.is_running or self.should_stop) and not self._job_finished_emitted:
                    self._job_finished_emitted = True
                    success = not self.should_stop
                    self.is_running = False
                    self.is_paused = False
                    self.streaming_finished.emit(
                        success,
                        "Completed cncjs job" if success else "Stopped by user")
                    self.should_stop = False

    def ensure_socket_connection(self):
        if self.is_socket_connected and self.socket_client:
            return True
        if not self.start_cncjs_server():
            return False
        if not self._authenticate_cncjs():
            return False
        if not SOCKETIO_AVAILABLE:
            self.server_status_changed.emit(False,
                "python-socketio missing — pip install 'python-socketio[client]'")
            return False

        if self.socket_client:
            try:
                self.socket_client.disconnect()
            except Exception:
                pass

        self.socket_client = socketio_client.Client(
            reconnection=True, logger=False, engineio_logger=False)
        self._install_socket_handlers()

        connect_url = self.server_url().rstrip("/")
        if self._session_token:
            connect_url = f"{connect_url}?token={self._session_token}"

        try:
            self.socket_client.connect(
                connect_url, transports=["websocket"],
                wait=True, wait_timeout=10)
            return True
        except Exception as exc:
            self.server_status_changed.emit(False, f"Failed to connect websocket: {exc}")
            return False

    def request_port_list(self):
        if not (self.socket_client and self.is_socket_connected):
            return
        try:
            self.socket_client.emit("list")
        except Exception as exc:
            self.data_received.emit(f"⚠ Could not request port list: {exc}")

    def _auto_detect_port(self):
        if not SERIAL_AVAILABLE:
            return ""
        usb_ports = []
        other_ports = []
        for port in serial.tools.list_ports.comports():
            device = port.device
            if re.match(r"/dev/tty(USB|ACM)\d+", device):
                usb_ports.append(device)
            else:
                other_ports.append(device)
        ordered = usb_ports + other_ports
        return ordered[0] if ordered else ""

    # ============================================================
    # FIXED: cncjs uses positional args
    # Format: socket.emit('command', port, command_name, ...args)
    # ============================================================
    def _emit_controller_command(self, command_name, *args, callback=None):
        if not (self.socket_client and self.is_socket_connected and self.active_port):
            return False
        try:
            if callback:
                self.socket_client.emit("command", self.active_port,
                                        command_name, *args, callback=callback)
            else:
                self.socket_client.emit("command", self.active_port,
                                        command_name, *args)
            return True
        except Exception as exc:
            self.data_received.emit(f"⚠ cncjs command failed: {exc}")
            return False

    def connect_serial(self, port, baudrate):
        if not port:
            port = self._auto_detect_port()
        if not port:
            self.connection_status.emit(False, "No serial port selected")
            return False
        if self.is_connected and self.active_port == port and self.active_baudrate == baudrate:
            self.connection_status.emit(True, f"Already connected: {port} @ {baudrate}")
            return True
        if not self.ensure_socket_connection():
            return False
        if self.is_connected:
            self.disconnect_serial()
            time.sleep(0.2)

        self.active_port = port
        self.active_baudrate = baudrate
        self._last_error = ""
        self._connect_event.clear()

        def ack(*args):
            if args and args[0]:
                self._last_error = str(args[0])
                self._connect_event.set()

        try:
            # FIXED: positional args, not tuple
            self.socket_client.emit(
                "open", port,
                {"baudrate": baudrate, "controllerType": self.controller_type},
                callback=ack)
        except Exception as exc:
            self.connection_status.emit(False, f"Could not open {port}: {exc}")
            return False

        deadline = time.time() + 10
        while time.time() < deadline:
            if self.is_connected:
                return True
            if self._last_error:
                self.connection_status.emit(False, f"Failed to open {port}: {self._last_error}")
                return False
            time.sleep(0.1)
        self.connection_status.emit(False, f"Timeout opening {port}")
        return False

    def disconnect_serial(self):
        self.should_stop = True
        self.is_running = False
        self.is_paused = False
        if not (self.socket_client and self.is_socket_connected and self.active_port):
            self.is_connected = False
            self.connection_status.emit(False, "Disconnected")
            return
        port = self.active_port
        self._close_event.clear()
        self._last_error = ""

        def ack(*args):
            if args and args[0]:
                self._last_error = str(args[0])
                self._close_event.set()
        try:
            self.socket_client.emit("close", port, callback=ack)
        except Exception as exc:
            self.connection_status.emit(False, f"Could not close {port}: {exc}")
            self.is_connected = False
            return
        deadline = time.time() + 5
        while time.time() < deadline:
            if not self.is_connected:
                return
            if self._last_error:
                self.connection_status.emit(False, f"Close error: {self._last_error}")
                self.is_connected = False
                return
            time.sleep(0.1)
        self.is_connected = False
        self.connection_status.emit(False, f"Disconnected from {port}")

    def send_raw(self, command):
        if not self.is_connected:
            return False
        text = (command or "").rstrip("\n")
        if not text:
            return False
        if text == "?":
            return self._emit_controller_command("statusreport")
        if text == "!":
            self.is_paused = True
            return self._emit_controller_command("feedhold")
        if text == "~":
            self.is_paused = False
            return self._emit_controller_command("cyclestart")
        if text == "\x18":
            return self._emit_controller_command("reset")
        try:
            # FIXED: positional args
            self.socket_client.emit("write", self.active_port,
                                    text + "\n", {"source": "robot.py"})
            return True
        except Exception as exc:
            self.data_received.emit(f"⚠ Write failed: {exc}")
            return False

    def send_gcode(self, gcode_content, job_name=None):
        """FIXED main streaming bug.
        cncjs gcode:load takes (name, gcode, context) as positional args.
        After load, we call gcode:start to actually run it.
        """
        if not self.is_connected:
            self.data_received.emit("⚠ Cannot stream — not connected")
            return False
        if self.is_running:
            self.data_received.emit("⚠ cncjs already running a job")
            return False

        raw_lines = [l for l in gcode_content.splitlines() if l.strip()]
        if not raw_lines:
            return False

        self.total_lines = len(raw_lines)
        self.current_line = 0
        self.is_running = True
        self.is_paused = False
        self.should_stop = False
        self._job_finished_emitted = False
        self.progress_updated.emit(0, self.total_lines)

        if not job_name:
            job_name = f"drawing_{datetime.now().strftime('%Y%m%d_%H%M%S')}.gcode"

        worker = self

        def load_ack(*args):
            if args and args[0]:
                worker.is_running = False
                worker._job_finished_emitted = True
                err_msg = str(args[0])
                worker.error_triggered.emit(err_msg)
                worker.streaming_finished.emit(False, f"Load failed: {err_msg}")
                return
            worker.data_received.emit(f"✅ Loaded {job_name} into cncjs")

            def start_job():
                if worker._emit_controller_command("gcode:start"):
                    worker.data_received.emit("▶️ Sent gcode:start to cncjs")
                else:
                    worker.is_running = False
                    worker.streaming_finished.emit(False, "Could not start sender")
            QTimer.singleShot(400, start_job)

        try:
            # FIXED: positional args, NOT tuple
            self.socket_client.emit(
                "command", self.active_port, "gcode:load",
                job_name, gcode_content,
                {"name": job_name, "source": "robot.py"},
                callback=load_ack)
            self.data_received.emit(
                f"📤 Uploading {job_name} ({self.total_lines} lines)...")
            return True
        except Exception as exc:
            self.is_running = False
            self.streaming_finished.emit(False, f"Send failed: {exc}")
            return False

    def pause(self):
        if not self.is_connected:
            return
        self.is_paused = True
        if not self._emit_controller_command("gcode:pause"):
            self._emit_controller_command("feedhold")

    def resume(self):
        if not self.is_connected:
            return
        self.is_paused = False
        if not self._emit_controller_command("gcode:resume"):
            self._emit_controller_command("cyclestart")

    def emergency_stop(self):
        self.should_stop = True
        self.is_running = False
        self.is_paused = False
        if self.is_connected:
            self._emit_controller_command("gcode:stop", {"force": True})
            self._emit_controller_command("reset")

    def unlock_alarm(self):
        if self.is_connected:
            self._emit_controller_command("unlock")

    def home(self):
        if self.is_connected:
            self._emit_controller_command("homing")

    def get_status(self):
        if self.is_connected:
            self._emit_controller_command("statusreport")

    def jog_incremental(self, dx=0.0, dy=0.0, dz=0.0, da=0.0, feed=1200):
        if not self.is_connected:
            return False
        axes = []
        for axis, value in (("X", dx), ("Y", dy), ("Z", dz), ("A", da)):
            if abs(value) > 1e-9:
                axes.append(f"{axis}{value:.3f}")
        if not axes:
            return False
        try:
            feed_value = max(int(feed), 1)
        except (TypeError, ValueError):
            feed_value = 1200
        command = f"$J=G91 G21 {' '.join(axes)} F{feed_value}"
        sent = self.send_raw(command)
        if sent:
            QTimer.singleShot(250, self.get_status)
        return sent

    def start_background(self, preferred_port="", baudrate=115200,
                         open_browser=True, auto_connect_machine=True):
        threading.Thread(
            target=self._start_background_impl,
            args=(preferred_port, baudrate, open_browser, auto_connect_machine),
            daemon=True, name="cncjs-startup").start()

    def _start_background_impl(self, preferred_port, baudrate, open_browser, auto_connect_machine):
        if not self.start_cncjs_server(open_browser=open_browser):
            return
        if not self.ensure_socket_connection():
            return
        if auto_connect_machine and not self.is_connected:
            self.connect_serial(preferred_port, baudrate)

    def shutdown(self):
        try:
            if self.is_connected:
                self.disconnect_serial()
        except Exception:
            pass
        try:
            if self.socket_client and self.is_socket_connected:
                self.socket_client.disconnect()
        except Exception:
            pass
        if self._started_cncjs and self._cncjs_process and self._cncjs_process.poll() is None:
            try:
                self._cncjs_process.terminate()
            except Exception:
                pass


# ============================================================
# OPTIMIZED DRAWING CANVAS (Pi 4: grid in drawBackground)
# ============================================================

class DrawingCanvas(QGraphicsView):
    mouse_position_changed = pyqtSignal(float, float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.scene = QGraphicsScene(self)
        self.setScene(self.scene)
        self.plot_width_mm = 200
        self.plot_height_mm = 150
        self.px_per_mm = 4.0
        self.update_canvas_size()
        self.setBackgroundBrush(QBrush(QColor(45, 48, 55)))

        self.drawing = False
        self.current_path = None
        self.current_path_item = None
        self.last_point = QPointF()
        self.pen_color = QColor(0, 0, 0)
        self.pen_width = 2
        self.current_tool = "pen"
        self.strokes = []

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

        self.temp_item = None
        self.start_point = None
        self.show_grid = True
        self.show_axes = True
        self.show_rulers = True
        self.show_origin = True
        self.show_frame = True
        self.scene.setSceneRect(0, 0, self.scene_width, self.scene_height)

    def update_canvas_size(self):
        margin_mm = 30
        self.margin_px = margin_mm * self.px_per_mm
        self.plot_width_px = self.plot_width_mm * self.px_per_mm
        self.plot_height_px = self.plot_height_mm * self.px_per_mm
        self.scene_width = self.plot_width_px + 2 * self.margin_px
        self.scene_height = self.plot_height_px + 2 * self.margin_px
        self.origin_x = self.margin_px
        self.origin_y = self.margin_px + self.plot_height_px
        if hasattr(self, 'scene') and self.scene is not None:
            self.scene.setSceneRect(0, 0, self.scene_width, self.scene_height)
            self.resetCachedContent()

    def set_plot_size(self, width_mm, height_mm):
        self.plot_width_mm = width_mm
        self.plot_height_mm = height_mm
        self.update_canvas_size()
        self.resetCachedContent()
        self.viewport().update()

    def drawBackground(self, painter, rect):
        super().drawBackground(painter, rect)
        plot_rect = QRectF(self.origin_x, self.margin_px,
                           self.plot_width_px, self.plot_height_px)
        painter.fillRect(plot_rect, QColor(255, 255, 255))

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

    def _paint_grid(self, painter):
        scale = self.transform().m11()
        show_minor = (self.px_per_mm * scale) >= 3.0

        if show_minor and not IS_RPI:
            pen = QPen(QColor(220, 225, 235), 0)
            painter.setPen(pen)
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

        pen = QPen(QColor(180, 190, 210), 0)
        painter.setPen(pen)
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

        pen = QPen(QColor(140, 155, 180), 0)
        painter.setPen(pen)
        for mm in range(0, int(self.plot_width_mm) + 1, 50):
            x = self.origin_x + mm * self.px_per_mm
            painter.drawLine(QLineF(x, self.margin_px, x, self.origin_y))
        for mm in range(0, int(self.plot_height_mm) + 1, 50):
            y = self.origin_y - mm * self.px_per_mm
            painter.drawLine(QLineF(self.origin_x, y,
                                    self.origin_x + self.plot_width_px, y))

    def _paint_frame(self, painter):
        pen = QPen(QColor(30, 144, 255), 2.0)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        painter.drawRect(QRectF(self.origin_x, self.margin_px,
                                self.plot_width_px, self.plot_height_px))
        corner_size = 6
        painter.setBrush(QColor(30, 144, 255))
        for cx, cy in [(self.origin_x, self.margin_px),
                       (self.origin_x + self.plot_width_px, self.margin_px),
                       (self.origin_x, self.origin_y),
                       (self.origin_x + self.plot_width_px, self.origin_y)]:
            painter.drawRect(QRectF(cx - corner_size/2, cy - corner_size/2,
                                    corner_size, corner_size))

    def _paint_axes(self, painter):
        pen = QPen(QColor(220, 50, 50), 1.5)
        painter.setPen(pen)
        x_end = self.origin_x + self.plot_width_px + 20
        painter.drawLine(QLineF(self.origin_x, self.origin_y, x_end, self.origin_y))
        arrow = QPainterPath()
        arrow.moveTo(x_end, self.origin_y)
        arrow.lineTo(x_end - 8, self.origin_y - 4)
        arrow.lineTo(x_end - 8, self.origin_y + 4)
        arrow.closeSubpath()
        painter.fillPath(arrow, QColor(220, 50, 50))
        painter.setFont(QFont("Arial", 10, QFont.Bold))
        painter.drawText(QPointF(x_end + 4, self.origin_y - 4), "X+")

        pen = QPen(QColor(50, 180, 50), 1.5)
        painter.setPen(pen)
        y_end = self.origin_y - self.plot_height_px - 20
        painter.drawLine(QLineF(self.origin_x, self.origin_y, self.origin_x, y_end))
        arrow = QPainterPath()
        arrow.moveTo(self.origin_x, y_end)
        arrow.lineTo(self.origin_x - 4, y_end + 8)
        arrow.lineTo(self.origin_x + 4, y_end + 8)
        arrow.closeSubpath()
        painter.fillPath(arrow, QColor(50, 180, 50))
        painter.drawText(QPointF(self.origin_x - 18, y_end - 2), "Y+")

    def _paint_rulers(self, painter):
        pen = QPen(QColor(180, 190, 210), 0.8)
        painter.setPen(pen)
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
        painter.drawText(QPointF(self.origin_x - 25, self.origin_y + 22), "[mm]")

    def _paint_origin(self, painter):
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
        painter.drawText(QPointF(self.origin_x + radius + 4, self.origin_y + radius - 1),
                        "ORIGIN (0,0)")

    def scene_to_mm(self, scene_pos):
        x_mm = (scene_pos.x() - self.origin_x) / self.px_per_mm
        y_mm = (self.origin_y - scene_pos.y()) / self.px_per_mm
        return x_mm, y_mm

    def mm_to_scene(self, x_mm, y_mm):
        x = self.origin_x + x_mm * self.px_per_mm
        y = self.origin_y - y_mm * self.px_per_mm
        return QPointF(x, y)

    def is_in_plot_area(self, scene_pos):
        return (self.origin_x <= scene_pos.x() <= self.origin_x + self.plot_width_px
                and self.margin_px <= scene_pos.y() <= self.origin_y)

    def set_pen_color(self, color):
        self.pen_color = color

    def set_pen_width(self, width):
        self.pen_width = width

    def set_tool(self, tool):
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

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and self.current_tool not in ("pan", "select"):
            pos = self.mapToScene(event.pos())
            if not self.is_in_plot_area(pos) and self.current_tool in ("pen", "line", "rect", "circle"):
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
                items = self.scene.items(pos)
                for item in items:
                    if isinstance(item, (QGraphicsPathItem, QGraphicsTextItem)):
                        if item.zValue() < 0:
                            continue
                        self.scene.removeItem(item)
                        self.strokes = [s for s in self.strokes if s.get('item') != item]
                        break
            elif self.current_tool == "text":
                self.insert_text_at(pos)
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        pos = self.mapToScene(event.pos())
        x_mm, y_mm = self.scene_to_mm(pos)
        self.mouse_position_changed.emit(x_mm, y_mm)
        if self.drawing and self.current_tool == "pen":
            if self.is_in_plot_area(pos):
                dx = pos.x() - self.last_point.x()
                dy = pos.y() - self.last_point.y()
                if dx*dx + dy*dy >= 4:
                    self.current_path.lineTo(pos)
                    self.current_path_item.setPath(self.current_path)
                    self.last_point = pos
        elif self.current_tool in ["line", "rect", "circle"] and event.buttons() & Qt.LeftButton:
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

    def mouseReleaseEvent(self, event):
        if self.drawing and self.current_tool == "pen":
            self.drawing = False
            if self.current_path_item:
                self.current_path_item.setFlag(QGraphicsItem.ItemIsMovable, True)
                self.current_path_item.setFlag(QGraphicsItem.ItemIsSelectable, True)
                self.strokes.append({
                    'type': 'path', 'color': QColor(self.pen_color),
                    'path': QPainterPath(self.current_path),
                    'width': self.pen_width, 'item': self.current_path_item})
            self.current_path = None
            self.current_path_item = None
        elif self.current_tool in ["line", "rect", "circle"] and self.temp_item:
            self.temp_item.setFlag(QGraphicsItem.ItemIsMovable, True)
            self.temp_item.setFlag(QGraphicsItem.ItemIsSelectable, True)
            self.strokes.append({
                'type': self.current_tool, 'color': QColor(self.pen_color),
                'path': self.temp_item.path(), 'width': self.pen_width,
                'item': self.temp_item})
            self.temp_item = None
        super().mouseReleaseEvent(event)

    def insert_text_at(self, pos):
        dialog = TextPropertiesDialog(self, self.pen_color)
        if dialog.exec_() == QDialog.Accepted:
            props = dialog.get_properties()
            if not props['text']:
                return
            text_item = QGraphicsTextItem(props['text'])
            text_item.setDefaultTextColor(props['color'])
            text_item.setFont(props['font'])
            text_item.setPos(pos)
            text_item.setRotation(props['rotation'])
            text_item.setFlag(QGraphicsTextItem.ItemIsMovable, True)
            text_item.setFlag(QGraphicsTextItem.ItemIsSelectable, True)
            self.scene.addItem(text_item)
            self.strokes.append({
                'type': 'text', 'color': props['color'],
                'text': props['text'], 'pos': pos,
                'font': props['font'], 'rotation': props['rotation'],
                'letter_spacing': props['letter_spacing'], 'item': text_item})

    def wheelEvent(self, event):
        if event.modifiers() & Qt.ControlModifier:
            factor = 1.15 if event.angleDelta().y() > 0 else 1 / 1.15
            self.scale(factor, factor)
            self.resetCachedContent()
        else:
            super().wheelEvent(event)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Delete:
            for item in self.scene.selectedItems():
                if item.zValue() >= 0:
                    self.scene.removeItem(item)
                    self.strokes = [s for s in self.strokes if s.get('item') != item]
        super().keyPressEvent(event)

    def clear_canvas(self):
        for stroke in self.strokes:
            if 'item' in stroke and stroke['item'] in self.scene.items():
                self.scene.removeItem(stroke['item'])
        self.strokes = []

    def toggle_grid(self, show):
        self.show_grid = show
        self.resetCachedContent()
        self.viewport().update()

    def toggle_axes(self, show):
        self.show_axes = show
        self.resetCachedContent()
        self.viewport().update()

    def toggle_rulers(self, show):
        self.show_rulers = show
        self.resetCachedContent()
        self.viewport().update()

    def toggle_origin(self, show):
        self.show_origin = show
        self.resetCachedContent()
        self.viewport().update()

    def fit_to_view(self):
        rect = QRectF(self.margin_px / 2, self.margin_px / 2,
                      self.plot_width_px + self.margin_px,
                      self.plot_height_px + self.margin_px)
        self.fitInView(rect, Qt.KeepAspectRatio)
        self.resetCachedContent()

    def import_image(self, filepath):
        ext = Path(filepath).suffix.lower()
        center = self.mm_to_scene(self.plot_width_mm / 2, self.plot_height_mm / 2)
        if ext in ['.png', '.jpg', '.jpeg', '.bmp', '.gif']:
            pixmap = QPixmap(filepath)
            if not pixmap.isNull():
                item = QGraphicsPixmapItem(pixmap)
                item.setPos(center.x() - pixmap.width() / 2,
                           center.y() - pixmap.height() / 2)
                item.setFlag(QGraphicsPixmapItem.ItemIsMovable, True)
                item.setFlag(QGraphicsPixmapItem.ItemIsSelectable, True)
                self.scene.addItem(item)
                self.strokes.append({'type': 'image', 'pixmap': pixmap,
                                    'item': item, 'filepath': filepath})
                return True
        elif ext == '.svg':
            try:
                from PyQt5.QtSvg import QGraphicsSvgItem
                item = QGraphicsSvgItem(filepath)
                item.setPos(center)
                item.setFlag(QGraphicsSvgItem.ItemIsMovable, True)
                item.setFlag(QGraphicsSvgItem.ItemIsSelectable, True)
                self.scene.addItem(item)
                self.strokes.append({'type': 'svg', 'item': item, 'filepath': filepath})
                return True
            except ImportError:
                return False
        return False

    def export_image(self, filepath):
        rect = QRectF(self.origin_x, self.margin_px,
                      self.plot_width_px, self.plot_height_px)
        image = QImage(int(rect.width()), int(rect.height()), QImage.Format_ARGB32)
        image.fill(Qt.white)
        painter = QPainter(image)
        painter.setRenderHint(QPainter.Antialiasing)
        self.scene.render(painter, QRectF(image.rect()), rect)
        painter.end()
        return image.save(filepath)


# ============================================================
# G-CODE GENERATOR with AUTOMATED PEN CHANGER
# ============================================================

class GCodeGenerator:
    def __init__(self, canvas, plot_width=200, plot_height=150):
        self.canvas = canvas
        self.plot_width = plot_width
        self.plot_height = plot_height
        self.pen_up_z = 5
        self.pen_down_z = 0
        self.feed_rate = 3000
        self.travel_rate = 5000
        self.max_x = plot_width
        self.max_y = plot_height
        self.min_x = 0
        self.min_y = 0
        # Auto pen changer settings
        self.tool_change_enabled = False
        self.tool_change_safe_z = 15.0
        self.tool_change_approach_dist = 100.0  # 10cm forward/backward
        self.tool_change_approach_axis = "Y"    # default approach along Y
        self.tool_change_approach_dir = -1       # -1 = backward releases, +1 forward grabs
        self.tool_change_travel_feed = 3000
        self.tool_change_pickup_feed = 800
        self.tool_change_dwell_ms = 300
        self.tool_change_return_to_rack = True
        self.tool_change_start_tool = 0
        self.tool_change_slots = {}

    def configure_tool_changer(self, enabled=False, slots=None,
                               safe_z=15.0, approach_dist=100.0,
                               approach_axis="Y", approach_dir=-1,
                               travel_feed=3000, pickup_feed=800,
                               dwell_ms=300, return_to_rack=True, start_tool=0,
                               **kwargs):  # accept legacy keys
        self.tool_change_enabled = bool(enabled)
        self.tool_change_safe_z = float(safe_z)
        self.tool_change_approach_dist = float(approach_dist)
        self.tool_change_approach_axis = (approach_axis or "Y").upper()
        self.tool_change_approach_dir = -1 if int(approach_dir) < 0 else 1
        self.tool_change_travel_feed = int(travel_feed)
        self.tool_change_pickup_feed = int(pickup_feed)
        self.tool_change_dwell_ms = int(dwell_ms)
        self.tool_change_return_to_rack = bool(return_to_rack)
        self.tool_change_start_tool = int(start_tool or 0)
        normalized_slots = {}
        for key, slot in (slots or {}).items():
            try:
                tool = int(key)
            except (TypeError, ValueError):
                continue
            if not isinstance(slot, dict):
                continue
            if all(axis in slot for axis in ("x", "y", "z")):
                normalized_slots[tool] = {
                    "name": str(slot.get("name",
                        TOOL_COLOR_MAP.get(tool, {}).get("name", f"Pen {tool}"))),
                    "x": float(slot["x"]),
                    "y": float(slot["y"]),
                    "z": float(slot["z"]),
                }
        self.tool_change_slots = normalized_slots

    def scale_point(self, scene_x, scene_y):
        x_mm, y_mm = self.canvas.scene_to_mm(QPointF(scene_x, scene_y))
        x_mm = max(self.min_x, min(self.max_x, x_mm))
        y_mm = max(self.min_y, min(self.max_y, y_mm))
        return x_mm, y_mm

    def color_to_tool(self, color):
        rgb = (color.red(), color.green(), color.blue())
        min_dist = float('inf')
        best_tool = 1
        for preset in PEN_PRESETS:
            dist = sum((a - b) ** 2 for a, b in zip(rgb, preset["rgb"]))
            if dist < min_dist:
                min_dist = dist
                best_tool = preset["tool"]
        return best_tool

    def missing_pen_slots(self, strokes):
        if not self.tool_change_enabled:
            return []
        missing = []
        seen = set()
        for stroke in strokes:
            if stroke.get("type") in ("image", "svg"):
                continue
            color = stroke.get("color", QColor(0, 0, 0))
            tool = self.color_to_tool(color)
            if tool not in self.tool_change_slots and tool not in seen:
                seen.add(tool)
                missing.append(tool)
        return missing

    def _tool_change_comment(self, tool):
        slot = self.tool_change_slots.get(tool)
        if slot:
            return slot.get("name", f"Pen {tool}")
        preset = TOOL_COLOR_MAP.get(tool)
        if preset:
            return preset["name"]
        return f"Pen {tool}"

    def _approach_offset(self, sign):
        """Return (dx, dy) for approach offset along configured axis.
        sign=+1 means 'engage' direction (grab pen), sign=-1 means 'retract' (release)."""
        axis = self.tool_change_approach_axis
        # User config: approach_dir tells which direction GRABS the pen
        # +1 means moving forward in axis grabs, -1 means moving backward grabs
        direction = self.tool_change_approach_dir * sign
        dist = self.tool_change_approach_dist * direction
        if axis == "X":
            return dist, 0.0
        return 0.0, dist  # Y axis default

    def _append_auto_tool_change(self, lines, current_tool, next_tool):
        """Fully automatic pen change with linear approach/retract.
        
        DROP OFF current pen:
          1. Lift to safe Z
          2. Move XY to old pen rack position (offset by approach_dist in retract direction)
          3. Lower Z to slot Z
          4. Move forward approach_dist (engage holder) - SLOT GRIPS THE PEN
          5. Move backward approach_dist (retract empty) - PEN STAYS IN HOLDER
          6. Lift to safe Z
        
        PICK UP next pen:
          7. Move XY to new pen rack offset position
          8. Lower Z to slot Z
          9. Move forward approach_dist (engage pen) - HEAD GRABS PEN
          10. Lift to safe Z with new pen
        """
        current_slot = self.tool_change_slots.get(current_tool) if current_tool else None
        next_slot = self.tool_change_slots.get(next_tool) if next_tool else None
        dwell_seconds = max(self.tool_change_dwell_ms, 0) / 1000.0
        approach = self.tool_change_approach_dist
        travel_f = self.tool_change_travel_feed
        pickup_f = self.tool_change_pickup_feed

        lines.append("; ========= AUTOMATIC PEN CHANGE =========")
        lines.append("G90 ; absolute positioning")
        lines.append(f"G0 Z{self.tool_change_safe_z:.3f} F{travel_f} ; safe Z")

        # ----- DROP OFF old pen -----
        if current_tool and current_slot:
            # Approach offset (start position is BACKWARD from slot)
            dx_off, dy_off = self._approach_offset(-1)  # retract direction
            approach_x = current_slot['x'] + dx_off
            approach_y = current_slot['y'] + dy_off

            lines.append(f"; --- Drop {self._tool_change_comment(current_tool)} into rack slot {current_tool} ---")
            lines.append(f"G0 X{approach_x:.3f} Y{approach_y:.3f} F{travel_f} ; pre-approach")
            lines.append(f"G0 Z{current_slot['z']:.3f} F{travel_f} ; lower to slot Z")

            # Move FORWARD into slot (the slot's clamp grabs the pen)
            lines.append(f"G1 X{current_slot['x']:.3f} Y{current_slot['y']:.3f} F{pickup_f} ; insert pen into holder")
            if dwell_seconds > 0:
                lines.append(f"G4 P{dwell_seconds:.3f} ; settle")

            # Move BACKWARD - leave pen behind in holder
            lines.append(f"G1 X{approach_x:.3f} Y{approach_y:.3f} F{pickup_f} ; retract (leave pen in slot)")
            lines.append(f"G0 Z{self.tool_change_safe_z:.3f} F{travel_f} ; lift")
        elif not current_tool:
            lines.append("; --- Initial state: no pen loaded ---")

        # ----- PICK UP new pen -----
        if next_tool and next_slot:
            dx_off, dy_off = self._approach_offset(-1)  # start retracted
            approach_x = next_slot['x'] + dx_off
            approach_y = next_slot['y'] + dy_off

            lines.append(f"; --- Pick {self._tool_change_comment(next_tool)} from rack slot {next_tool} ---")
            lines.append(f"G0 X{approach_x:.3f} Y{approach_y:.3f} F{travel_f} ; pre-approach")
            lines.append(f"G0 Z{next_slot['z']:.3f} F{travel_f} ; lower to grab height")

            # Move FORWARD to engage pen (head clamp grabs it)
            lines.append(f"G1 X{next_slot['x']:.3f} Y{next_slot['y']:.3f} F{pickup_f} ; engage pen")
            if dwell_seconds > 0:
                lines.append(f"G4 P{dwell_seconds:.3f} ; clamp settle")

            lines.append(f"G0 Z{self.tool_change_safe_z:.3f} F{travel_f} ; lift with new pen")

        lines.append("; ========= END PEN CHANGE =========")

    def generate(self, strokes):
        gcode = []
        gcode.append("; ============================================")
        gcode.append(f"; Generated by {APP_NAME} v{APP_VERSION}")
        gcode.append(f"; Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        gcode.append(f"; Plot area: {self.plot_width}x{self.plot_height} mm")
        gcode.append(f"; Auto pen change: {'ENABLED' if self.tool_change_enabled else 'disabled'}")
        if self.tool_change_enabled:
            gcode.append(f"; Approach axis: {self.tool_change_approach_axis} "
                        f"distance: {self.tool_change_approach_dist}mm")
            gcode.append(f"; Configured pen slots: {sorted(self.tool_change_slots.keys())}")
        gcode.append("; ============================================")
        gcode.append("")
        gcode.append("G21 ; mm units")
        gcode.append("G90 ; absolute positioning")
        gcode.append("G17 ; XY plane")
        gcode.append(f"G0 Z{self.pen_up_z} ; pen up")
        gcode.append("")

        # Group strokes by color
        color_groups = {}
        color_order = []
        for stroke in strokes:
            if stroke.get('type') in ('image', 'svg'):
                continue
            color = stroke.get('color', QColor(0, 0, 0))
            key = (color.red(), color.green(), color.blue())
            if key not in color_groups:
                color_groups[key] = []
                color_order.append(key)
            color_groups[key].append(stroke)

        current_tool = self.tool_change_start_tool if self.tool_change_enabled else None

        for color_key in color_order:
            group = color_groups[color_key]
            color = QColor(*color_key)
            tool = self.color_to_tool(color)

            if tool != current_tool:
                gcode.append("")
                gcode.append(f"; ===== Color change to RGB{color_key} (Tool T{tool}) =====")
                gcode.append(f"G0 Z{self.pen_up_z} ; pen up")

                if self.tool_change_enabled and tool in self.tool_change_slots:
                    self._append_auto_tool_change(gcode, current_tool, tool)
                    gcode.append(f"(MSG, Auto-loaded {self._tool_change_comment(tool)})")
                else:
                    # Fallback: pause and prompt user
                    gcode.append(f"(MSG, Change pen to {self._tool_change_comment(tool)} - RGB{color_key})")
                    gcode.append("M0 ; pause for manual pen change")
                current_tool = tool

            gcode.append(f"; --- Drawing with {self._tool_change_comment(tool)} ---")
            for stroke in group:
                gcode.extend(self._stroke_to_gcode(stroke))

        # Return final pen to rack
        if (self.tool_change_enabled and self.tool_change_return_to_rack
                and current_tool and current_tool in self.tool_change_slots):
            gcode.append("")
            gcode.append("; ===== Return final pen to rack =====")
            gcode.append(f"G0 Z{self.pen_up_z} ; pen up")
            self._append_auto_tool_change(gcode, current_tool, 0)

        gcode.append("")
        gcode.append(f"G0 Z{self.pen_up_z} ; pen up")
        gcode.append("G0 X0 Y0 ; return to origin")
        gcode.append("M5 ; spindle off")
        gcode.append("M30 ; program end")
        return "\n".join(gcode)

    def _stroke_to_gcode(self, stroke):
        lines = []
        stype = stroke.get('type', 'path')
        item = stroke.get('item')
        offset_x, offset_y = 0, 0
        if item is not None:
            offset_x = item.pos().x()
            offset_y = item.pos().y()

        if stype == 'text':
            pos = stroke['pos']
            if item is not None:
                actual_x = pos.x() + item.pos().x()
                actual_y = pos.y() + item.pos().y()
            else:
                actual_x, actual_y = pos.x(), pos.y()
            x, y = self.scale_point(actual_x, actual_y)
            lines.append(f"; Text: {stroke['text']}")
            lines.append(f"; Font: {stroke['font'].family()} size {stroke['font'].pointSize()}")
            lines.append(f"G0 X{x:.3f} Y{y:.3f}")
            return lines

        path = stroke.get('path')
        if path is None or path.isEmpty():
            return lines
        length = path.length()
        if length == 0:
            return lines
        num_samples = max(int(length / 2), 10)
        first = True
        for i in range(num_samples + 1):
            t = i / num_samples
            point = path.pointAtPercent(t)
            actual_x = point.x() + offset_x
            actual_y = point.y() + offset_y
            x, y = self.scale_point(actual_x, actual_y)
            if first:
                lines.append(f"G0 X{x:.3f} Y{y:.3f}")
                lines.append(f"G1 Z{self.pen_down_z} F{self.feed_rate}")
                first = False
            else:
                lines.append(f"G1 X{x:.3f} Y{y:.3f} F{self.feed_rate}")
        lines.append(f"G0 Z{self.pen_up_z}")
        return lines
# ============================================================
# SERIAL PANEL
# ============================================================

class SerialPanel(QWidget):
    def __init__(self, serial_worker, parent=None):
        super().__init__(parent)
        self.serial_worker = serial_worker
        self.setup_ui()
        self.refresh_ports()
        self.refresh_timer = QTimer()
        self.refresh_timer.timeout.connect(self.refresh_ports)
        self.refresh_timer.start(3000)

    def setup_ui(self):
        layout = QVBoxLayout(self)
        conn_group = QGroupBox("cncjs Machine Port")
        conn_layout = QFormLayout()
        port_row = QHBoxLayout()
        self.port_combo = QComboBox()
        self.port_combo.setMinimumWidth(180)
        refresh_btn = QPushButton("↻")
        refresh_btn.setMaximumWidth(35)
        refresh_btn.clicked.connect(self.refresh_ports)
        port_row.addWidget(self.port_combo)
        port_row.addWidget(refresh_btn)
        conn_layout.addRow("Port:", port_row)

        self.baud_combo = QComboBox()
        for baud in COMMON_BAUD_RATES:
            self.baud_combo.addItem(str(baud))
        self.baud_combo.setCurrentText("115200")
        self.baud_combo.setEditable(True)
        conn_layout.addRow("Baud:", self.baud_combo)

        self.connect_btn = QPushButton("CONNECT MACHINE")
        self.connect_btn.setStyleSheet("""
            QPushButton { background-color: #2196F3; color: white;
                padding: 9px; font-weight: bold; border-radius: 4px; }
            QPushButton:hover { background-color: #1976D2; }
        """)
        self.connect_btn.clicked.connect(self.toggle_connection)
        conn_layout.addRow(self.connect_btn)

        self.status_label = QLabel("⚫ Machine disconnected")
        self.status_label.setStyleSheet("color: #ff6666; font-weight: bold;")
        self.status_label.setWordWrap(True)
        conn_layout.addRow("Status:", self.status_label)
        conn_group.setLayout(conn_layout)
        layout.addWidget(conn_group)

        tcp_group = QGroupBox("cncjs Server")
        tcp_layout = QFormLayout()
        self.tcp_host = QLineEdit("127.0.0.1")
        self.tcp_port = QSpinBox()
        self.tcp_port.setRange(1, 65535)
        self.tcp_port.setValue(8000)
        self.server_status_label = QLabel("⚫ Waiting for cncjs")
        self.server_status_label.setWordWrap(True)
        tcp_layout.addRow("Host:", self.tcp_host)
        tcp_layout.addRow("Port:", self.tcp_port)
        tcp_layout.addRow("Server:", self.server_status_label)
        tcp_group.setLayout(tcp_layout)
        layout.addWidget(tcp_group)
        layout.addStretch()

        self.serial_worker.connection_status.connect(self.update_status)
        self.serial_worker.server_status_changed.connect(self.update_server_status)

    def refresh_ports(self):
        if not SERIAL_AVAILABLE:
            self.port_combo.clear()
            self.port_combo.addItem("pyserial not installed!")
            return
        current = self.port_combo.currentData()
        self.port_combo.clear()
        ports = serial.tools.list_ports.comports()

        usb_ports = []
        other_ports = []
        for port in ports:
            device = port.device
            desc = port.description or ""
            if re.match(r'/dev/ttyS\d+', device):
                if desc.lower() in ('n/a', '', 'ttysx'):
                    continue
            label = f"{device}"
            if desc and desc.lower() != 'n/a':
                label += f" — {desc[:40]}"
            if port.manufacturer:
                label += f" [{port.manufacturer}]"
            if re.match(r'/dev/tty(USB|ACM)\d+', device):
                usb_ports.append((label, device))
            else:
                other_ports.append((label, device))

        by_id_links = glob.glob('/dev/serial/by-id/*')
        for link in by_id_links:
            try:
                target = os.path.realpath(link)
                name = os.path.basename(link)
                if not any(target == dev for _, dev in usb_ports):
                    usb_ports.append((f"{target} — {name}", target))
            except Exception:
                pass

        if not usb_ports and not other_ports:
            self.port_combo.addItem("No USB devices detected", None)
            return

        if usb_ports:
            self.port_combo.addItem("── USB Devices ──", None)
            for label, device in usb_ports:
                self.port_combo.addItem(f"  {label}", device)
        if other_ports:
            if usb_ports:
                self.port_combo.addItem("── Other Ports ──", None)
            for label, device in other_ports:
                self.port_combo.addItem(f"  {label}", device)

        if current:
            idx = self.port_combo.findData(current)
            if idx >= 0:
                self.port_combo.setCurrentIndex(idx)
                return
        if usb_ports:
            self.port_combo.setCurrentIndex(1 if self.port_combo.count() > 1 else 0)

    def toggle_connection(self):
        if self.serial_worker.is_connected:
            self.serial_worker.disconnect_serial()
        else:
            port_data = self.port_combo.currentData()
            if not port_data:
                QMessageBox.warning(self, "Invalid Port",
                    "Please select a valid serial port (not a header)")
                return
            try:
                baud = int(self.baud_combo.currentText())
            except ValueError:
                baud = 115200
            self.serial_worker.connect_serial(port_data, baud)

    def update_status(self, connected, message):
        if connected:
            self.status_label.setText(f"🟢 {message}")
            self.status_label.setStyleSheet("color: #4CAF50; font-weight: bold;")
            self.connect_btn.setText("DISCONNECT")
            self.connect_btn.setStyleSheet("""
                QPushButton { background-color: #f44336; color: white;
                    padding: 9px; font-weight: bold; border-radius: 4px; }
                QPushButton:hover { background-color: #d32f2f; }
            """)
        else:
            self.status_label.setText(f"⚫ {message}")
            self.status_label.setStyleSheet("color: #ff6666; font-weight: bold;")
            self.connect_btn.setText("CONNECT MACHINE")
            self.connect_btn.setStyleSheet("""
                QPushButton { background-color: #2196F3; color: white;
                    padding: 9px; font-weight: bold; border-radius: 4px; }
                QPushButton:hover { background-color: #1976D2; }
            """)

    def update_server_status(self, running, message):
        prefix = "🟢" if running else "⚫"
        color = "#4CAF50" if running else "#ff6666"
        self.server_status_label.setText(f"{prefix} {message}")
        self.server_status_label.setStyleSheet(f"color: {color}; font-weight: bold;")
        self.tcp_host.setText(self.serial_worker.cncjs_host)
        self.tcp_port.setValue(self.serial_worker.cncjs_port)


# ============================================================
# SAFETY CONTROL PANEL
# ============================================================

class SafetyControlPanel(QWidget):
    def __init__(self, serial_worker, parent=None):
        super().__init__(parent)
        self.serial_worker = serial_worker
        self.setup_ui()
        self.serial_worker.alarm_triggered.connect(self.on_alarm)
        self.serial_worker.error_triggered.connect(self.on_error)

    def setup_ui(self):
        layout = QVBoxLayout(self)
        self.estop_btn = QPushButton("🛑 EMERGENCY STOP")
        self.estop_btn.setMinimumHeight(60)
        self.estop_btn.setStyleSheet("""
            QPushButton { background-color: #d32f2f; color: white;
                font-size: 16px; font-weight: bold;
                border: 3px solid #b71c1c; border-radius: 8px; }
            QPushButton:hover { background-color: #b71c1c; }
            QPushButton:pressed { background-color: #8b0000; }
        """)
        self.estop_btn.clicked.connect(self.emergency_stop)
        layout.addWidget(self.estop_btn)

        ctrl_group = QGroupBox("Machine Control")
        ctrl_layout = QVBoxLayout()
        pause_row = QHBoxLayout()
        self.pause_btn = QPushButton("⏸ PAUSE")
        self.pause_btn.setStyleSheet("background-color: #ff9800; color: white; padding: 7px; font-weight: bold;")
        self.pause_btn.clicked.connect(self.serial_worker.pause)
        self.resume_btn = QPushButton("▶ RESUME")
        self.resume_btn.setStyleSheet("background-color: #4CAF50; color: white; padding: 7px; font-weight: bold;")
        self.resume_btn.clicked.connect(self.serial_worker.resume)
        pause_row.addWidget(self.pause_btn)
        pause_row.addWidget(self.resume_btn)
        ctrl_layout.addLayout(pause_row)

        home_row = QHBoxLayout()
        self.home_btn = QPushButton("🏠 HOME")
        self.home_btn.setStyleSheet("background-color: #2196F3; color: white; padding: 7px; font-weight: bold;")
        self.home_btn.clicked.connect(self.home_machine)
        self.unlock_btn = QPushButton("🔓 UNLOCK")
        self.unlock_btn.setStyleSheet("background-color: #9c27b0; color: white; padding: 7px; font-weight: bold;")
        self.unlock_btn.clicked.connect(self.serial_worker.unlock_alarm)
        home_row.addWidget(self.home_btn)
        home_row.addWidget(self.unlock_btn)
        ctrl_layout.addLayout(home_row)
        ctrl_group.setLayout(ctrl_layout)
        layout.addWidget(ctrl_group)

        safety_group = QGroupBox("Safety Limits")
        safety_layout = QFormLayout()
        self.enable_soft_limits = QCheckBox("Soft Limits ($20=1)")
        self.enable_soft_limits.setChecked(True)
        self.enable_hard_limits = QCheckBox("Hard Limits ($21=1)")
        self.enable_hard_limits.setChecked(True)
        self.enable_homing = QCheckBox("Homing ($22=1)")
        self.enable_homing.setChecked(True)
        safety_layout.addRow(self.enable_soft_limits)
        safety_layout.addRow(self.enable_hard_limits)
        safety_layout.addRow(self.enable_homing)
        apply_safety_btn = QPushButton("Apply Safety Settings")
        apply_safety_btn.clicked.connect(self.apply_safety_settings)
        safety_layout.addRow(apply_safety_btn)
        safety_group.setLayout(safety_layout)
        layout.addWidget(safety_group)

        alarm_group = QGroupBox("Alarm & Error Log")
        alarm_layout = QVBoxLayout()
        self.alarm_log = QTextEdit()
        self.alarm_log.setReadOnly(True)
        self.alarm_log.setMaximumHeight(90)
        self.alarm_log.setStyleSheet("""
            QTextEdit { background-color: #1a1a1a; color: #ff6666;
                font-family: 'Courier New', monospace; font-size: 10px; }
        """)
        alarm_layout.addWidget(self.alarm_log)
        clear_log_btn = QPushButton("Clear Log")
        clear_log_btn.clicked.connect(self.alarm_log.clear)
        alarm_layout.addWidget(clear_log_btn)
        alarm_group.setLayout(alarm_layout)
        layout.addWidget(alarm_group)
        layout.addStretch()

    def emergency_stop(self):
        self.serial_worker.emergency_stop()
        self.alarm_log.append(f"[{datetime.now().strftime('%H:%M:%S')}] EMERGENCY STOP")
        QMessageBox.critical(self, "EMERGENCY STOP",
            "Emergency stop sent!\n\nMachine halted.\nUse UNLOCK after checking.")

    def home_machine(self):
        reply = QMessageBox.question(self, "Home Machine",
            "Start homing cycle?\nMake sure path is clear!",
            QMessageBox.Yes | QMessageBox.No)
        if reply == QMessageBox.Yes:
            self.serial_worker.home()
            self.alarm_log.append(f"[{datetime.now().strftime('%H:%M:%S')}] Homing")

    def apply_safety_settings(self):
        if not self.serial_worker.is_connected:
            QMessageBox.warning(self, "Not Connected", "Connect first")
            return
        settings = [
            f"$20={'1' if self.enable_soft_limits.isChecked() else '0'}",
            f"$21={'1' if self.enable_hard_limits.isChecked() else '0'}",
            f"$22={'1' if self.enable_homing.isChecked() else '0'}",
        ]
        for cmd in settings:
            self.serial_worker.send_raw(cmd)
            time.sleep(0.1)
        QMessageBox.information(self, "Applied", "Safety settings sent")

    def on_alarm(self, message):
        timestamp = datetime.now().strftime('%H:%M:%S')
        self.alarm_log.append(f"[{timestamp}] ALARM: {message}")
        msg = QMessageBox(self)
        msg.setIcon(QMessageBox.Critical)
        msg.setWindowTitle("GRBL ALARM")
        msg.setText("<h3 style='color:red'>ALARM TRIGGERED</h3>")
        msg.setInformativeText(f"<b>{message}</b><br><br>Machine stopped for safety.")
        msg.exec_()

    def on_error(self, message):
        timestamp = datetime.now().strftime('%H:%M:%S')
        self.alarm_log.append(f"[{timestamp}] ERROR: {message}")


# ============================================================
# CONSOLE WIDGET
# ============================================================

class ConsoleWidget(QWidget):
    def __init__(self, serial_worker, parent=None):
        super().__init__(parent)
        self.serial_worker = serial_worker
        self.setup_ui()
        self.serial_worker.data_received.connect(self.on_data)

    def setup_ui(self):
        layout = QVBoxLayout(self)
        self.progress = QProgressBar()
        self.progress.setStyleSheet("""
            QProgressBar { border: 1px solid #555; border-radius: 3px;
                text-align: center; color: white; }
            QProgressBar::chunk { background-color: #4CAF50; border-radius: 2px; }
        """)
        layout.addWidget(self.progress)
        self.serial_worker.progress_updated.connect(self.update_progress)

        self.console = QTextEdit()
        self.console.setReadOnly(True)
        self.console.setStyleSheet("""
            QTextEdit { background-color: #0d0d0d; color: #00ff00;
                font-family: 'Courier New', monospace; font-size: 10px; }
        """)
        layout.addWidget(self.console)

        input_row = QHBoxLayout()
        self.cmd_input = QLineEdit()
        self.cmd_input.setPlaceholderText("GRBL command (e.g., $$, $H, ?, G0 X10)")
        self.cmd_input.returnPressed.connect(self.send_command)
        self.cmd_input.setStyleSheet("""
            QLineEdit { background-color: #1a1a1a; color: #00ff00;
                font-family: 'Courier New', monospace;
                padding: 5px; border: 1px solid #555; }
        """)
        send_btn = QPushButton("Send")
        send_btn.clicked.connect(self.send_command)
        input_row.addWidget(self.cmd_input)
        input_row.addWidget(send_btn)
        layout.addLayout(input_row)

        quick_row = QHBoxLayout()
        for label, cmd in [("? Status", "?"), ("$$", "$$"), ("$X", "$X"), ("$H", "$H")]:
            btn = QPushButton(label)
            btn.clicked.connect(lambda checked, c=cmd: self.serial_worker.send_raw(c))
            quick_row.addWidget(btn)
        layout.addLayout(quick_row)

    def send_command(self):
        cmd = self.cmd_input.text().strip()
        if cmd:
            self.console.append(f">>> {cmd}")
            self.serial_worker.send_raw(cmd)
            self.cmd_input.clear()

    def on_data(self, data):
        self.console.append(data)
        # Limit console history (Pi 4 perf)
        if self.console.document().lineCount() > 500:
            cursor = self.console.textCursor()
            cursor.movePosition(cursor.Start)
            cursor.movePosition(cursor.Down, cursor.KeepAnchor, 100)
            cursor.removeSelectedText()
        scrollbar = self.console.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def update_progress(self, current, total):
        if total > 0:
            pct = int((current / total) * 100)
            self.progress.setValue(pct)
            self.progress.setFormat(f"{current}/{total} ({pct}%)")


# ============================================================
# COLOR PALETTE
# ============================================================

class ColorPalette(QWidget):
    def __init__(self, canvas, parent=None):
        super().__init__(parent)
        self.canvas = canvas
        self.current_color = QColor(0, 0, 0)
        self.setup_ui()

    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(5, 5, 5, 5)
        title = QLabel("Pen Colors")
        title.setStyleSheet("font-weight: bold; font-size: 13px;")
        layout.addWidget(title)
        for preset in PEN_PRESETS:
            color = QColor(*preset["rgb"])
            tool = preset["tool"]
            name = preset["name"]
            btn = QPushButton(f"  Pen {tool}: {name}")
            btn.setStyleSheet(f"""
                QPushButton {{
                    background-color: {color.name()};
                    color: {'white' if color.lightness() < 128 else 'black'};
                    border: 2px solid #333; padding: 7px;
                    font-weight: bold; text-align: left; }}
                QPushButton:hover {{ border: 3px solid #00aaff; }}
            """)
            btn.clicked.connect(lambda checked, c=color: self.select_color(c))
            layout.addWidget(btn)
        custom_btn = QPushButton("Custom Color...")
        custom_btn.setStyleSheet("padding: 7px; font-weight: bold;")
        custom_btn.clicked.connect(self.pick_custom_color)
        layout.addWidget(custom_btn)
        self.current_label = QLabel("Current: Black")
        self.current_display = QFrame()
        self.current_display.setFixedHeight(28)
        self.current_display.setStyleSheet("background-color: black; border: 2px solid #333;")
        layout.addWidget(self.current_label)
        layout.addWidget(self.current_display)
        layout.addStretch()

    def select_color(self, color):
        self.current_color = color
        self.canvas.set_pen_color(color)
        self.current_display.setStyleSheet(
            f"background-color: {color.name()}; border: 2px solid #333;")
        self.current_label.setText(f"Current: {color.name()}")

    def pick_custom_color(self):
        color = QColorDialog.getColor(self.current_color, self)
        if color.isValid():
            self.select_color(color)


# ============================================================
# JOG PANEL (DRO + manual movement)
# ============================================================

class JogPanel(QWidget):
    position_save_requested = pyqtSignal(dict)

    def __init__(self, serial_worker, parent=None):
        super().__init__(parent)
        self.serial_worker = serial_worker
        self.setup_ui()
        self.serial_worker.machine_position_changed.connect(self.update_dro)

    def setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(4, 4, 4, 4)

        dro_group = QGroupBox("DRO (machine position)")
        dro_lay = QGridLayout()
        self.dro_labels = {}
        for col, axis in enumerate(("X", "Y", "Z", "A")):
            header = QLabel(axis)
            header.setStyleSheet("font-weight:bold; font-size:12px; color:#89b4fa;")
            header.setAlignment(Qt.AlignCenter)
            dro_lay.addWidget(header, 0, col)
            val = QLabel("---")
            val.setAlignment(Qt.AlignCenter)
            val.setStyleSheet(
                "font-family:'Courier New',monospace; font-size:13px; "
                "color:#a6e3a1; background:#181825; padding:3px; border-radius:3px;")
            dro_lay.addWidget(val, 1, col)
            self.dro_labels[axis] = val
        self.state_label = QLabel("State: ---")
        self.state_label.setStyleSheet("font-size:11px; color:#f9e2af;")
        dro_lay.addWidget(self.state_label, 2, 0, 1, 4)
        dro_group.setLayout(dro_lay)
        root.addWidget(dro_group)

        step_group = QGroupBox("Step / Feed")
        step_lay = QFormLayout()
        self.step_combo = QComboBox()
        for s in ("0.1", "0.5", "1", "5", "10", "50"):
            self.step_combo.addItem(f"{s} mm", float(s))
        self.step_combo.setCurrentIndex(2)
        step_lay.addRow("Step:", self.step_combo)
        self.jog_feed_spin = QSpinBox()
        self.jog_feed_spin.setRange(10, 10000)
        self.jog_feed_spin.setValue(1200)
        self.jog_feed_spin.setSuffix(" mm/min")
        step_lay.addRow("Feed:", self.jog_feed_spin)
        step_group.setLayout(step_lay)
        root.addWidget(step_group)

        xy_group = QGroupBox("XY Jog")
        xy_grid = QGridLayout()
        xy_grid.setSpacing(2)
        btn_y_plus = QPushButton("Y+")
        btn_y_minus = QPushButton("Y-")
        btn_x_minus = QPushButton("X-")
        btn_x_plus = QPushButton("X+")
        btn_home_xy = QPushButton("⌂")
        for b in (btn_y_plus, btn_y_minus, btn_x_minus, btn_x_plus, btn_home_xy):
            b.setFixedSize(50, 36)
            b.setStyleSheet("font-weight:bold; font-size:12px;")
        btn_y_plus.clicked.connect(lambda: self._jog(dy=self._step()))
        btn_y_minus.clicked.connect(lambda: self._jog(dy=-self._step()))
        btn_x_minus.clicked.connect(lambda: self._jog(dx=-self._step()))
        btn_x_plus.clicked.connect(lambda: self._jog(dx=self._step()))
        btn_home_xy.clicked.connect(lambda: self.serial_worker.send_raw("G0 X0 Y0"))
        xy_grid.addWidget(btn_y_plus, 0, 1)
        xy_grid.addWidget(btn_x_minus, 1, 0)
        xy_grid.addWidget(btn_home_xy, 1, 1)
        xy_grid.addWidget(btn_x_plus, 1, 2)
        xy_grid.addWidget(btn_y_minus, 2, 1)
        xy_group.setLayout(xy_grid)
        root.addWidget(xy_group)

        za_group = QGroupBox("Z / A Jog")
        za_lay = QGridLayout()
        za_lay.setSpacing(2)
        btn_z_plus = QPushButton("Z+")
        btn_z_minus = QPushButton("Z-")
        btn_a_plus = QPushButton("A+")
        btn_a_minus = QPushButton("A-")
        for b in (btn_z_plus, btn_z_minus, btn_a_plus, btn_a_minus):
            b.setFixedSize(50, 36)
            b.setStyleSheet("font-weight:bold; font-size:12px;")
        btn_z_plus.clicked.connect(lambda: self._jog(dz=self._step()))
        btn_z_minus.clicked.connect(lambda: self._jog(dz=-self._step()))
        btn_a_plus.clicked.connect(lambda: self._jog(da=self._step()))
        btn_a_minus.clicked.connect(lambda: self._jog(da=-self._step()))
        za_lay.addWidget(QLabel("Z"), 0, 0, Qt.AlignCenter)
        za_lay.addWidget(btn_z_plus, 0, 1)
        za_lay.addWidget(btn_z_minus, 0, 2)
        za_lay.addWidget(QLabel("A"), 1, 0, Qt.AlignCenter)
        za_lay.addWidget(btn_a_plus, 1, 1)
        za_lay.addWidget(btn_a_minus, 1, 2)
        za_group.setLayout(za_lay)
        root.addWidget(za_group)

        save_btn = QPushButton("Save Position to Pen Slot...")
        save_btn.setStyleSheet(
            "background-color:#f38ba8; color:#1e1e2e; font-weight:bold; padding:7px;")
        save_btn.clicked.connect(self._emit_save_position)
        root.addWidget(save_btn)

        get_status_btn = QPushButton("Refresh Status (?)")
        get_status_btn.clicked.connect(self.serial_worker.get_status)
        root.addWidget(get_status_btn)

        root.addStretch()

    def _step(self):
        return self.step_combo.currentData() or 1.0

    def _jog(self, dx=0.0, dy=0.0, dz=0.0, da=0.0):
        self.serial_worker.jog_incremental(
            dx=dx, dy=dy, dz=dz, da=da, feed=self.jog_feed_spin.value())

    def update_dro(self, pos):
        for axis in ("X", "Y", "Z", "A"):
            val = pos.get(axis)
            text = f"{val:.3f}" if val is not None else "---"
            self.dro_labels[axis].setText(text)
        state = pos.get("state", "---")
        self.state_label.setText(f"State: {state}")

    def _emit_save_position(self):
        pos = dict(self.serial_worker.machine_position)
        if pos.get("X") is None:
            QMessageBox.warning(self, "No Position",
                "Machine position unknown.\nConnect and query status first.")
            return
        self.position_save_requested.emit(pos)


# ============================================================
# PEN CHANGER PANEL — automatic pen rack mapping
# ============================================================

class PenChangerPanel(QWidget):
    settings_changed = pyqtSignal()

    def __init__(self, serial_worker, parent=None):
        super().__init__(parent)
        self.serial_worker = serial_worker
        self.slot_data = {}
        self.setup_ui()

    def setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(4, 4, 4, 4)

        header = QLabel(
            "<b>Automatic Pen Changer Setup (forward/backward pickup)</b><br>"
            "<span style='font-size:10px;color:#a6adc8'>"
            "Saved position = where the pen sits in its holder.<br>"
            "Robot approaches from offset distance, moves IN to engage, "
            "OUT to retract.</span>")
        header.setWordWrap(True)
        root.addWidget(header)

        self.enable_check = QCheckBox("Enable automatic pen changer")
        self.enable_check.setStyleSheet("font-weight:bold; color:#a6e3a1;")
        self.enable_check.toggled.connect(lambda: self.settings_changed.emit())
        root.addWidget(self.enable_check)

        geom_group = QGroupBox("Approach Geometry")
        geom_lay = QFormLayout()

        self.approach_axis_combo = QComboBox()
        self.approach_axis_combo.addItems(["Y", "X"])
        self.approach_axis_combo.currentTextChanged.connect(
            lambda: self.settings_changed.emit())
        geom_lay.addRow("Approach axis:", self.approach_axis_combo)

        self.approach_dir_combo = QComboBox()
        self.approach_dir_combo.addItem("Forward (+) grabs pen", 1)
        self.approach_dir_combo.addItem("Backward (−) grabs pen", -1)
        self.approach_dir_combo.currentIndexChanged.connect(
            lambda: self.settings_changed.emit())
        geom_lay.addRow("Direction:", self.approach_dir_combo)

        self.approach_dist_spin = QDoubleSpinBox()
        self.approach_dist_spin.setRange(1, 500)
        self.approach_dist_spin.setValue(100)  # 10cm
        self.approach_dist_spin.setSingleStep(5)
        self.approach_dist_spin.setSuffix(" mm")
        self.approach_dist_spin.valueChanged.connect(
            lambda: self.settings_changed.emit())
        geom_lay.addRow("Approach distance:", self.approach_dist_spin)

        self.safe_z_spin = QDoubleSpinBox()
        self.safe_z_spin.setRange(0, 100)
        self.safe_z_spin.setValue(15)
        self.safe_z_spin.setSuffix(" mm")
        self.safe_z_spin.valueChanged.connect(lambda: self.settings_changed.emit())
        geom_lay.addRow("Safe Z:", self.safe_z_spin)

        self.travel_feed_spin = QSpinBox()
        self.travel_feed_spin.setRange(100, 20000)
        self.travel_feed_spin.setValue(3000)
        self.travel_feed_spin.setSuffix(" mm/min")
        self.travel_feed_spin.valueChanged.connect(
            lambda: self.settings_changed.emit())
        geom_lay.addRow("Travel feed:", self.travel_feed_spin)

        self.pickup_feed_spin = QSpinBox()
        self.pickup_feed_spin.setRange(50, 5000)
        self.pickup_feed_spin.setValue(800)
        self.pickup_feed_spin.setSuffix(" mm/min")
        self.pickup_feed_spin.valueChanged.connect(
            lambda: self.settings_changed.emit())
        geom_lay.addRow("Pickup feed (slow):", self.pickup_feed_spin)

        self.dwell_spin = QSpinBox()
        self.dwell_spin.setRange(0, 5000)
        self.dwell_spin.setValue(300)
        self.dwell_spin.setSuffix(" ms")
        self.dwell_spin.valueChanged.connect(lambda: self.settings_changed.emit())
        geom_lay.addRow("Dwell:", self.dwell_spin)

        self.return_check = QCheckBox("Return final pen to rack after job")
        self.return_check.setChecked(True)
        self.return_check.toggled.connect(lambda: self.settings_changed.emit())
        geom_lay.addRow(self.return_check)

        geom_group.setLayout(geom_lay)
        root.addWidget(geom_group)

        slot_group = QGroupBox("Pen Rack Slot Positions (X / Y / Z)")
        slot_lay = QVBoxLayout()
        hdr = QHBoxLayout()
        for text, w in [("Pen", 75), ("X", 60), ("Y", 60), ("Z", 60),
                        ("Save", 50), ("Goto", 50)]:
            lbl = QLabel(text)
            lbl.setFixedWidth(w)
            lbl.setStyleSheet("font-weight:bold; font-size:10px;")
            lbl.setAlignment(Qt.AlignCenter)
            hdr.addWidget(lbl)
        slot_lay.addLayout(hdr)

        self.slot_rows = {}
        for preset in PEN_PRESETS:
            tool = preset["tool"]
            name = preset["name"]
            rgb = preset["rgb"]
            row = QHBoxLayout()
            color_label = QLabel(f"T{tool} {name}")
            color_label.setFixedWidth(75)
            color_label.setStyleSheet(
                f"background-color:rgb({rgb[0]},{rgb[1]},{rgb[2]});"
                f"color:{'white' if sum(rgb)<380 else 'black'};"
                f"padding:3px; border-radius:3px; font-weight:bold; font-size:10px;")
            row.addWidget(color_label)

            x_lbl = QLabel("---")
            y_lbl = QLabel("---")
            z_lbl = QLabel("---")
            for lbl in (x_lbl, y_lbl, z_lbl):
                lbl.setFixedWidth(60)
                lbl.setAlignment(Qt.AlignCenter)
                lbl.setStyleSheet(
                    "font-family:'Courier New'; font-size:10px; "
                    "background:#181825; padding:2px; border-radius:2px;")
            row.addWidget(x_lbl)
            row.addWidget(y_lbl)
            row.addWidget(z_lbl)

            save_btn = QPushButton("Save")
            save_btn.setFixedWidth(50)
            save_btn.setStyleSheet("font-size:10px; padding:2px;")
            save_btn.clicked.connect(lambda _, t=tool: self._save_current_position(t))
            row.addWidget(save_btn)

            goto_btn = QPushButton("Go")
            goto_btn.setFixedWidth(50)
            goto_btn.setStyleSheet(
                "font-size:10px; padding:2px; background-color:#f9e2af; color:#1e1e2e;")
            goto_btn.clicked.connect(lambda _, t=tool: self._goto_slot(t))
            row.addWidget(goto_btn)

            slot_lay.addLayout(row)
            self.slot_rows[tool] = {"x": x_lbl, "y": y_lbl, "z": z_lbl}
        slot_group.setLayout(slot_lay)
        root.addWidget(slot_group)

        test_row = QHBoxLayout()
        test_pickup_btn = QPushButton("Test Pickup Sequence")
        test_pickup_btn.setStyleSheet(
            "background-color:#a6e3a1; color:#1e1e2e; font-weight:bold;")
        test_pickup_btn.clicked.connect(self._test_pickup_sequence)
        clear_all_btn = QPushButton("Clear All Slots")
        clear_all_btn.setStyleSheet("background-color:#f38ba8; color:#1e1e2e;")
        clear_all_btn.clicked.connect(self._clear_all_slots)
        test_row.addWidget(test_pickup_btn)
        test_row.addWidget(clear_all_btn)
        root.addLayout(test_row)

        root.addStretch()

    def save_position_for_slot(self, pos_dict):
        tools = [f"T{p['tool']} {p['name']}" for p in PEN_PRESETS]
        choice, ok = QInputDialog.getItem(
            self, "Save Position to Pen Slot",
            "Which pen slot for this position?", tools, 0, False)
        if not ok:
            return
        tool = int(choice.split()[0][1:])
        self._store_slot(tool, pos_dict)

    def _save_current_position(self, tool):
        pos = dict(self.serial_worker.machine_position)
        if pos.get("X") is None:
            QMessageBox.warning(self, "No Position",
                "Machine position unknown.\nJog first and click Refresh Status.")
            return
        self._store_slot(tool, pos)

    def _store_slot(self, tool, pos):
        preset = TOOL_COLOR_MAP.get(tool, {})
        self.slot_data[tool] = {
            "name": preset.get("name", f"Pen {tool}"),
            "x": float(pos.get("X", 0) or 0),
            "y": float(pos.get("Y", 0) or 0),
            "z": float(pos.get("Z", 0) or 0),
        }
        self._refresh_slot_display(tool)
        self.settings_changed.emit()

    def _refresh_slot_display(self, tool):
        row = self.slot_rows.get(tool)
        data = self.slot_data.get(tool)
        if not row or not data:
            return
        row["x"].setText(f"{data['x']:.2f}")
        row["y"].setText(f"{data['y']:.2f}")
        row["z"].setText(f"{data['z']:.2f}")

    def _refresh_all_displays(self):
        for tool in self.slot_rows:
            if tool in self.slot_data:
                self._refresh_slot_display(tool)

    def _goto_slot(self, tool):
        slot = self.slot_data.get(tool)
        if not slot:
            QMessageBox.warning(self, "No Position",
                f"Pen {tool} position not saved yet")
            return
        if not self.serial_worker.is_connected:
            QMessageBox.warning(self, "Not Connected", "Connect to machine first")
            return
        reply = QMessageBox.question(
            self, "Goto Position",
            f"Move machine to:\nX: {slot['x']:.2f}\nY: {slot['y']:.2f}\nZ: {slot['z']:.2f}\n\nProceed?",
            QMessageBox.Yes | QMessageBox.No)
        if reply == QMessageBox.Yes:
            safe_z = self.safe_z_spin.value()
            self.serial_worker.send_raw(f"G0 Z{safe_z:.3f}")
            self.serial_worker.send_raw(f"G0 X{slot['x']:.3f} Y{slot['y']:.3f}")
            self.serial_worker.send_raw(f"G0 Z{slot['z']:.3f}")

    def _clear_all_slots(self):
        reply = QMessageBox.question(
            self, "Clear All Slots",
            "Remove all saved pen positions?",
            QMessageBox.Yes | QMessageBox.No)
        if reply == QMessageBox.Yes:
            self.slot_data.clear()
            for tool in self.slot_rows:
                row = self.slot_rows[tool]
                row["x"].setText("---")
                row["y"].setText("---")
                row["z"].setText("---")
            self.settings_changed.emit()

    def _test_pickup_sequence(self):
        """Test the approach/engage/retract cycle on first saved slot."""
        if not self.serial_worker.is_connected:
            QMessageBox.warning(self, "Not Connected", "Connect first")
            return
        if not self.slot_data:
            QMessageBox.warning(self, "No Slots", "Save at least one pen position")
            return
        first_tool = sorted(self.slot_data.keys())[0]
        slot = self.slot_data[first_tool]
        axis = self.approach_axis_combo.currentText()
        direction = self.approach_dir_combo.currentData()
        dist = self.approach_dist_spin.value()
        safe_z = self.safe_z_spin.value()
        travel_f = self.travel_feed_spin.value()
        pickup_f = self.pickup_feed_spin.value()

        # Pre-approach offset (retracted)
        if axis == "X":
            ax = slot['x'] - direction * dist
            ay = slot['y']
        else:
            ax = slot['x']
            ay = slot['y'] - direction * dist

        reply = QMessageBox.question(self, "Test Pickup",
            f"Test sequence on {slot['name']} (T{first_tool}):\n\n"
            f"1. Safe Z = {safe_z}\n"
            f"2. Move to approach ({ax:.1f}, {ay:.1f})\n"
            f"3. Lower to Z = {slot['z']}\n"
            f"4. Engage to ({slot['x']:.1f}, {slot['y']:.1f})\n"
            f"5. Retract back\n"
            f"6. Lift to safe Z\n\nProceed?",
            QMessageBox.Yes | QMessageBox.No)
        if reply != QMessageBox.Yes:
            return

        sw = self.serial_worker
        sw.send_raw("G90")
        sw.send_raw(f"G0 Z{safe_z:.3f} F{travel_f}")
        sw.send_raw(f"G0 X{ax:.3f} Y{ay:.3f} F{travel_f}")
        sw.send_raw(f"G0 Z{slot['z']:.3f} F{travel_f}")
        sw.send_raw(f"G1 X{slot['x']:.3f} Y{slot['y']:.3f} F{pickup_f}")
        sw.send_raw("G4 P0.5")
        sw.send_raw(f"G1 X{ax:.3f} Y{ay:.3f} F{pickup_f}")
        sw.send_raw(f"G0 Z{safe_z:.3f} F{travel_f}")

    def get_tool_changer_config(self):
        return {
            "enabled": self.enable_check.isChecked(),
            "slots": {str(k): v for k, v in self.slot_data.items()},
            "safe_z": self.safe_z_spin.value(),
            "approach_dist": self.approach_dist_spin.value(),
            "approach_axis": self.approach_axis_combo.currentText(),
            "approach_dir": self.approach_dir_combo.currentData(),
            "travel_feed": self.travel_feed_spin.value(),
            "pickup_feed": self.pickup_feed_spin.value(),
            "dwell_ms": self.dwell_spin.value(),
            "return_to_rack": self.return_check.isChecked(),
            "start_tool": 0,
        }

    def load_from_settings(self, cfg):
        if not cfg:
            return
        self.enable_check.setChecked(cfg.get("enabled", False))
        self.approach_dist_spin.setValue(cfg.get("approach_dist", 100))
        axis = cfg.get("approach_axis", "Y")
        idx = self.approach_axis_combo.findText(axis)
        if idx >= 0:
            self.approach_axis_combo.setCurrentIndex(idx)
        direction = cfg.get("approach_dir", 1)
        idx = self.approach_dir_combo.findData(direction)
        if idx >= 0:
            self.approach_dir_combo.setCurrentIndex(idx)
        self.safe_z_spin.setValue(cfg.get("safe_z", 15))
        self.travel_feed_spin.setValue(cfg.get("travel_feed", 3000))
        self.pickup_feed_spin.setValue(cfg.get("pickup_feed", 800))
        self.dwell_spin.setValue(cfg.get("dwell_ms", 300))
        self.return_check.setChecked(cfg.get("return_to_rack", True))
        slots = cfg.get("slots", {})
        for key, val in slots.items():
            try:
                tool = int(key)
            except (TypeError, ValueError):
                continue
            if isinstance(val, dict) and "x" in val:
                self.slot_data[tool] = val
        self._refresh_all_displays()


# ============================================================
# TOOL PANEL
# ============================================================

class ToolPanel(QWidget):
    def __init__(self, canvas, parent=None):
        super().__init__(parent)
        self.canvas = canvas
        self.setup_ui()

    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(5, 5, 5, 5)
        title = QLabel("Tools")
        title.setStyleSheet("font-weight: bold; font-size: 13px;")
        layout.addWidget(title)
        tools = [
            ("✏ Pen", "pen"), ("Eraser", "eraser"),
            ("Line", "line"), ("Rectangle", "rect"),
            ("Circle", "circle"), ("T Text", "text"),
            ("Select/Move", "select"), ("Pan", "pan"),
        ]
        for name, tool in tools:
            btn = QPushButton(name)
            btn.setStyleSheet("""
                QPushButton { padding: 8px; text-align: left; font-size: 12px; }
                QPushButton:hover { background-color: #585b70; }
            """)
            btn.clicked.connect(lambda checked, t=tool: self.canvas.set_tool(t))
            layout.addWidget(btn)
        layout.addWidget(QLabel("Pen Width:"))
        self.width_slider = QSlider(Qt.Horizontal)
        self.width_slider.setMinimum(1)
        self.width_slider.setMaximum(20)
        self.width_slider.setValue(2)
        self.width_label = QLabel("2 px")
        self.width_slider.valueChanged.connect(self.change_width)
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
        view_layout.addWidget(self.grid_check)
        view_layout.addWidget(self.axes_check)
        view_layout.addWidget(self.rulers_check)
        view_layout.addWidget(self.origin_check)
        view_group.setLayout(view_layout)
        layout.addWidget(view_group)
        layout.addStretch()

    def change_width(self, value):
        self.canvas.set_pen_width(value)
        self.width_label.setText(f"{value} px")
# ============================================================
# MAIN WINDOW
# ============================================================

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"{APP_NAME} v{APP_VERSION}")
        self.resize(1100, 700)
        self.setMinimumSize(800, 480)

        self.gcode_folder = Path(os.path.dirname(os.path.abspath(__file__))) / "gcode"
        self.gcode_folder.mkdir(exist_ok=True)

        self.serial_worker = SerialWorker()
        self.setup_ui()
        self.gcode_generator = GCodeGenerator(self.canvas)
        self.create_menus()
        self.create_toolbar()

        self.status = QStatusBar()
        self.setStatusBar(self.status)

        self.coord_label = QLabel("X: 0.00 mm   Y: 0.00 mm")
        self.coord_label.setStyleSheet("""
            QLabel { background-color: #1e1e2e; color: #89b4fa;
                padding: 3px 10px; border-radius: 3px;
                font-family: 'Courier New', monospace; font-weight: bold; }
        """)
        self.status.addPermanentWidget(self.coord_label)

        self.plot_size_label = QLabel("200 × 150 mm")
        self.plot_size_label.setStyleSheet("""
            QLabel { background-color: #1e1e2e; color: #a6e3a1;
                padding: 3px 10px; border-radius: 3px; font-weight: bold; }
        """)
        self.status.addPermanentWidget(self.plot_size_label)

        self.status.showMessage(f"Ready | G-code folder: {self.gcode_folder}")

        self.canvas.mouse_position_changed.connect(self.update_coords)
        self.serial_worker.connection_status.connect(self.on_connection_change)
        self.serial_worker.server_status_changed.connect(self.on_server_status_change)
        self.serial_worker.streaming_finished.connect(self.on_streaming_finished)

        self.load_settings()
        self.sync_worker_settings()

        QTimer.singleShot(200, self.canvas.fit_to_view)
        QTimer.singleShot(1500, self.auto_start_cncjs)

    def update_coords(self, x_mm, y_mm):
        self.coord_label.setText(f"X: {x_mm:7.2f} mm   Y: {y_mm:7.2f} mm")

    def setup_ui(self):
        self.canvas = DrawingCanvas(self)
        self.setCentralWidget(self.canvas)

        # LEFT: Tools (scrollable)
        tool_dock = QDockWidget("Tools", self)
        tool_dock.setAllowedAreas(Qt.LeftDockWidgetArea | Qt.RightDockWidgetArea)
        self.tool_panel = ToolPanel(self.canvas)
        tool_scroll = QScrollArea()
        tool_scroll.setWidget(self.tool_panel)
        tool_scroll.setWidgetResizable(True)
        tool_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        tool_dock.setWidget(tool_scroll)
        tool_dock.setMinimumWidth(170)
        tool_dock.setMaximumWidth(220)
        self.addDockWidget(Qt.LeftDockWidgetArea, tool_dock)
        self.tool_dock = tool_dock

        # RIGHT: Control panel tabs
        right_tabs = QTabWidget()
        right_tabs.setUsesScrollButtons(True)
        right_tabs.setElideMode(Qt.ElideRight)
        self.serial_panel = SerialPanel(self.serial_worker)
        right_tabs.addTab(self.serial_panel, "Connect")
        self.safety_panel = SafetyControlPanel(self.serial_worker)
        right_tabs.addTab(self.safety_panel, "Safety")
        self.jog_panel = JogPanel(self.serial_worker)
        right_tabs.addTab(self.jog_panel, "Jog")
        self.color_palette = ColorPalette(self.canvas)
        right_tabs.addTab(self.color_palette, "Colors")

        right_scroll = QScrollArea()
        right_scroll.setWidget(right_tabs)
        right_scroll.setWidgetResizable(True)
        right_dock = QDockWidget("Control Panel", self)
        right_dock.setWidget(right_scroll)
        right_dock.setMinimumWidth(290)
        right_dock.setMaximumWidth(360)
        self.addDockWidget(Qt.RightDockWidgetArea, right_dock)
        self.right_dock = right_dock

        # BOTTOM: Console + Settings + Pen Rack
        bottom_tabs = QTabWidget()
        bottom_tabs.setUsesScrollButtons(True)
        self.console = ConsoleWidget(self.serial_worker)
        bottom_tabs.addTab(self.console, "Console")

        settings_widget = self.create_settings_widget()
        settings_scroll = QScrollArea()
        settings_scroll.setWidget(settings_widget)
        settings_scroll.setWidgetResizable(True)
        bottom_tabs.addTab(settings_scroll, "Settings")

        self.pen_changer_panel = PenChangerPanel(self.serial_worker)
        pen_scroll = QScrollArea()
        pen_scroll.setWidget(self.pen_changer_panel)
        pen_scroll.setWidgetResizable(True)
        bottom_tabs.addTab(pen_scroll, "Pen Rack")

        # Wire pen rack
        self.jog_panel.position_save_requested.connect(
            self.pen_changer_panel.save_position_for_slot)
        self.pen_changer_panel.settings_changed.connect(self._sync_tool_changer)

        bottom_dock = QDockWidget("Console / Settings / Pen Rack", self)
        bottom_dock.setWidget(bottom_tabs)
        bottom_dock.setMaximumHeight(280)
        bottom_dock.setMinimumHeight(180)
        self.addDockWidget(Qt.BottomDockWidgetArea, bottom_dock)
        self.bottom_dock = bottom_dock

    def _sync_tool_changer(self):
        cfg = self.pen_changer_panel.get_tool_changer_config()
        self.gcode_generator.configure_tool_changer(**cfg)

    def on_streaming_finished(self, success, message):
        if success:
            self.status.showMessage(f"✅ {message}")
            QMessageBox.information(self, "Job Complete",
                f"Drawing finished successfully!\n\n{message}")
        else:
            self.status.showMessage(f"⚠ {message}")

    def create_settings_widget(self):
        widget = QWidget()
        layout = QHBoxLayout(widget)

        size_group = QGroupBox("Plot Area")
        size_layout = QFormLayout()
        self.plot_width_input = QSpinBox()
        self.plot_width_input.setRange(10, 2000)
        self.plot_width_input.setValue(200)
        self.plot_width_input.setSuffix(" mm")
        self.plot_width_input.valueChanged.connect(self.update_plot_size)
        self.plot_height_input = QSpinBox()
        self.plot_height_input.setRange(10, 2000)
        self.plot_height_input.setValue(150)
        self.plot_height_input.setSuffix(" mm")
        self.plot_height_input.valueChanged.connect(self.update_plot_size)
        size_layout.addRow("Width:", self.plot_width_input)
        size_layout.addRow("Height:", self.plot_height_input)
        preset_row = QHBoxLayout()
        for name, w, h in [("A5", 148, 210), ("A4", 210, 297),
                           ("A3", 297, 420), ("Letter", 216, 279)]:
            btn = QPushButton(name)
            btn.clicked.connect(lambda checked, w=w, h=h: self.set_preset_size(w, h))
            preset_row.addWidget(btn)
        size_layout.addRow("Presets:", preset_row)
        size_group.setLayout(size_layout)
        layout.addWidget(size_group)

        feed_group = QGroupBox("Feed / Z")
        feed_layout = QFormLayout()
        self.feed_input = QSpinBox()
        self.feed_input.setRange(100, 10000)
        self.feed_input.setValue(3000)
        self.feed_input.setSuffix(" mm/min")
        self.feed_input.valueChanged.connect(
            lambda v: setattr(self.gcode_generator, 'feed_rate', v))
        self.pen_up_input = QDoubleSpinBox()
        self.pen_up_input.setRange(0, 50)
        self.pen_up_input.setValue(5)
        self.pen_up_input.setSingleStep(0.5)
        self.pen_up_input.setSuffix(" mm")
        self.pen_up_input.valueChanged.connect(
            lambda v: setattr(self.gcode_generator, 'pen_up_z', v))
        self.pen_down_input = QDoubleSpinBox()
        self.pen_down_input.setRange(-20, 20)
        self.pen_down_input.setValue(0)
        self.pen_down_input.setSingleStep(0.5)
        self.pen_down_input.setSuffix(" mm")
        self.pen_down_input.valueChanged.connect(
            lambda v: setattr(self.gcode_generator, 'pen_down_z', v))
        feed_layout.addRow("Feed:", self.feed_input)
        feed_layout.addRow("Pen Up Z:", self.pen_up_input)
        feed_layout.addRow("Pen Down Z:", self.pen_down_input)
        feed_group.setLayout(feed_layout)
        layout.addWidget(feed_group)

        path_group = QGroupBox("cncjs / Folder")
        path_layout = QVBoxLayout()
        host_row = QHBoxLayout()
        self.cncjs_host_input = QLineEdit("127.0.0.1")
        self.cncjs_host_input.editingFinished.connect(self.sync_worker_settings)
        self.cncjs_port_input = QSpinBox()
        self.cncjs_port_input.setRange(1, 65535)
        self.cncjs_port_input.setValue(8000)
        self.cncjs_port_input.valueChanged.connect(self.sync_worker_settings)
        host_row.addWidget(QLabel("Host"))
        host_row.addWidget(self.cncjs_host_input)
        host_row.addWidget(QLabel("Port"))
        host_row.addWidget(self.cncjs_port_input)
        path_layout.addLayout(host_row)

        path_row = QHBoxLayout()
        self.path_input = QLineEdit()
        self.path_input.setPlaceholderText("Optional cncjs command path")
        self.path_input.editingFinished.connect(self.sync_worker_settings)
        browse_btn = QPushButton("Browse...")
        browse_btn.clicked.connect(self.browse_cncjs_command)
        auto_find_btn = QPushButton("Detect")
        auto_find_btn.clicked.connect(self.auto_find_cncjs)
        path_row.addWidget(self.path_input)
        path_row.addWidget(browse_btn)
        path_row.addWidget(auto_find_btn)
        path_layout.addLayout(path_row)

        token_row = QHBoxLayout()
        self.cncjs_token_input = QLineEdit()
        self.cncjs_token_input.setPlaceholderText("Optional cncjs access token")
        self.cncjs_token_input.editingFinished.connect(self.sync_worker_settings)
        token_row.addWidget(QLabel("Token"))
        token_row.addWidget(self.cncjs_token_input)
        path_layout.addLayout(token_row)

        login_row = QHBoxLayout()
        self.cncjs_user_input = QLineEdit()
        self.cncjs_user_input.setPlaceholderText("Username")
        self.cncjs_user_input.editingFinished.connect(self.sync_worker_settings)
        self.cncjs_password_input = QLineEdit()
        self.cncjs_password_input.setPlaceholderText("Password")
        self.cncjs_password_input.setEchoMode(QLineEdit.Password)
        self.cncjs_password_input.editingFinished.connect(self.sync_worker_settings)
        login_row.addWidget(QLabel("User"))
        login_row.addWidget(self.cncjs_user_input)
        login_row.addWidget(QLabel("Pass"))
        login_row.addWidget(self.cncjs_password_input)
        path_layout.addLayout(login_row)

        self.auto_launch_check = QCheckBox("Auto-start cncjs on app launch")
        self.auto_launch_check.setChecked(True)
        self.auto_launch_check.toggled.connect(self.sync_worker_settings)
        self.auto_browser_check = QCheckBox("Auto-open browser")
        self.auto_browser_check.setChecked(True)
        self.auto_browser_check.toggled.connect(self.sync_worker_settings)
        self.auto_connect_check = QCheckBox("Auto-connect first USB port")
        self.auto_connect_check.setChecked(True)
        path_layout.addWidget(self.auto_launch_check)
        path_layout.addWidget(self.auto_browser_check)
        path_layout.addWidget(self.auto_connect_check)

        launch_row = QHBoxLayout()
        self.launch_grbl_btn = QPushButton("Start cncjs")
        self.launch_grbl_btn.setStyleSheet("""
            QPushButton { background-color: #7c3aed; color: white;
                padding: 8px; font-weight: bold; border-radius: 4px; }
            QPushButton:hover { background-color: #6d28d9; }
        """)
        self.launch_grbl_btn.clicked.connect(
            lambda: self.auto_start_cncjs(force_open_browser=True))
        self.open_browser_btn = QPushButton("Open cncjs")
        self.open_browser_btn.clicked.connect(self.open_cncjs_browser)
        self.grbl_status_label = QLabel("⚫ cncjs not started")
        self.grbl_status_label.setWordWrap(True)
        self.grbl_status_label.setStyleSheet("color: #888; font-style: italic;")
        launch_row.addWidget(self.launch_grbl_btn)
        launch_row.addWidget(self.open_browser_btn)
        launch_row.addWidget(self.grbl_status_label)
        path_layout.addLayout(launch_row)

        folder_row = QHBoxLayout()
        folder_label = QLabel(f"Folder: {self.gcode_folder}")
        folder_label.setWordWrap(True)
        open_folder_btn = QPushButton("Open Folder")
        open_folder_btn.clicked.connect(self.open_gcode_folder)
        folder_row.addWidget(folder_label)
        folder_row.addWidget(open_folder_btn)
        path_layout.addLayout(folder_row)
        path_group.setLayout(path_layout)
        layout.addWidget(path_group)

        return widget

    def set_preset_size(self, w, h):
        self.plot_width_input.setValue(w)
        self.plot_height_input.setValue(h)

    def open_gcode_folder(self):
        if sys.platform == "win32":
            os.startfile(str(self.gcode_folder))
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(self.gcode_folder)])
        else:
            subprocess.Popen(["xdg-open", str(self.gcode_folder)])

    def update_plot_size(self):
        w = self.plot_width_input.value()
        h = self.plot_height_input.value()
        self.canvas.set_plot_size(w, h)
        self.gcode_generator.plot_width = w
        self.gcode_generator.plot_height = h
        self.gcode_generator.max_x = w
        self.gcode_generator.max_y = h
        self.plot_size_label.setText(f"{w} × {h} mm")
        QTimer.singleShot(100, self.canvas.fit_to_view)

    def browse_cncjs_command(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select cncjs executable", "", "All Files (*)")
        if path:
            self.path_input.setText(path)
            self.sync_worker_settings()

    def auto_find_cncjs(self):
        detected = self.serial_worker._resolve_cncjs_command()
        if detected:
            self.path_input.setText(detected)
            self.sync_worker_settings()
            self.status.showMessage(f"Found cncjs: {detected}")
            return
        QMessageBox.warning(self, "cncjs Not Found",
            "cncjs not found in PATH.\n\nInstall with:\nnpm install -g cncjs")

    def sync_worker_settings(self):
        self.serial_worker.configure_cncjs(
            host=self.cncjs_host_input.text().strip() or "127.0.0.1",
            port=self.cncjs_port_input.value(),
            controller_type="Grbl",
            token=self.cncjs_token_input.text(),
            username=self.cncjs_user_input.text(),
            password=self.cncjs_password_input.text(),
            command_path=self.path_input.text(),
            watch_directory=self.gcode_folder,
            auto_open_browser=self.auto_browser_check.isChecked(),
        )

    def auto_start_cncjs(self, force_open_browser=False):
        self.sync_worker_settings()
        if not self.auto_launch_check.isChecked() and not force_open_browser:
            return

        # Force port refresh first so combo is populated
        self.serial_panel.refresh_ports()

        preferred_port = self.serial_panel.port_combo.currentData() or ""
        # Fallback if combo is empty/header-selected
        if not preferred_port:
            preferred_port = self.serial_worker._auto_detect_port()

        try:
            baudrate = int(self.serial_panel.baud_combo.currentText())
        except ValueError:
            baudrate = 115200

        self.serial_worker.start_background(
            preferred_port=preferred_port,
            baudrate=baudrate,
            open_browser=force_open_browser or self.auto_browser_check.isChecked(),
            auto_connect_machine=self.auto_connect_check.isChecked(),
        )

    def open_cncjs_browser(self):
        self.sync_worker_settings()
        if self.serial_worker.start_cncjs_server(open_browser=True):
            self.serial_worker.open_browser()

    def create_toolbar(self):
        toolbar = QToolBar("Main")
        toolbar.setIconSize(QSize(20, 20) if IS_RPI else QSize(24, 24))
        toolbar.setMovable(False)
        toolbar.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.addToolBar(toolbar)

        new_action = QAction(std_icon(self, QStyle.SP_FileIcon), "New", self)
        new_action.setShortcut(QKeySequence.New)
        new_action.triggered.connect(self.new_canvas)
        toolbar.addAction(new_action)

        import_action = QAction(std_icon(self, QStyle.SP_DialogOpenButton), "Import", self)
        import_action.setShortcut("Ctrl+I")
        import_action.triggered.connect(self.import_file)
        toolbar.addAction(import_action)

        export_action = QAction(std_icon(self, QStyle.SP_DialogSaveButton), "Export", self)
        export_action.setShortcut("Ctrl+E")
        export_action.triggered.connect(self.export_image)
        toolbar.addAction(export_action)

        toolbar.addSeparator()

        save_action = QAction(std_icon(self, QStyle.SP_DriveHDIcon), "Save G-code", self)
        save_action.setShortcut("Ctrl+S")
        save_action.triggered.connect(self.save_gcode)
        toolbar.addAction(save_action)

        toolbar.addSeparator()

        self.draw_serial_action = QAction(
            std_icon(self, QStyle.SP_MediaPlay), "STREAM TO MACHINE", self)
        self.draw_serial_action.setShortcut("F5")
        self.draw_serial_action.triggered.connect(self.stream_to_machine)
        toolbar.addAction(self.draw_serial_action)
        draw_btn = toolbar.widgetForAction(self.draw_serial_action)
        if draw_btn:
            draw_btn.setStyleSheet("""
                QToolButton { background-color: #4CAF50; color: white;
                    font-weight: bold; padding: 6px 14px; border-radius: 4px; }
                QToolButton:hover { background-color: #45a049; }
            """)

        send_to_action = QAction(std_icon(self, QStyle.SP_FileDialogToParent),
                                "Save For cncjs", self)
        send_to_action.triggered.connect(self.send_to_cncjs)
        toolbar.addAction(send_to_action)

        toolbar.addSeparator()

        self.launch_grbl_action = QAction(
            std_icon(self, QStyle.SP_ComputerIcon), "Start cncjs", self)
        self.launch_grbl_action.triggered.connect(
            lambda: self.auto_start_cncjs(force_open_browser=True))
        toolbar.addAction(self.launch_grbl_action)
        launch_btn = toolbar.widgetForAction(self.launch_grbl_action)
        if launch_btn:
            launch_btn.setStyleSheet("""
                QToolButton { background-color: #7c3aed; color: white;
                    font-weight: bold; padding: 6px 12px; border-radius: 4px; }
                QToolButton:hover { background-color: #6d28d9; }
            """)

        toolbar.addSeparator()
        clear_action = QAction(std_icon(self, QStyle.SP_TrashIcon), "Clear", self)
        clear_action.triggered.connect(self.clear_canvas)
        toolbar.addAction(clear_action)

        toolbar.addSeparator()
        zoom_in = QAction(std_icon(self, QStyle.SP_ArrowUp), "Zoom +", self)
        zoom_in.triggered.connect(lambda: self.canvas.scale(1.2, 1.2))
        toolbar.addAction(zoom_in)

        zoom_out = QAction(std_icon(self, QStyle.SP_ArrowDown), "Zoom -", self)
        zoom_out.triggered.connect(lambda: self.canvas.scale(1/1.2, 1/1.2))
        toolbar.addAction(zoom_out)

        fit_action = QAction(std_icon(self, QStyle.SP_BrowserReload), "Fit", self)
        fit_action.triggered.connect(self.canvas.fit_to_view)
        toolbar.addAction(fit_action)

    def create_menus(self):
        menubar = self.menuBar()
        file_menu = menubar.addMenu("File")
        file_menu.addAction("New", self.new_canvas, QKeySequence.New)
        file_menu.addAction("Import File...", self.import_file, "Ctrl+I")
        file_menu.addAction("Export Image...", self.export_image, "Ctrl+E")
        file_menu.addSeparator()
        file_menu.addAction("Save G-code...", self.save_gcode, "Ctrl+S")
        file_menu.addAction("Open G-code Folder", self.open_gcode_folder)
        file_menu.addSeparator()
        file_menu.addAction("Stream to Machine", self.stream_to_machine, "F5")
        file_menu.addAction("Save For cncjs", self.send_to_cncjs)
        file_menu.addSeparator()
        file_menu.addAction("Exit", self.close, "Ctrl+Q")

        edit_menu = menubar.addMenu("Edit")
        edit_menu.addAction("Clear Canvas", self.clear_canvas)
        edit_menu.addAction("Delete Selected", self.delete_selected, "Delete")

        view_menu = menubar.addMenu("View")
        view_menu.addAction("Fit to View", self.canvas.fit_to_view, "Ctrl+0")
        view_menu.addAction("Zoom In", lambda: self.canvas.scale(1.2, 1.2), "Ctrl++")
        view_menu.addAction("Zoom Out", lambda: self.canvas.scale(1/1.2, 1/1.2), "Ctrl+-")

        machine_menu = menubar.addMenu("Machine")
        machine_menu.addAction("Home ($H)", lambda: self.serial_worker.home())
        machine_menu.addAction("Unlock ($X)", lambda: self.serial_worker.unlock_alarm())
        machine_menu.addAction("Emergency Stop", lambda: self.serial_worker.emergency_stop())
        machine_menu.addAction("Status", lambda: self.serial_worker.get_status())

        help_menu = menubar.addMenu("Help")
        help_menu.addAction("About", self.show_about)

    def delete_selected(self):
        for item in self.canvas.scene.selectedItems():
            if item.zValue() >= 0:
                self.canvas.scene.removeItem(item)
                self.canvas.strokes = [s for s in self.canvas.strokes
                                      if s.get('item') != item]

    def on_connection_change(self, connected, message):
        prefix = "✅" if connected else "⚫"
        self.status.showMessage(f"{prefix} {message}")

    def on_server_status_change(self, connected, message):
        prefix = "🟢" if connected else "⚫"
        color = "#4CAF50" if connected else "#888"
        self.grbl_status_label.setText(f"{prefix} {message}")
        self.grbl_status_label.setStyleSheet(f"color: {color}; font-weight: bold;")

    def new_canvas(self):
        reply = QMessageBox.question(self, "New Canvas",
            "Clear and start new?", QMessageBox.Yes | QMessageBox.No)
        if reply == QMessageBox.Yes:
            self.canvas.clear_canvas()

    def clear_canvas(self):
        reply = QMessageBox.question(self, "Clear",
            "Clear all drawings?", QMessageBox.Yes | QMessageBox.No)
        if reply == QMessageBox.Yes:
            self.canvas.clear_canvas()

    def import_file(self):
        filter_str = ";;".join([f"{name} ({ext})"
                               for name, ext in SUPPORTED_IMPORT_FORMATS.items()])
        path, _ = QFileDialog.getOpenFileName(
            self, "Import drawing", "", filter_str)
        if path:
            ext = Path(path).suffix.lower()
            allowed = {'.svg', '.dxf', '.png', '.jpg', '.jpeg', '.bmp',
                      '.gif', '.hpgl', '.plt', '.gcode', '.nc', '.ngc', '.csv'}
            if ext not in allowed:
                QMessageBox.warning(self, "Unsupported", f"{ext} not supported")
                return
            if self.canvas.import_image(path):
                self.status.showMessage(f"Imported: {os.path.basename(path)}")
            else:
                QMessageBox.warning(self, "Error",
                    f"Cannot load {ext}.\nConvert DXF/HPGL to SVG/G-code first.")

    def export_image(self):
        default = str(self.gcode_folder.parent / "drawing.png")
        path, _ = QFileDialog.getSaveFileName(
            self, "Export", default, "PNG (*.png);;JPEG (*.jpg);;BMP (*.bmp)")
        if path and self.canvas.export_image(path):
            self.status.showMessage(f"Exported: {path}")
            QMessageBox.information(self, "Success", f"Exported:\n{path}")

    def save_gcode(self):
        if not self.canvas.strokes:
            QMessageBox.warning(self, "Empty", "Draw something first!")
            return
        # Sync tool changer config before generating
        self._sync_tool_changer()
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        default_path = str(self.gcode_folder / f"drawing_{timestamp}.gcode")
        path, _ = QFileDialog.getSaveFileName(
            self, "Save G-code", default_path,
            "G-code (*.gcode *.nc *.ngc);;All Files (*)")
        if path:
            gcode = self.gcode_generator.generate(self.canvas.strokes)
            with open(path, 'w') as f:
                f.write(gcode)
            self.status.showMessage(f"Saved: {path}")
            QMessageBox.information(self, "Saved", f"G-code saved:\n{path}")

    def save_gcode_snapshot(self):
        self._sync_tool_changer()
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filepath = self.gcode_folder / f"drawing_{timestamp}.gcode"
        gcode = self.gcode_generator.generate(self.canvas.strokes)
        with open(filepath, 'w') as f:
            f.write(gcode)
        return filepath, gcode

    def stream_to_machine(self):
        if not self.canvas.strokes:
            QMessageBox.warning(self, "Empty", "Draw something first!")
            return
        self.sync_worker_settings()
        self._sync_tool_changer()

        # Check missing pen slots if auto changer enabled
        missing = self.gcode_generator.missing_pen_slots(self.canvas.strokes)
        if missing:
            names = ", ".join(self.gcode_generator._tool_change_comment(t) for t in missing)
            reply = QMessageBox.warning(
                self, "Missing Pen Slots",
                f"Auto pen changer is ENABLED but no positions saved for:\n\n{names}\n\n"
                "Continue anyway? (These pens will use manual M6 tool change.)",
                QMessageBox.Yes | QMessageBox.No)
            if reply != QMessageBox.Yes:
                return

        if not self.serial_worker.is_connected:
            preferred_port = self.serial_panel.port_combo.currentData() or ""
            try:
                baudrate = int(self.serial_panel.baud_combo.currentText())
            except ValueError:
                baudrate = 115200
            self.serial_worker.start_cncjs_server(open_browser=True)
            self.serial_worker.ensure_socket_connection()
            self.serial_worker.connect_serial(preferred_port, baudrate)

        if not self.serial_worker.is_connected:
            msg = QMessageBox(self)
            msg.setIcon(QMessageBox.Warning)
            msg.setWindowTitle("Not Connected")
            msg.setText("<h3>Machine not connected</h3>")
            msg.setInformativeText(
                "1. Make sure cncjs opened in browser<br>"
                "2. Select correct USB port in Connect tab<br>"
                "3. Click CONNECT MACHINE<br><br>"
                "You can still save the file to the cncjs watch folder.")
            send_btn = msg.addButton("Save For cncjs", QMessageBox.AcceptRole)
            msg.addButton("I Will Connect", QMessageBox.ActionRole)
            msg.addButton(QMessageBox.Cancel)
            msg.exec_()
            if msg.clickedButton() == send_btn:
                self.send_to_cncjs()
            return

        filepath, gcode = self.save_gcode_snapshot()
        colors = set()
        for s in self.canvas.strokes:
            if 'color' in s:
                c = s['color']
                colors.add((c.red(), c.green(), c.blue()))

        cfg = self.gcode_generator
        msg = QMessageBox(self)
        msg.setIcon(QMessageBox.Question)
        msg.setWindowTitle("Start Drawing?")
        msg.setText("<h3>Ready to stream G-code</h3>")
        info = (f"<b>Strokes:</b> {len(self.canvas.strokes)}<br>"
                f"<b>Colors:</b> {len(colors)}<br>"
                f"<b>Plot:</b> {self.canvas.plot_width_mm}×{self.canvas.plot_height_mm} mm<br>"
                f"<b>File:</b> {filepath.name}<br>"
                f"<b>Auto Pen Change:</b> "
                f"{'ENABLED ✓' if cfg.tool_change_enabled else 'disabled'}<br><br>"
                "<b>SAFETY CHECK:</b><br>"
                "• Machine homed?<br>"
                "• Path clear?<br>"
                "• Pen rack loaded with correct pens?<br>"
                "• Paper secured?<br>"
                "• Emergency stop accessible?")
        msg.setInformativeText(info)
        msg.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
        if msg.exec_() != QMessageBox.Yes:
            return

        if self.serial_worker.send_gcode(gcode, filepath.name):
            self.status.showMessage(f"Streaming... ({filepath.name})")
            QMessageBox.information(self, "Streaming",
                "Job sent to cncjs and started.\n\n"
                "Monitor progress in Console tab and cncjs browser.")
        else:
            QMessageBox.critical(self, "Error",
                "Stream failed — check cncjs server and machine port")

    def send_to_cncjs(self):
        if not self.canvas.strokes:
            QMessageBox.warning(self, "Empty", "Draw something!")
            return
        filepath, _ = self.save_gcode_snapshot()
        self.sync_worker_settings()
        self.serial_worker.start_cncjs_server(open_browser=True)
        self.serial_worker.open_browser()
        QMessageBox.information(self, "Saved For cncjs",
            f"G-code saved to:\n{filepath}\n\n"
            f"Watch folder:\n{self.gcode_folder}\n\n"
            "File should appear in cncjs browser.")
        self.status.showMessage(f"Saved for cncjs: {filepath.name}")

    def save_settings(self):
        settings_path = Path(os.path.dirname(os.path.abspath(__file__))) / 'settings.json'
        settings = {
            'cncjs_host': self.cncjs_host_input.text(),
            'cncjs_port': self.cncjs_port_input.value(),
            'cncjs_command': self.path_input.text(),
            'cncjs_token': self.cncjs_token_input.text(),
            'cncjs_username': self.cncjs_user_input.text(),
            'cncjs_password': self.cncjs_password_input.text(),
            'auto_launch_cncjs': self.auto_launch_check.isChecked(),
            'auto_open_browser': self.auto_browser_check.isChecked(),
            'auto_connect_machine': self.auto_connect_check.isChecked(),
            'machine_port': self.serial_panel.port_combo.currentData() or '',
            'baud_rate': self.serial_panel.baud_combo.currentText(),
            'plot_width': self.plot_width_input.value(),
            'plot_height': self.plot_height_input.value(),
            'feed_rate': self.feed_input.value(),
            'pen_up_z': self.pen_up_input.value(),
            'pen_down_z': self.pen_down_input.value(),
            'tool_changer': self.pen_changer_panel.get_tool_changer_config(),
        }
        try:
            with open(settings_path, 'w') as f:
                json.dump(settings, f, indent=2)
        except Exception:
            pass

    def load_settings(self):
        settings_path = Path(os.path.dirname(os.path.abspath(__file__))) / 'settings.json'
        try:
            with open(settings_path, 'r') as f:
                settings = json.load(f)
            self.cncjs_host_input.setText(settings.get('cncjs_host', '127.0.0.1'))
            self.cncjs_port_input.setValue(settings.get('cncjs_port', 8000))
            self.path_input.setText(settings.get('cncjs_command', ''))
            self.cncjs_token_input.setText(settings.get('cncjs_token', ''))
            self.cncjs_user_input.setText(settings.get('cncjs_username', ''))
            self.cncjs_password_input.setText(settings.get('cncjs_password', ''))
            self.auto_launch_check.setChecked(settings.get('auto_launch_cncjs', True))
            self.auto_browser_check.setChecked(settings.get('auto_open_browser', True))
            self.auto_connect_check.setChecked(settings.get('auto_connect_machine', True))
            saved_port = settings.get('machine_port', '')
            if saved_port:
                idx = self.serial_panel.port_combo.findData(saved_port)
                if idx >= 0:
                    self.serial_panel.port_combo.setCurrentIndex(idx)
            baud_rate = str(settings.get('baud_rate', '115200'))
            idx = self.serial_panel.baud_combo.findText(baud_rate)
            if idx >= 0:
                self.serial_panel.baud_combo.setCurrentIndex(idx)
            else:
                self.serial_panel.baud_combo.setCurrentText(baud_rate)
            self.plot_width_input.setValue(settings.get('plot_width', 200))
            self.plot_height_input.setValue(settings.get('plot_height', 150))
            self.feed_input.setValue(settings.get('feed_rate', 3000))
            self.pen_up_input.setValue(settings.get('pen_up_z', 5))
            self.pen_down_input.setValue(settings.get('pen_down_z', 0))
            if 'tool_changer' in settings:
                self.pen_changer_panel.load_from_settings(settings['tool_changer'])
            self._sync_tool_changer()
        except Exception:
            pass

    def show_about(self):
        QMessageBox.about(self, "About",
            f"<h2>{APP_NAME}</h2><p>v{APP_VERSION}</p>"
            "<p>Professional plotter with cncjs + auto pen changer</p>"
            "<p><b>Features:</b></p><ul>"
            "<li>Pi 4 optimized rendering</li>"
            "<li>cncjs Socket.IO streaming (FIXED)</li>"
            "<li>Automated pen changing with XYZ rack mapping</li>"
            "<li>Servo A gripper open/close</li>"
            "<li>Jog-and-save position</li>"
            "<li>Resume after tool change</li>"
            "<li>Standard cross-platform icons</li>"
            "</ul>")

    def closeEvent(self, event):
        if self.serial_worker.is_connected:
            reply = QMessageBox.question(self, "Exit",
                "Disconnect and exit?", QMessageBox.Yes | QMessageBox.No)
            if reply != QMessageBox.Yes:
                event.ignore()
                return
        self.serial_worker.shutdown()
        self.save_settings()
        event.accept()


# ============================================================
# STYLESHEET (Pi-friendly)
# ============================================================

_BASE_FONT_SIZE = "10px" if IS_RPI else "11px"
_BASE_PADDING = "4px" if IS_RPI else "6px"

INDUSTRIAL_STYLE = f"""
QMainWindow, QDialog {{ background-color: #1e1e2e; }}
QWidget {{ color: #cdd6f4; font-family: 'Segoe UI', 'Ubuntu', sans-serif; font-size: {_BASE_FONT_SIZE}; }}
QDockWidget {{ color: #cdd6f4; font-weight: bold; }}
QDockWidget::title {{
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #313244, stop:1 #181825);
    padding: 6px; border-bottom: 2px solid #89b4fa;
}}
QMenuBar {{ background: #313244; color: #cdd6f4; padding: 3px; }}
QMenuBar::item {{ padding: 5px 10px; }}
QMenuBar::item:selected {{ background: #45475a; border-radius: 3px; }}
QMenu {{ background-color: #313244; color: #cdd6f4; border: 1px solid #45475a; }}
QMenu::item {{ padding: 5px 22px; }}
QMenu::item:selected {{ background: #89b4fa; color: #1e1e2e; }}
QToolBar {{
    background: #313244; spacing: 4px; padding: 4px;
    border-bottom: 1px solid #45475a;
}}
QToolButton {{
    color: #cdd6f4; padding: {_BASE_PADDING} 10px; border-radius: 4px;
    background-color: #45475a;
}}
QToolButton:hover {{ background-color: #585b70; }}
QToolButton:pressed {{ background-color: #89b4fa; color: #1e1e2e; }}
QStatusBar {{ background: #181825; color: #a6adc8; border-top: 1px solid #45475a; }}
QGroupBox {{
    color: #89b4fa; font-weight: bold; border: 1px solid #45475a;
    border-radius: 5px; margin-top: 10px; padding-top: 10px; padding: 6px;
}}
QGroupBox::title {{
    subcontrol-origin: margin; left: 10px; padding: 0 5px;
    background-color: #1e1e2e;
}}
QLabel {{ color: #cdd6f4; }}
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QFontComboBox, QTextEdit {{
    background-color: #313244; color: #cdd6f4;
    padding: 4px; border: 1px solid #45475a; border-radius: 4px;
    selection-background-color: #89b4fa;
}}
QLineEdit:focus, QSpinBox:focus, QComboBox:focus, QFontComboBox:focus {{
    border: 2px solid #89b4fa;
}}
QComboBox::drop-down, QFontComboBox::drop-down {{ border: none; }}
QComboBox QAbstractItemView {{
    background-color: #313244; color: #cdd6f4;
    selection-background-color: #89b4fa;
}}
QCheckBox {{ color: #cdd6f4; spacing: 6px; }}
QCheckBox::indicator {{
    width: 14px; height: 14px; border: 1px solid #45475a;
    border-radius: 3px; background-color: #313244;
}}
QCheckBox::indicator:checked {{
    background-color: #a6e3a1; border: 1px solid #a6e3a1;
}}
QPushButton {{
    background-color: #45475a; color: #cdd6f4;
    padding: {_BASE_PADDING} 12px; border: 1px solid #585b70;
    border-radius: 4px; font-weight: 500;
}}
QPushButton:hover {{ background-color: #585b70; border: 1px solid #89b4fa; }}
QPushButton:pressed {{ background-color: #89b4fa; color: #1e1e2e; }}
QTabWidget::pane {{ border: 1px solid #45475a; background: #1e1e2e; }}
QTabBar::tab {{
    background: #313244; color: #cdd6f4; padding: 5px 12px;
    border: 1px solid #45475a; border-bottom: none;
    border-top-left-radius: 4px; border-top-right-radius: 4px;
}}
QTabBar::tab:selected {{ background: #89b4fa; color: #1e1e2e; font-weight: bold; }}
QTabBar::tab:hover:!selected {{ background: #45475a; }}
QScrollBar:vertical {{ background: #1e1e2e; width: 10px; border: none; }}
QScrollBar::handle:vertical {{
    background: #45475a; border-radius: 5px; min-height: 25px;
}}
QScrollBar::handle:vertical:hover {{ background: #585b70; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
QScrollBar:horizontal {{ background: #1e1e2e; height: 10px; border: none; }}
QScrollBar::handle:horizontal {{
    background: #45475a; border-radius: 5px; min-width: 25px;
}}
QSlider::groove:horizontal {{
    border: 1px solid #45475a; height: 5px;
    background: #313244; border-radius: 3px;
}}
QSlider::handle:horizontal {{
    background: #89b4fa; border: 1px solid #89b4fa;
    width: 14px; margin: -5px 0; border-radius: 7px;
}}
"""


def main():
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps)
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setStyleSheet(INDUSTRIAL_STYLE)
    app.setApplicationName(APP_NAME)
    if IS_RPI:
        print("🍓 Raspberry Pi detected — performance optimizations enabled")
    window = MainWindow()
    window.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()