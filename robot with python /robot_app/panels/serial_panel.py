"""Connect tab — choose backend mode (Direct / cncjs), pick port, baud."""

from __future__ import annotations

from PyQt5.QtCore import QSize, QTimer
from PyQt5.QtWidgets import (
    QComboBox, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit,
    QMessageBox, QPushButton, QSpinBox, QStyle, QVBoxLayout, QWidget,
)

from ..config import COMMON_BAUD_RATES
from ..serial_worker import (
    SERIAL_AVAILABLE, SOCKETIO_AVAILABLE, list_serial_ports,
)


class SerialPanel(QWidget):
    """Connection panel with explicit Direct / cncjs mode toggle."""

    def __init__(self, serial_worker, parent=None):
        super().__init__(parent)
        self.serial_worker = serial_worker
        self._build()
        self.refresh_ports()
        self._refresh_timer = QTimer(self)
        self._refresh_timer.timeout.connect(self.refresh_ports)
        self._refresh_timer.start(3000)

        self.serial_worker.connection_status.connect(self.update_status)
        self.serial_worker.server_status_changed.connect(self.update_server_status)
        self.serial_worker.mode_changed.connect(self._sync_mode_combo)

    # ------------------------------------------------------------------
    def _build(self) -> None:
        layout = QVBoxLayout(self)

        # ----- Mode -----
        mode_group = QGroupBox("Connection Mode")
        mode_lay = QFormLayout()
        self.mode_combo = QComboBox()
        self.mode_combo.addItem("Direct serial (recommended)", "direct")
        self.mode_combo.addItem("Through cncjs (Socket.IO)", "cncjs")
        self.mode_combo.currentIndexChanged.connect(self._on_mode_changed)
        mode_lay.addRow("Mode:", self.mode_combo)

        self.mode_hint = QLabel(
            "Direct mode talks to the controller via pyserial — works "
            "without cncjs. Switch to cncjs if you also want the browser "
            "UI; remember the same USB port can only be open in one app."
        )
        self.mode_hint.setWordWrap(True)
        self.mode_hint.setStyleSheet(
            "background: transparent; color:#a6adc8;"
            "font-style: italic; font-size: 10px;"
        )
        mode_lay.addRow(self.mode_hint)

        if not SERIAL_AVAILABLE:
            warn = QLabel(
                "⚠ pyserial is not installed.\n"
                "Run: pip install pyserial"
            )
            warn.setStyleSheet("color:#f38ba8; background: transparent;")
            mode_lay.addRow(warn)
        if not SOCKETIO_AVAILABLE:
            warn = QLabel(
                "ℹ python-socketio is missing — cncjs mode will be disabled.\n"
                "Run: pip install 'python-socketio[client]'"
            )
            warn.setStyleSheet("color:#f9e2af; background: transparent;")
            mode_lay.addRow(warn)

        mode_group.setLayout(mode_lay)
        layout.addWidget(mode_group)

        # ----- Port -----
        port_group = QGroupBox("Machine Port (USB / ACM / COM)")
        port_lay = QFormLayout()
        port_row = QHBoxLayout()
        self.port_combo = QComboBox()
        self.port_combo.setMinimumWidth(200)
        self.port_combo.setToolTip(
            "<b>USB serial port</b><br>"
            "Pick the device the controller is plugged into.<br>"
            "Persistent <code>/dev/serial/by-id/...</code> entries are "
            "preferred when available because they survive USB "
            "re-numbering."
        )
        refresh_btn = QPushButton()
        refresh_btn.setIcon(
            self.style().standardIcon(QStyle.SP_BrowserReload)
        )
        refresh_btn.setIconSize(QSize(18, 18))
        refresh_btn.setMaximumWidth(36)
        refresh_btn.setToolTip(
            "<b>Refresh Port List</b><br>"
            "Re-scans the system for USB / ACM / COM serial devices.<br>"
            "Click after plugging in or unplugging a controller."
        )
        refresh_btn.clicked.connect(self.refresh_ports)
        port_row.addWidget(self.port_combo)
        port_row.addWidget(refresh_btn)
        port_lay.addRow("Port:", port_row)

        self.baud_combo = QComboBox()
        for baud in COMMON_BAUD_RATES:
            self.baud_combo.addItem(str(baud))
        self.baud_combo.setCurrentText("115200")
        self.baud_combo.setEditable(True)
        port_lay.addRow("Baud:", self.baud_combo)

        self.connect_btn = QPushButton("CONNECT MACHINE")
        self._style_connect_button(False)
        self.connect_btn.clicked.connect(self.toggle_connection)
        port_lay.addRow(self.connect_btn)

        self.status_label = QLabel("⚫ Machine disconnected")
        self.status_label.setWordWrap(True)
        self.status_label.setStyleSheet(
            "color:#ff8888; background: transparent; font-weight: bold;"
        )
        port_lay.addRow("Status:", self.status_label)
        port_group.setLayout(port_lay)
        layout.addWidget(port_group)

        # ----- cncjs server -----
        srv_group = QGroupBox("cncjs Server (only used in cncjs mode)")
        srv_lay = QFormLayout()
        self.tcp_host = QLineEdit("127.0.0.1")
        self.tcp_port = QSpinBox()
        self.tcp_port.setRange(1, 65535)
        self.tcp_port.setValue(8000)
        self.server_status_label = QLabel("⚫ cncjs not started")
        self.server_status_label.setWordWrap(True)
        self.server_status_label.setStyleSheet(
            "color:#888; background: transparent; font-style: italic;"
        )
        srv_lay.addRow("Host:", self.tcp_host)
        srv_lay.addRow("Port:", self.tcp_port)
        srv_lay.addRow("Server:", self.server_status_label)
        srv_group.setLayout(srv_lay)
        layout.addWidget(srv_group)
        layout.addStretch()

    # ------------------------------------------------------------------
    def _style_connect_button(self, connected: bool) -> None:
        if connected:
            self.connect_btn.setText(" DISCONNECT")
            self.connect_btn.setIcon(
                self.style().standardIcon(QStyle.SP_DialogCloseButton)
            )
            self.connect_btn.setIconSize(QSize(20, 20))
            self.connect_btn.setToolTip(
                "<b>Disconnect</b><br>"
                "Close the serial port (or the cncjs Socket.IO link) "
                "and release the device so other apps can use it."
            )
            self.connect_btn.setStyleSheet(
                "QPushButton { background-color: #f44336; color: white;"
                "  padding: 9px; font-weight: bold; border-radius: 4px;"
                "  border: 1px solid #c62828; text-align: center; }"
                "QPushButton:hover { background-color: #d32f2f; color: white; }"
            )
        else:
            self.connect_btn.setText(" CONNECT MACHINE")
            self.connect_btn.setIcon(
                self.style().standardIcon(QStyle.SP_ComputerIcon)
            )
            self.connect_btn.setIconSize(QSize(20, 20))
            self.connect_btn.setToolTip(
                "<b>Connect to Controller</b><br>"
                "Opens the selected USB port at the chosen baud rate.<br>"
                "In <i>Direct</i> mode talks to GRBL via pyserial.<br>"
                "In <i>cncjs</i> mode tells cncjs to open the port for you."
            )
            self.connect_btn.setStyleSheet(
                "QPushButton { background-color: #2196F3; color: white;"
                "  padding: 9px; font-weight: bold; border-radius: 4px;"
                "  border: 1px solid #1565C0; text-align: center; }"
                "QPushButton:hover { background-color: #1976D2; color: white; }"
            )

    # ------------------------------------------------------------------
    def refresh_ports(self) -> None:
        if not SERIAL_AVAILABLE:
            self.port_combo.clear()
            self.port_combo.addItem("pyserial not installed", None)
            return
        current = self.port_combo.currentData()
        self.port_combo.clear()
        ports = list_serial_ports()
        if not ports:
            self.port_combo.addItem("No USB devices detected", None)
            return
        usb_seen = False
        for entry in ports:
            device = entry["device"]
            label = entry["label"]
            self.port_combo.addItem(f"  {label}", device)
            usb_seen = usb_seen or device.startswith("/dev/tty")
        if current:
            idx = self.port_combo.findData(current)
            if idx >= 0:
                self.port_combo.setCurrentIndex(idx)
                return
        if usb_seen:
            self.port_combo.setCurrentIndex(0)

    # ------------------------------------------------------------------
    def selected_port(self) -> str:
        return self.port_combo.currentData() or ""

    def selected_baudrate(self) -> int:
        try:
            return int(self.baud_combo.currentText())
        except ValueError:
            return 115200

    def select_port(self, port: str) -> bool:
        idx = self.port_combo.findData(port)
        if idx >= 0:
            self.port_combo.setCurrentIndex(idx)
            return True
        return False

    def select_baudrate(self, baud) -> None:
        text = str(baud)
        idx = self.baud_combo.findText(text)
        if idx >= 0:
            self.baud_combo.setCurrentIndex(idx)
        else:
            self.baud_combo.setCurrentText(text)

    def select_mode(self, mode: str) -> None:
        idx = self.mode_combo.findData(mode)
        if idx >= 0:
            self.mode_combo.blockSignals(True)
            self.mode_combo.setCurrentIndex(idx)
            self.mode_combo.blockSignals(False)

    # ------------------------------------------------------------------
    def _on_mode_changed(self, _ignored=None) -> None:
        mode = self.mode_combo.currentData() or "direct"
        if mode == "cncjs" and not SOCKETIO_AVAILABLE:
            QMessageBox.warning(
                self, "python-socketio missing",
                "cncjs mode needs python-socketio.\n\n"
                "Install with:\n  pip install 'python-socketio[client]'\n\n"
                "Falling back to Direct mode."
            )
            self.select_mode("direct")
            mode = "direct"
        self.serial_worker.set_mode(mode)

    def _sync_mode_combo(self, mode: str) -> None:
        self.select_mode(mode)

    # ------------------------------------------------------------------
    def toggle_connection(self) -> None:
        if self.serial_worker.is_connected:
            self.serial_worker.disconnect_serial()
            return
        port = self.selected_port()
        if not port:
            QMessageBox.warning(self, "Invalid Port",
                                "Please select a valid serial port.")
            return
        self.serial_worker.connect_serial(port, self.selected_baudrate())

    # ------------------------------------------------------------------
    def update_status(self, connected: bool, message: str) -> None:
        if connected:
            self.status_label.setText(f"🟢 {message}")
            self.status_label.setStyleSheet(
                "color:#4CAF50; background: transparent; font-weight: bold;"
            )
        else:
            self.status_label.setText(f"⚫ {message}")
            self.status_label.setStyleSheet(
                "color:#ff8888; background: transparent; font-weight: bold;"
            )
        self._style_connect_button(connected)

    def update_server_status(self, running: bool, message: str) -> None:
        prefix = "🟢" if running else "⚫"
        color = "#4CAF50" if running else "#888"
        self.server_status_label.setText(f"{prefix} {message}")
        self.server_status_label.setStyleSheet(
            f"color: {color}; background: transparent; font-weight: bold;"
        )
        self.tcp_host.setText(self.serial_worker.cncjs_host)
        self.tcp_port.setValue(self.serial_worker.cncjs_port)
