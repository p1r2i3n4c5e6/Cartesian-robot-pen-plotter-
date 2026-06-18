"""Steps-per-mm calibration wizard for industrial-grade motion accuracy.

Solves the classic "I commanded 50 mm and the head moved 75 mm" problem.

Two calibration modes are supported:

────────────────────────────────────────────────────────────────────
1. SOFTWARE  (default — recommended for FluidNC users)
────────────────────────────────────────────────────────────────────
   Stores a per-axis multiplier (``x_scale``, ``y_scale``, ``z_scale``)
   in ``settings.json``.  Every X / Y / Z token in every G-code stream
   is pre-multiplied by this scale before it leaves the app, so the
   firmware steps/mm values are never touched.

   Why this is the default:
     * Survives FluidNC YAML resets on every boot.
     * Survives firmware re-flashes.
     * No EEPROM writes — works even when the controller is read-only.
     * Easy to back up (a settings file).

────────────────────────────────────────────────────────────────────
2. FIRMWARE  (advanced — vanilla GRBL builds)
────────────────────────────────────────────────────────────────────
   Sends ``$<reg>=<value>`` to the controller, which writes the new
   steps/mm into NVS / EEPROM.  Useful for bare-metal GRBL where you
   want every host (this app, cncjs, bCNC, …) to see the corrected
   value without each maintaining its own scale.

The math is identical in both modes::

    new_value = old_value × (commanded ÷ measured)

The only difference is *what* gets multiplied — the software scale,
or the firmware register.
"""

from __future__ import annotations

import logging
import re
from typing import Dict, Optional

from PyQt5.QtCore import Qt, QTimer, pyqtSignal
from PyQt5.QtWidgets import (
    QButtonGroup, QDoubleSpinBox, QFrame, QGridLayout, QGroupBox,
    QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QRadioButton,
    QVBoxLayout, QWidget,
)

log = logging.getLogger(__name__)


# GRBL register IDs for steps-per-mm per axis.
AXIS_REGS = {"X": 100, "Y": 101, "Z": 102}
# Safe per-axis feed-rates for the calibration JOG button (mm/min).
AXIS_FEEDS = {"X": 1500, "Y": 1500, "Z": 600}
# Index into the (x, y, z) tuple returned by gcode_generator.calibration_scales
AXIS_INDEX = {"X": 0, "Y": 1, "Z": 2}


