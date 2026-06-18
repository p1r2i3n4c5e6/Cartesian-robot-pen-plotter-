"""Safety panel — emergency stop, pause/resume, alarms log.

Every action button gets a Qt standard icon, a clear visible label
and a tooltip explaining what GRBL command (or real-time byte) is
sent so the user is never staring at a cryptic button.
"""

from datetime import datetime

from PyQt5.QtCore import QSize
from PyQt5.QtWidgets import (
    QCheckBox, QFormLayout, QGroupBox, QHBoxLayout, QMessageBox,
    QPushButton, QStyle, QTextEdit, QVBoxLayout, QWidget,
)


class SafetyControlPanel(QWidget):
    def __init__(self, serial_worker, parent=None):
        super().__init__(parent)
        self.serial_worker = serial_worker
        self._build()
        self.serial_worker.alarm_triggered.connect(self.on_alarm)
        self.serial_worker.error_triggered.connect(self.on_error)
        # Disable every action that needs a live serial link until the
        # backend reports it's connected — clicking Pause / Resume /
        # Home / Unlock with no controller behind them just silently
        # ate user input before, which is exactly the kind of "button
        # bug" the user complained about.
        self.serial_worker.connection_status.connect(self._on_conn_state)
        self._on_conn_state(self.serial_worker.is_connected, "")

    def _build(self) -> None:
        layout = QVBoxLayout(self)

        self.estop_btn = QPushButton(" EMERGENCY STOP")
        self.estop_btn.setIcon(self.style().standardIcon(QStyle.SP_BrowserStop))
        self.estop_btn.setIconSize(QSize(28, 28))
        self.estop_btn.setMinimumHeight(60)
        self.estop_btn.setToolTip(
            "<b>Emergency Stop</b><br>"
            "Sends GRBL real-time soft-reset (<code>0x18</code>).<br>"
            "Halts motion immediately, clears the planner buffer, and "
            "puts the controller into ALARM state. Press <i>Unlock</i> "
            "and re-home before resuming work."
        )
        self.estop_btn.setStyleSheet(
            "QPushButton {"
            "  background-color: #d32f2f; color: white;"
            "  font-size: 16px; font-weight: bold;"
            "  border: 3px solid #b71c1c; border-radius: 8px;"
            "  text-align: center; padding: 6px;"
            "} "
            "QPushButton:hover { background-color: #b71c1c; color: #ffffff; }"
            "QPushButton:pressed { background-color: #8b0000; color: #ffffff; }"
        )
        self.estop_btn.clicked.connect(self.emergency_stop)
        layout.addWidget(self.estop_btn)

        ctrl_group = QGroupBox("Machine Control")
        ctrl_layout = QVBoxLayout()
        pause_row = QHBoxLayout()
        self.pause_btn = QPushButton(" Pause Job  (!)")
        self.pause_btn.setIcon(self.style().standardIcon(QStyle.SP_MediaPause))
        self.pause_btn.setIconSize(QSize(20, 20))
        self.pause_btn.setToolTip(
            "<b>Feed Hold</b><br>"
            "Sends real-time <code>!</code> to GRBL.<br>"
            "Decelerates current motion smoothly and pauses the job. "
            "Use <i>Resume</i> to continue from the same point."
        )
        self.pause_btn.setStyleSheet(
            "QPushButton { background-color: #ff9800; color: #1e1e2e;"
            "  padding: 7px; font-weight: bold; border-radius: 4px;"
            "  text-align: left; }"
            "QPushButton:hover { background-color: #fb8c00; color: #1e1e2e; }"
        )
        self.pause_btn.clicked.connect(self.serial_worker.pause)

        self.resume_btn = QPushButton(" Resume Job  (~)")
        self.resume_btn.setIcon(self.style().standardIcon(QStyle.SP_MediaPlay))
        self.resume_btn.setIconSize(QSize(20, 20))
        self.resume_btn.setToolTip(
            "<b>Cycle Start / Resume</b><br>"
            "Sends real-time <code>~</code> to GRBL.<br>"
            "Resumes a job that was paused with Feed Hold."
        )
        self.resume_btn.setStyleSheet(
            "QPushButton { background-color: #4CAF50; color: #1e1e2e;"
            "  padding: 7px; font-weight: bold; border-radius: 4px;"
            "  text-align: left; }"
            "QPushButton:hover { background-color: #43a047; color: #1e1e2e; }"
        )
        self.resume_btn.clicked.connect(self.serial_worker.resume)
        pause_row.addWidget(self.pause_btn)
        pause_row.addWidget(self.resume_btn)
        ctrl_layout.addLayout(pause_row)

        home_row = QHBoxLayout()
        self.home_btn = QPushButton(" Home All Axes  ($H)")
        self.home_btn.setIcon(self.style().standardIcon(QStyle.SP_DirHomeIcon))
        self.home_btn.setIconSize(QSize(20, 20))
        self.home_btn.setToolTip(
            "<b>Run Homing Cycle</b><br>"
            "Sends <code>$H</code>.<br>"
            "Moves every enabled axis to its limit switch, then backs "
            "off by the pull-off distance to establish machine zero. "
            "Make sure nothing is in the path before clicking!"
        )
        self.home_btn.setStyleSheet(
            "QPushButton { background-color: #2196F3; color: white;"
            "  padding: 7px; font-weight: bold; border-radius: 4px;"
            "  text-align: left; }"
            "QPushButton:hover { background-color: #1976D2; color: white; }"
        )
        self.home_btn.clicked.connect(self.home_machine)

        self.unlock_btn = QPushButton(" Unlock Alarm  ($X)")
        self.unlock_btn.setIcon(
            self.style().standardIcon(QStyle.SP_DialogResetButton)
        )
        self.unlock_btn.setIconSize(QSize(20, 20))
        self.unlock_btn.setToolTip(
            "<b>Clear Alarm</b><br>"
            "Sends <code>$X</code>.<br>"
            "Use after an emergency stop, limit-switch hit or "
            "soft-limit violation. Machine coordinates are no longer "
            "trusted — re-home before resuming a job."
        )
        self.unlock_btn.setStyleSheet(
            "QPushButton { background-color: #9c27b0; color: white;"
            "  padding: 7px; font-weight: bold; border-radius: 4px;"
            "  text-align: left; }"
            "QPushButton:hover { background-color: #7b1fa2; color: white; }"
        )
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

        apply_btn = QPushButton("Apply Safety Settings")
        apply_btn.clicked.connect(self.apply_safety_settings)
        safety_layout.addRow(apply_btn)
        safety_group.setLayout(safety_layout)
        layout.addWidget(safety_group)

        alarm_group = QGroupBox("Alarm & Error Log")
        alarm_layout = QVBoxLayout()
        self.alarm_log = QTextEdit()
        self.alarm_log.setReadOnly(True)
        self.alarm_log.setMaximumHeight(110)
        self.alarm_log.setStyleSheet(
            "QTextEdit { background-color: #181825; color: #ff8888;"
            "  font-family: 'Courier New', monospace; font-size: 11px;"
            "  border: 1px solid #45475a; border-radius: 3px; }"
        )
        alarm_layout.addWidget(self.alarm_log)
        clear_btn = QPushButton("Clear Log")
        clear_btn.clicked.connect(self.alarm_log.clear)
        alarm_layout.addWidget(clear_btn)
        alarm_group.setLayout(alarm_layout)
        layout.addWidget(alarm_group)
        layout.addStretch()

    # ------------------------------------------------------------------
    def emergency_stop(self) -> None:
        self.serial_worker.emergency_stop()
        self.alarm_log.append(
            f"[{datetime.now().strftime('%H:%M:%S')}] EMERGENCY STOP"
        )
        QMessageBox.critical(
            self, "Emergency Stop",
            "Emergency stop sent.\n\n"
            "Machine has been halted.\n"
            "Use UNLOCK after checking everything is safe.",
        )

    def home_machine(self) -> None:
        reply = QMessageBox.question(
            self, "Home Machine",
            "Start homing cycle?\n\nMake sure path is clear!",
            QMessageBox.Yes | QMessageBox.No,
        )
        if reply == QMessageBox.Yes:
            self.serial_worker.home()
            self.alarm_log.append(
                f"[{datetime.now().strftime('%H:%M:%S')}] Homing"
            )

    def apply_safety_settings(self) -> None:
        if not self.serial_worker.is_connected:
            QMessageBox.warning(self, "Not Connected",
                                "Connect to the machine first.")
            return
        for cmd in (
            f"$20={'1' if self.enable_soft_limits.isChecked() else '0'}",
            f"$21={'1' if self.enable_hard_limits.isChecked() else '0'}",
            f"$22={'1' if self.enable_homing.isChecked() else '0'}",
        ):
            self.serial_worker.send_raw(cmd)
        QMessageBox.information(self, "Applied", "Safety settings sent.")

    def on_alarm(self, message: str) -> None:
        ts = datetime.now().strftime("%H:%M:%S")
        self.alarm_log.append(f"[{ts}] ALARM: {message}")
        msg = QMessageBox(self)
        msg.setIcon(QMessageBox.Critical)
        msg.setWindowTitle("GRBL ALARM")
        msg.setText("<h3 style='color:#d32f2f'>ALARM TRIGGERED</h3>")
        msg.setInformativeText(
            f"<b>{message}</b><br><br>"
            "Machine stopped for safety.<br><br>"
            "1. Check the machine physically<br>"
            "2. Clear obstructions<br>"
            "3. Click UNLOCK to clear<br>"
            "4. Re-home if necessary"
        )
        msg.exec_()

    def on_error(self, message: str) -> None:
        ts = datetime.now().strftime("%H:%M:%S")
        self.alarm_log.append(f"[{ts}] ERROR: {message}")

    # ------------------------------------------------------------------
    def _on_conn_state(self, connected: bool, _msg: str = "") -> None:
        """Enable / disable every action button based on link state.

        ESTOP stays clickable because it's the user's panic button —
        even if our local idea of "is connected" is stale, we still
        want to fire the soft-reset bytes at whatever serial port we
        opened most recently.
        """
        for btn in (self.pause_btn, self.resume_btn,
                    self.home_btn, self.unlock_btn):
            btn.setEnabled(bool(connected))
        # Visual hint: grey-out the disabled labels so the user can see
        # at a glance that they need to connect first.
        if connected:
            for btn in (self.pause_btn, self.resume_btn,
                        self.home_btn, self.unlock_btn):
                btn.setToolTip(btn.toolTip().replace(
                    "<br><i>(connect first)</i>", ""
                ))
        else:
            for btn in (self.pause_btn, self.resume_btn,
                        self.home_btn, self.unlock_btn):
                tt = btn.toolTip()
                if "<i>(connect first)</i>" not in tt:
                    btn.setToolTip(tt + "<br><i>(connect first)</i>")