class _AxisCalibrator(QFrame):
    """One row of the calibration UI — handles a single axis end-to-end."""

    request_send = pyqtSignal(str)               # raw line of G-code / GRBL
    request_software_apply = pyqtSignal(str, float, float, float)
    log_message = pyqtSignal(str)
    # axis, commanded, measured, new_scale  ↑

    def __init__(self, axis: str, parent=None):
        super().__init__(parent)
        self.axis = axis
        self.reg_id = AXIS_REGS[axis]
        self.firmware_steps: Optional[float] = None     # current $100/$101/$102
        self.software_scale: float = 1.0                 # current sw multiplier
        self._connected = False
        self._mode = "software"                          # set by parent
        self._pending_new_value: Optional[float] = None  # last math result
        self._pending_new_scale: Optional[float] = None

        self.setFrameShape(QFrame.StyledPanel)
        self.setStyleSheet(
            "QFrame { background:#1e1e2e; border:1px solid #45475a;"
            "         border-radius: 6px; padding: 6px; }"
        )

        title = QLabel(
            f"<b style='color:#89b4fa;font-size:13pt'>"
            f"  {axis}-axis  </b>"
            f"<span style='color:#a6adc8'>"
            f"(GRBL ${self.reg_id})</span>"
        )
        title.setTextFormat(Qt.RichText)

        # ── current values: software scale + firmware register ─────────
        self.sw_lbl = QLabel(self._sw_label_text())
        self.sw_lbl.setTextFormat(Qt.RichText)
        self.sw_lbl.setStyleSheet(
            "color:#a6e3a1;font-family:'Courier New';font-weight:bold;"
        )
        self.fw_lbl = QLabel(self._fw_label_text())
        self.fw_lbl.setTextFormat(Qt.RichText)
        self.fw_lbl.setStyleSheet(
            "color:#fab387;font-family:'Courier New';font-weight:bold;"
        )

        # ── commanded distance + JOG ────────────────────────────────────
        self.commanded_spin = QDoubleSpinBox()
        self.commanded_spin.setRange(1.0, 300.0)
        self.commanded_spin.setSingleStep(5.0)
        self.commanded_spin.setSuffix(" mm")
        self.commanded_spin.setValue(50.0)
        self.commanded_spin.setDecimals(2)
        self.commanded_spin.setToolTip(
            "Distance the controller will be told to move.<br>"
            "<b>Pick a value you can measure precisely</b> with the "
            "ruler you have on hand — 50&nbsp;mm or 100&nbsp;mm is "
            "ideal.  Bigger moves give better accuracy."
        )

        self.jog_btn = QPushButton(f"⮕ JOG {axis} →")
        self.jog_btn.setStyleSheet(
            "QPushButton { background:#74c7ec; color:#1e1e2e; font-weight:bold;"
            "              padding:6px 12px; border-radius:4px; }"
            "QPushButton:hover { background:#89dceb; }"
            "QPushButton:disabled { background:#45475a; color:#7c7e95; }"
        )
        self.jog_btn.setEnabled(False)
        self.jog_btn.setToolTip(
            "Send a <b>relative</b> jog on this axis.<br>"
            "When <b>software</b> calibration is active, the move "
            "is pre-scaled — so after APPLY a 50&nbsp;mm jog will "
            "physically move 50&nbsp;mm and you can verify the fix."
        )
        self.jog_btn.clicked.connect(self._on_jog)

        # ── measured distance + APPLY ───────────────────────────────────
        self.measured_spin = QDoubleSpinBox()
        self.measured_spin.setRange(0.01, 600.0)
        self.measured_spin.setSingleStep(0.1)
        self.measured_spin.setSuffix(" mm")
        self.measured_spin.setValue(50.0)
        self.measured_spin.setDecimals(3)
        self.measured_spin.setToolTip(
            "Type in the <b>actual</b> distance the head moved, "
            "measured with a ruler or calliper."
        )
        self.measured_spin.valueChanged.connect(self._update_preview)
        self.commanded_spin.valueChanged.connect(self._update_preview)

        self.preview_lbl = QLabel("New: <i>—</i>")
        self.preview_lbl.setTextFormat(Qt.RichText)
        self.preview_lbl.setStyleSheet(
            "color:#a6e3a1;font-family:'Courier New';font-weight:bold;"
        )

        self.apply_btn = QPushButton("✓ APPLY")
        self.apply_btn.setStyleSheet(
            "QPushButton { background:#a6e3a1; color:#1e1e2e; font-weight:bold;"
            "              padding:6px 12px; border-radius:4px; }"
            "QPushButton:hover { background:#94e2d5; }"
            "QPushButton:disabled { background:#45475a; color:#7c7e95; }"
        )
        self.apply_btn.clicked.connect(self._on_apply)
        self.apply_btn.setToolTip(
            "Calculate the corrected steps/mm and either save it to "
            "the software (default) or write it to the controller's "
            "EEPROM (firmware mode)."
        )

        # ── reset to 1.0 (software) ─────────────────────────────────────
        self.reset_btn = QPushButton("⟲")
        self.reset_btn.setFixedWidth(28)
        self.reset_btn.setToolTip(
            f"Reset {axis} software scale to 1.0 (no compensation)."
        )
        self.reset_btn.setStyleSheet(
            "QPushButton { background:#7f849c; color:#1e1e2e;"
            "              font-weight:bold; border-radius:4px; }"
            "QPushButton:hover { background:#9399b2; }"
        )
        self.reset_btn.clicked.connect(self._on_reset)

        # ── layout ──────────────────────────────────────────────────────
        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(4)
        grid.addWidget(title,                     0, 0, 1, 5)
        grid.addWidget(self.sw_lbl,               1, 0, 1, 4)
        grid.addWidget(self.reset_btn,            1, 4)
        grid.addWidget(self.fw_lbl,               2, 0, 1, 5)
        grid.addWidget(QLabel("Commanded:"),      3, 0)
        grid.addWidget(self.commanded_spin,       3, 1)
        grid.addWidget(self.jog_btn,              3, 2, 1, 3)
        grid.addWidget(QLabel("Measured:"),       4, 0)
        grid.addWidget(self.measured_spin,        4, 1)
        grid.addWidget(self.preview_lbl,          4, 2, 1, 2)
        grid.addWidget(self.apply_btn,            4, 4)
        self.setLayout(grid)
        self._refresh_apply_state()

    # ----------------------------------------------------------------------
    # Label builders
    # ----------------------------------------------------------------------
    def _sw_label_text(self) -> str:
        if self.software_scale == 1.0:
            return (
                "<b>Software scale:</b> "
                "<span style='color:#a6adc8'>1.000 (no compensation)</span>"
            )
        col = "#a6e3a1" if 0.95 <= self.software_scale <= 1.05 else "#f9e2af"
        return (
            f"<b>Software scale:</b> "
            f"<span style='color:{col}'>×{self.software_scale:.4f}</span>"
        )

    def _fw_label_text(self) -> str:
        if self.firmware_steps is None:
            return (
                f"<b>Firmware ${self.reg_id}:</b> "
                f"<i style='color:#a6adc8'>unknown — click READ</i>"
            )
        return (
            f"<b>Firmware ${self.reg_id}:</b> "
            f"<span style='color:#fab387'>{self.firmware_steps:.3f}</span> "
            f"<span style='color:#a6adc8'>steps/mm</span>"
        )

    # ----------------------------------------------------------------------
    # Public API used by the parent panel
    # ----------------------------------------------------------------------
    def set_connected(self, connected: bool) -> None:
        self._connected = bool(connected)
        self.jog_btn.setEnabled(self._connected)
        self._refresh_apply_state()

    def set_mode(self, mode: str) -> None:
        """``'software'`` or ``'firmware'``."""
        self._mode = mode if mode in ("software", "firmware") else "software"
        if self._mode == "software":
            self.apply_btn.setText("✓ APPLY (software)")
        else:
            self.apply_btn.setText("✓ APPLY (firmware $)")
        self._update_preview()
        self._refresh_apply_state()

    def update_firmware_steps(self, value: float) -> None:
        self.firmware_steps = float(value)
        self.fw_lbl.setText(self._fw_label_text())
        self._update_preview()
        self._refresh_apply_state()

    def update_software_scale(self, scale: float) -> None:
        self.software_scale = float(scale)
        self.sw_lbl.setText(self._sw_label_text())
        self._update_preview()

    def restore_last_input(self, commanded: float, measured: float) -> None:
        """Re-populate the spin-boxes with the last-saved attempt."""
        try:
            self.commanded_spin.setValue(float(commanded))
            self.measured_spin.setValue(float(measured))
        except (TypeError, ValueError):
            pass

    # ----------------------------------------------------------------------
    # Internal slots
    # ----------------------------------------------------------------------
    def _refresh_apply_state(self) -> None:
        if self._mode == "software":
            # Software apply needs no connection.
            self.apply_btn.setEnabled(True)
        else:
            self.apply_btn.setEnabled(
                self._connected and self.firmware_steps is not None
            )

    def _on_jog(self) -> None:
        """Send a relative jog so the user can measure the result.

        When software calibration is active we PRE-SCALE the commanded
        distance.  This means after a successful APPLY the next click
        of JOG will physically move the requested distance — letting
        the user immediately verify the fix without having to draw a
        full job.
        """
        cmd = self.commanded_spin.value()
        scale = self.software_scale
        # Pre-scale BOTH distance and feed by our current software
        # multiplier so the firmware physically moves ``cmd`` mm at
        # ``feed_user`` mm/min (assuming the scale is correct).  Without
        # the feed scale, the post-calibration jog lands in the right
        # place but the head moves at the firmware's mis-scaled
        # mm/min — that's the user's "robot speed never changes after
        # calibration" complaint.
        sent_dist = cmd * scale
        feed_user = AXIS_FEEDS[self.axis]
        sent_feed = max(1, int(round(feed_user * scale)))
        self.request_send.emit("G91")
        self.request_send.emit(
            f"G0 {self.axis}{sent_dist:.3f} F{sent_feed}"
        )
        self.request_send.emit("G90")
        # Default the measured field to the requested commanded value
        # so the user only has to overwrite when there's a discrepancy.
        self.measured_spin.setValue(cmd)
        # Multi-line, unambiguous log message.   Past users have looked
        # at the post-scale value (e.g. "1.000") and concluded "the
        # software is sending only 1 mm" \u2014 not realising it is the
        # firmware-side compensation for a wrong steps/mm.   Spell it
        # all out so the cmd vs sent vs physical distinction is
        # impossible to misread.
        if abs(scale - 1.0) < 1e-6:
            cal_note = (
                "(no calibration applied \u2014 firmware moves the raw "
                "value at the raw feed rate)"
            )
            speed_line = (
                f"   Speed:  user F{feed_user} mm/min  =  "
                f"firmware F{sent_feed} (1\u00d7 \u2014 no scale)"
            )
        else:
            cal_note = (f"(software calibration {self.axis}\u00d7{scale:.4f} "
                        "compensates for wrong firmware steps/mm)")
            speed_line = (
                f"   Speed:  user F{feed_user} mm/min  \u00d7 {scale:.4f}  "
                f"=  firmware F{sent_feed} mm/min  "
                "\u2190 robot physically moves at the user's requested speed"
            )
        self.log_message.emit(
            "\u2501" * 60 + "\n"
            f"\u2b95 JOG {self.axis}  COMMAND  =  {cmd:+.3f} mm  "
            "\u2190 expected physical distance\n"
            f"   {cal_note}\n"
            f"   G-code sent to firmware:  G0 {self.axis}{sent_dist:+.3f} F{sent_feed}\n"
            f"{speed_line}\n"
            f"   \u21bb  Now MEASURE the actual physical movement and "
            "enter it in the 'Measured' box below."
        )

    def _update_preview(self) -> None:
        commanded = self.commanded_spin.value()
        measured = self.measured_spin.value()
        if measured <= 0.0:
            self.preview_lbl.setText("New: <i>measured ≤ 0</i>")
            self._pending_new_value = None
            return
        ratio = commanded / measured
        delta_pct = abs(commanded - measured) / commanded * 100.0
        if self._mode == "software":
            new_scale = self.software_scale * ratio
            self._pending_new_scale = new_scale
            self._pending_new_value = None
            colour = "#a6e3a1" if delta_pct < 1.0 else \
                     "#f9e2af" if delta_pct < 10.0 else "#f38ba8"
            self.preview_lbl.setText(
                f"New scale: <span style='color:{colour}'>"
                f"×{new_scale:.4f}</span> "
                f"<span style='color:#a6adc8'>(Δ {delta_pct:.2f}%)</span>"
            )
        else:
            if self.firmware_steps is None:
                self.preview_lbl.setText("New $: <i>READ first</i>")
                return
            new_val = self.firmware_steps * ratio
            self._pending_new_value = new_val
            self._pending_new_scale = None
            colour = "#a6e3a1" if delta_pct < 1.0 else \
                     "#f9e2af" if delta_pct < 10.0 else "#f38ba8"
            self.preview_lbl.setText(
                f"New <code>${self.reg_id}</code>: "
                f"<span style='color:{colour}'>{new_val:.3f}</span> "
                f"<span style='color:#a6adc8'>(Δ {delta_pct:.2f}%)</span>"
            )

    def _on_apply(self) -> None:
        commanded = self.commanded_spin.value()
        measured = self.measured_spin.value()
        if measured <= 0.0:
            self.log_message.emit(
                f"⚠ {self.axis}-axis: measured distance must be > 0"
            )
            return

        if self._mode == "software":
            new_scale = self.software_scale * (commanded / measured)
            if not (0.1 <= new_scale <= 10.0):
                self.log_message.emit(
                    f"⚠ {self.axis}-axis: new scale {new_scale:.4f} out of "
                    "safe range (0.1–10.0) — refusing to apply."
                )
                return
            old = self.software_scale
            self.software_scale = new_scale
            self.sw_lbl.setText(self._sw_label_text())
            self._update_preview()
            self.request_software_apply.emit(
                self.axis, commanded, measured, new_scale,
            )
            self.log_message.emit(
                f"✓ {self.axis}: software scale {old:.4f} → "
                f"{new_scale:.4f}  (cmd {commanded:.2f}, meas {measured:.3f})"
            )
        else:
            if self.firmware_steps is None:
                self.log_message.emit(
                    f"⚠ {self.axis}-axis: READ $$ first."
                )
                return
            new_steps = self.firmware_steps * (commanded / measured)
            if not (1.0 <= new_steps <= 10000.0):
                self.log_message.emit(
                    f"⚠ {self.axis}-axis: new ${self.reg_id} = "
                    f"{new_steps:.2f} outside safe range (1–10000) "
                    "— refusing to apply."
                )
                return
            cmd = f"${self.reg_id}={new_steps:.3f}"
            self.request_send.emit(cmd)
            self.log_message.emit(
                f"✓ Sent {cmd}  ({self.axis}: {self.firmware_steps:.3f} "
                f"→ {new_steps:.3f} steps/mm)"
            )
            QTimer.singleShot(300, lambda: self.request_send.emit("$$"))

    def _on_reset(self) -> None:
        old = self.software_scale
        if old == 1.0:
            return
        self.software_scale = 1.0
        self.sw_lbl.setText(self._sw_label_text())
        self._update_preview()
        self.request_software_apply.emit(
            self.axis, self.commanded_spin.value(),
            self.measured_spin.value(), 1.0,
        )
        self.log_message.emit(
            f"⟲ {self.axis}: software scale reset {old:.4f} → 1.0000"
        )


class CalibrationPanel(QWidget):
    """Top-level steps/mm calibration wizard for X / Y / Z."""

    # Re-emitted up to MainWindow so the generator + settings.json get
    # updated when a software scale changes.  Args: x, y, z (final
    # multipliers), and a dict of last-used commanded/measured pairs.
    software_scales_changed = pyqtSignal(float, float, float, dict)

    def __init__(self, serial_worker, parent=None) -> None:
        super().__init__(parent)
        self.serial_worker = serial_worker
        self.setWindowTitle("Steps/mm Calibration")

        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(10)

        # ── header with explanation ─────────────────────────────────────
        header = QLabel(
            "<h3 style='color:#89b4fa;margin:0'>Motion Calibration "
            "(industrial accuracy)</h3>"
            "<p style='color:#a6adc8;font-size:9.5pt;margin:6px 0'>"
            "Tells the robot what \"1&nbsp;mm\" actually means.&nbsp;"
            "If you ask for 50&nbsp;mm and the head moves 75&nbsp;mm, "
            "this wizard fixes it.<br>"
            "<b>Procedure</b>: pick a mode → JOG one axis → measure "
            "with a steel rule → enter the value → APPLY → re-JOG to "
            "verify.&nbsp;Repeat per axis."
            "</p>"
        )
        header.setWordWrap(True)
        header.setTextFormat(Qt.RichText)
        root.addWidget(header)

        # ── mode chooser ────────────────────────────────────────────────
        mode_box = QGroupBox("Apply changes to:")
        mode_box.setStyleSheet(
            "QGroupBox { color:#cba6f7; font-weight:bold; "
            "            border:1px solid #45475a; border-radius:6px;"
            "            margin-top:12px; padding-top:12px; }"
            "QGroupBox::title { subcontrol-origin: margin; left: 10px;"
            "                   padding: 0 4px; }"
        )
        mode_lay = QVBoxLayout()
        self.sw_radio = QRadioButton(
            "Software (this app)  — recommended for FluidNC"
        )
        self.sw_radio.setStyleSheet("color:#a6e3a1;font-weight:bold;")
        self.sw_radio.setToolTip(
            "Stores per-axis scale in <code>settings.json</code> and "
            "scales every X/Y/Z coordinate before it's sent to the "
            "controller.&nbsp;Survives FluidNC YAML resets."
        )
        self.fw_radio = QRadioButton(
            "Firmware ($100/$101/$102) — vanilla GRBL"
        )
        self.fw_radio.setStyleSheet("color:#fab387;")
        self.fw_radio.setToolTip(
            "Writes the new value into the controller's EEPROM with "
            "<code>$&lt;n&gt;=&lt;value&gt;</code>.&nbsp;FluidNC "
            "users: this may be overwritten by your YAML on next boot."
        )
        self.sw_radio.setChecked(True)
        self.mode_group = QButtonGroup(self)
        self.mode_group.addButton(self.sw_radio)
        self.mode_group.addButton(self.fw_radio)
        self.sw_radio.toggled.connect(self._on_mode_changed)
        mode_lay.addWidget(self.sw_radio)
        mode_lay.addWidget(self.fw_radio)
        mode_box.setLayout(mode_lay)
        root.addWidget(mode_box)

        # ── connection / read button row ────────────────────────────────
        top = QHBoxLayout()
        self.read_btn = QPushButton("📖 READ $$ (current firmware values)")
        self.read_btn.setStyleSheet(
            "QPushButton { background:#cba6f7; color:#1e1e2e; font-weight:bold;"
            "              padding:8px 16px; border-radius:4px; }"
            "QPushButton:hover { background:#b4befe; }"
            "QPushButton:disabled { background:#45475a; color:#7c7e95; }"
        )
        self.read_btn.setEnabled(False)
        self.read_btn.setToolTip(
            "Send <code>$$</code> to GRBL — the current "
            "$100/$101/$102 values come back and populate the rows below."
        )
        self.read_btn.clicked.connect(self._on_read_settings)
        top.addWidget(self.read_btn)

        self.conn_lbl = QLabel(
            "<span style='color:#f38ba8;font-weight:bold'>"
            "● Disconnected — connect first</span>"
        )
        self.conn_lbl.setTextFormat(Qt.RichText)
        top.addWidget(self.conn_lbl, 1)
        root.addLayout(top)

        # ── one row per axis ────────────────────────────────────────────
        self.axes: Dict[str, _AxisCalibrator] = {}
        for axis in ("X", "Y", "Z"):
            row = _AxisCalibrator(axis, self)
            row.request_send.connect(self._send_raw)
            row.request_software_apply.connect(self._on_software_apply)
            row.log_message.connect(self._append_log)
            root.addWidget(row)
            self.axes[axis] = row
        self._on_mode_changed()       # set button labels correctly

        # ── activity log ────────────────────────────────────────────────
        log_box = QGroupBox("Activity Log")
        log_lay = QVBoxLayout()
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(500)
        self.log_view.setStyleSheet(
            "QPlainTextEdit { background:#11111b; color:#cdd6f4;"
            "                 font-family:'Courier New'; font-size: 9pt; }"
        )
        log_lay.addWidget(self.log_view)
        log_box.setLayout(log_lay)
        root.addWidget(log_box, 1)

        # ── wire to the worker ──────────────────────────────────────────
        self.serial_worker.connection_status.connect(self._on_connection_state)
        self.serial_worker.data_received.connect(self._on_data_line)

    # ----------------------------------------------------------------------
    # Public API consumed by MainWindow
    # ----------------------------------------------------------------------
    def load_from_settings(self, cfg: dict) -> None:
        if not isinstance(cfg, dict):
            return
        for axis in ("X", "Y", "Z"):
            row = self.axes[axis]
            row.update_software_scale(cfg.get(f"{axis.lower()}_scale", 1.0))
            row.restore_last_input(
                cfg.get(f"{axis.lower()}_commanded", 50.0),
                cfg.get(f"{axis.lower()}_measured", 50.0),
            )

    def get_settings(self) -> dict:
        out = {}
        for axis in ("X", "Y", "Z"):
            row = self.axes[axis]
            out[f"{axis.lower()}_scale"] = row.software_scale
            out[f"{axis.lower()}_commanded"] = row.commanded_spin.value()
            out[f"{axis.lower()}_measured"] = row.measured_spin.value()
        return out

    # ----------------------------------------------------------------------
    # Slots
    # ----------------------------------------------------------------------
    def _on_mode_changed(self) -> None:
        mode = "software" if self.sw_radio.isChecked() else "firmware"
        for row in self.axes.values():
            row.set_mode(mode)
        self._append_log(f"Mode: {mode.upper()}")

    def _on_connection_state(self, connected: bool, _msg: str) -> None:
        self.read_btn.setEnabled(connected)
        for row in self.axes.values():
            row.set_connected(connected)
        if connected:
            self.conn_lbl.setText(
                "<span style='color:#a6e3a1;font-weight:bold'>"
                "● Connected — ready to calibrate</span>"
            )
        else:
            self.conn_lbl.setText(
                "<span style='color:#f38ba8;font-weight:bold'>"
                "● Disconnected — connect first</span>"
            )

    def _on_read_settings(self) -> None:
        self._append_log("📖 Reading firmware settings ($$) …")
        self._send_raw("$$")

    # GRBL settings line: ``$100=80.000`` (some firmwares add comments)
    _SETTING_RE = re.compile(r"^\$(\d+)\s*=\s*([0-9.+\-eE]+)")

    def _on_data_line(self, line: str) -> None:
        line = (line or "").strip()
        if not line.startswith("$"):
            return
        m = self._SETTING_RE.match(line)
        if not m:
            return
        try:
            reg = int(m.group(1))
            val = float(m.group(2))
        except ValueError:
            return
        for axis, row in self.axes.items():
            if row.reg_id == reg:
                row.update_firmware_steps(val)
                self._append_log(
                    f"  • ${reg} ({axis}-axis) = {val:.3f} steps/mm"
                )
                break

    def _on_software_apply(self, axis: str, commanded: float,
                           measured: float, new_scale: float) -> None:
        """Forward up to MainWindow so the generator + settings update."""
        x = self.axes["X"].software_scale
        y = self.axes["Y"].software_scale
        z = self.axes["Z"].software_scale
        last = {
            "x_commanded": self.axes["X"].commanded_spin.value(),
            "x_measured":  self.axes["X"].measured_spin.value(),
            "y_commanded": self.axes["Y"].commanded_spin.value(),
            "y_measured":  self.axes["Y"].measured_spin.value(),
            "z_commanded": self.axes["Z"].commanded_spin.value(),
            "z_measured":  self.axes["Z"].measured_spin.value(),
        }
        self.software_scales_changed.emit(x, y, z, last)

    def _send_raw(self, cmd: str) -> None:
        if not getattr(self.serial_worker, "is_connected", False):
            self._append_log(f"⚠ Not connected — dropping: {cmd}")
            return
        ok = self.serial_worker.send_raw(cmd)
        if not ok:
            self._append_log(f"⚠ send_raw() refused: {cmd}")

    def _append_log(self, msg: str) -> None:
        # The log view is built late in __init__; early calls (e.g.
        # ``_on_mode_changed`` during construction) are silently
        # dropped instead of crashing.
        if hasattr(self, "log_view"):
            self.log_view.appendPlainText(msg)
