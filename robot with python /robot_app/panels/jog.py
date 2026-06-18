"""Jog / DRO panel.

Every jog button gets a directional Qt standard icon and a tooltip
that explains exactly which axis moves and in which direction so a
user who is new to CNC conventions does not have to guess what `Y+`
or `A-` do.
"""

from PyQt5.QtCore import QSize, Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QComboBox, QDoubleSpinBox, QFormLayout, QGridLayout, QGroupBox,
    QHBoxLayout, QLabel, QMessageBox, QPushButton, QSpinBox, QStyle,
    QVBoxLayout, QWidget,
)


class JogPanel(QWidget):
    position_save_requested = pyqtSignal(dict)

    def __init__(self, serial_worker, parent=None):
        super().__init__(parent)
        self.serial_worker = serial_worker
        self._build()
        self.serial_worker.machine_position_changed.connect(self.update_dro)

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(4, 4, 4, 4)

        dro_group = QGroupBox("DRO (machine position)")
        dro_lay = QGridLayout()
        self.dro_labels = {}
        for col, axis in enumerate(("X", "Y", "Z", "A")):
            header = QLabel(axis)
            header.setAlignment(Qt.AlignCenter)
            header.setStyleSheet(
                "color: #89b4fa; background: transparent;"
                "font-weight: bold; font-size: 12px;"
            )
            dro_lay.addWidget(header, 0, col)
            val = QLabel("---")
            val.setAlignment(Qt.AlignCenter)
            val.setStyleSheet(
                "font-family:'Courier New',monospace; font-size: 13px;"
                "color:#a6e3a1; background:#181825;"
                "padding: 4px; border-radius: 3px;"
            )
            dro_lay.addWidget(val, 1, col)
            self.dro_labels[axis] = val
        self.state_label = QLabel("State: ---")
        self.state_label.setStyleSheet(
            "color:#f9e2af; background: transparent;"
            "font-size: 11px; padding: 4px;"
        )
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

        # ----- Calibration scale display -----------------------------
        # Shows the user EXACTLY which scale will be applied to each
        # axis when they click a jog button.  When an axis has scale
        # ×1.0000, the row is dimmed and a hint reminds the user that
        # axis is uncalibrated — the most common cause of "my jogs
        # move the wrong distance".
        self.cal_label = QLabel()
        self.cal_label.setTextFormat(Qt.RichText)
        self.cal_label.setWordWrap(True)
        self.cal_label.setStyleSheet(
            "QLabel { background:#11111b; color:#cdd6f4;"
            " border:1px solid #45475a; border-radius:4px;"
            " padding:6px 8px; font-family:'Courier New';"
            " font-size: 10pt; }"
        )
        self.cal_label.setToolTip(
            "Calibration multipliers applied to <b>every</b> jog click.<br>"
            "×1.0000 means the axis is <i>uncalibrated</i> — if your robot "
            "over-moves on that axis, open Windows ▸ Steps/mm Calibration "
            "and run the wizard for that axis specifically."
        )
        self._refresh_cal_label()
        root.addWidget(self.cal_label)

        # ----- Travel-envelope (soft-limit) editor ------------------
        # CRITICAL UX:  GRBL/FluidNC clips every move at the soft-limit
        # boundaries  $130 / $131 / $132 .   If those are smaller than
        # the user's *physical* machine, jogs will silently stop short
        # — e.g. ``$131 = 80`` makes Y always end at 80 mm regardless
        # of how the user calibrates steps/mm.   Calibration adjusts
        # mechanical ratio, NOT the firmware's max-travel envelope, so
        # we surface the envelope right next to the jog buttons where
        # the user notices the bug.
        env_group = QGroupBox(
            "Travel Envelope (firmware soft-limit  $130 / $131 / $132)"
        )
        env_lay = QGridLayout()
        env_lay.setSpacing(4)
        self.env_spins: dict = {}
        env_defaults = {"X": 610.0, "Y": 610.0, "Z": 100.0}
        for col, axis in enumerate(("X", "Y", "Z")):
            lbl = QLabel(f"{axis}")
            lbl.setStyleSheet("color:#89b4fa; font-weight:bold;")
            env_lay.addWidget(lbl, 0, col * 2)
            spin = QDoubleSpinBox()
            spin.setRange(1.0, 10000.0)
            spin.setDecimals(1)
            spin.setSuffix(" mm")
            spin.setValue(env_defaults[axis])
            spin.setMinimumWidth(95)
            spin.setToolTip(
                f"<b>{axis} max travel</b> — FluidNC will clip every "
                f"jog and G-code move so the head can never exceed this "
                f"value above the homing-corner.   Set to your <i>actual "
                f"physical travel</i> (or slightly less for safety)."
            )
            self.env_spins[axis] = spin
            env_lay.addWidget(spin, 0, col * 2 + 1)

        apply_env_btn = QPushButton("Apply  ($130 / $131 / $132)")
        apply_env_btn.setToolTip(
            "<b>Update firmware soft limits</b><br>"
            "Sends <code>$130=&lt;X&gt;</code>, <code>$131=&lt;Y&gt;</code>, "
            "<code>$132=&lt;Z&gt;</code> to the controller.   This fixes the "
            "“jog stops at 80 mm even though my robot is 610 mm long” "
            "problem — the limit is in the firmware, not the calibration."
        )
        apply_env_btn.setStyleSheet(
            "QPushButton { background:#a6e3a1; color:#1e1e2e;"
            " font-weight:bold; padding:7px;"
            " border:1px solid #94d68a; border-radius:4px; }"
            "QPushButton:hover { background:#b5e8a8; }"
            "QPushButton:disabled { background:#45475a; color:#6c7086; }"
        )
        apply_env_btn.clicked.connect(self._apply_envelope)
        env_lay.addWidget(apply_env_btn, 1, 0, 1, 6)

        env_hint = QLabel(
            "⚠  If your jog stops short of where the head can actually "
            "reach, increase these values and click Apply."
        )
        env_hint.setWordWrap(True)
        env_hint.setStyleSheet(
            "color:#fab387; font-size:9pt; padding:2px 4px;"
        )
        env_lay.addWidget(env_hint, 2, 0, 1, 6)

        env_group.setLayout(env_lay)
        root.addWidget(env_group)

        xy_group = QGroupBox("XY Jog (move table along X / Y)")
        xy_grid = QGridLayout()
        xy_grid.setSpacing(2)
        # XY directional buttons with arrow icons + tooltips
        bx_yp = self._jog_btn(
            "Y+", QStyle.SP_ArrowUp,
            "<b>Jog Y+</b><br>Move Y axis forward (away from front).<br>"
            "<i>Step controlled by the Step combo above.</i>",
            lambda: self._jog(dy=self._step()),
        )
        bx_ym = self._jog_btn(
            "Y-", QStyle.SP_ArrowDown,
            "<b>Jog Y-</b><br>Move Y axis backward (toward front).<br>"
            "<i>Step controlled by the Step combo above.</i>",
            lambda: self._jog(dy=-self._step()),
        )
        bx_xm = self._jog_btn(
            "X-", QStyle.SP_ArrowLeft,
            "<b>Jog X-</b><br>Move X axis left.<br>"
            "<i>Step controlled by the Step combo above.</i>",
            lambda: self._jog(dx=-self._step()),
        )
        bx_xp = self._jog_btn(
            "X+", QStyle.SP_ArrowRight,
            "<b>Jog X+</b><br>Move X axis right.<br>"
            "<i>Step controlled by the Step combo above.</i>",
            lambda: self._jog(dx=self._step()),
        )
        bx_home = self._jog_btn(
            "", QStyle.SP_DirHomeIcon,
            "<b>Go to Work Origin (XY)</b><br>"
            "Sends <code>G0 X0 Y0</code> in the current work coordinate "
            "system. Z is not moved \u2014 raise it manually first if "
            "needed to avoid dragging the pen across the page.",
            lambda: self.serial_worker.send_raw("G0 X0 Y0"),
        )
        for b in (bx_yp, bx_ym, bx_xm, bx_xp, bx_home):
            b.setFixedSize(58, 38)
        xy_grid.addWidget(bx_yp, 0, 1)
        xy_grid.addWidget(bx_xm, 1, 0)
        xy_grid.addWidget(bx_home, 1, 1)
        xy_grid.addWidget(bx_xp, 1, 2)
        xy_grid.addWidget(bx_ym, 2, 1)
        xy_group.setLayout(xy_grid)
        root.addWidget(xy_group)

        za_group = QGroupBox("Z / A Jog")
        za_lay = QGridLayout()
        za_lay.setSpacing(2)
        bz_p = self._jog_btn(
            "Z+", QStyle.SP_ArrowUp,
            "<b>Jog Z+</b><br>Raise Z (pen up).<br>"
            "<i>Step controlled by the Step combo above.</i>",
            lambda: self._jog(dz=self._step()),
        )
        bz_m = self._jog_btn(
            "Z-", QStyle.SP_ArrowDown,
            "<b>Jog Z-</b><br>Lower Z (pen down). "
            "<b>BE CAREFUL</b> not to crash the pen into the bed.",
            lambda: self._jog(dz=-self._step()),
        )
        ba_p = self._jog_btn(
            "A+", QStyle.SP_ArrowRight,
            "<b>Jog A+</b><br>Rotate A axis in the positive direction.",
            lambda: self._jog(da=self._step()),
        )
        ba_m = self._jog_btn(
            "A-", QStyle.SP_ArrowLeft,
            "<b>Jog A-</b><br>Rotate A axis in the negative direction.",
            lambda: self._jog(da=-self._step()),
        )
        for b in (bz_p, bz_m, ba_p, ba_m):
            b.setFixedSize(58, 38)
        z_lbl = QLabel("Z")
        z_lbl.setToolTip("Vertical axis (pen up / pen down)")
        z_lbl.setStyleSheet("background: transparent; color:#cdd6f4;"
                            "font-weight: bold;")
        a_lbl = QLabel("A")
        a_lbl.setToolTip("Rotary axis (rotation, optional)")
        a_lbl.setStyleSheet("background: transparent; color:#cdd6f4;"
                            "font-weight: bold;")
        za_lay.addWidget(z_lbl, 0, 0, Qt.AlignCenter)
        za_lay.addWidget(bz_p, 0, 1)
        za_lay.addWidget(bz_m, 0, 2)
        za_lay.addWidget(a_lbl, 1, 0, Qt.AlignCenter)
        za_lay.addWidget(ba_p, 1, 1)
        za_lay.addWidget(ba_m, 1, 2)
        za_group.setLayout(za_lay)
        root.addWidget(za_group)

        save_btn = QPushButton(" Save Current Position to Pen Slot...")
        save_btn.setIcon(self.style().standardIcon(QStyle.SP_DialogSaveButton))
        save_btn.setIconSize(QSize(18, 18))
        save_btn.setToolTip(
            "<b>Save Current Position</b><br>"
            "Stores the current X / Y / Z reading as a pen-rack slot "
            "that the auto pen-changer will use later.<br>"
            "You will be asked which pen slot to assign it to."
        )
        save_btn.setStyleSheet(
            "QPushButton { background:#f38ba8; color:#1e1e2e;"
            " font-weight:bold; padding:8px; border-radius:4px;"
            " text-align: left; }"
            "QPushButton:hover { background:#eb6f92; color:#1e1e2e; }"
        )
        save_btn.clicked.connect(self._emit_save_position)
        root.addWidget(save_btn)

        status_btn = QPushButton(" Refresh Status  (?)")
        status_btn.setIcon(
            self.style().standardIcon(QStyle.SP_BrowserReload)
        )
        status_btn.setIconSize(QSize(18, 18))
        status_btn.setToolTip(
            "<b>Refresh DRO</b><br>"
            "Sends <code>?</code> to GRBL and updates the digital "
            "read-out above with the latest machine position and state."
        )
        status_btn.setStyleSheet(
            "QPushButton { background:#45475a; color:#cdd6f4;"
            " padding:7px; border:1px solid #585b70; border-radius:4px;"
            " text-align: left; }"
            "QPushButton:hover { background:#585b70; color:#ffffff;"
            " border:1px solid #89b4fa; }"
        )
        status_btn.clicked.connect(self.serial_worker.get_status)
        root.addWidget(status_btn)

        root.addStretch()

    def _jog_btn(self, label: str, icon_enum, tooltip: str,
                 callback) -> QPushButton:
        btn = QPushButton(label)
        btn.setIcon(self.style().standardIcon(icon_enum))
        btn.setIconSize(QSize(16, 16))
        btn.setToolTip(tooltip)
        btn.setStyleSheet(
            "QPushButton { background:#45475a; color:#cdd6f4;"
            " font-weight:bold; border:1px solid #585b70; border-radius:4px; }"
            "QPushButton:hover { background:#585b70; color:#ffffff;"
            " border:1px solid #89b4fa; }"
            "QPushButton:pressed { background:#89b4fa; color:#1e1e2e; }"
        )
        btn.clicked.connect(callback)
        return btn

    # ------------------------------------------------------------------
    def _step(self) -> float:
        return self.step_combo.currentData() or 1.0

    def _jog(self, dx: float = 0.0, dy: float = 0.0,
             dz: float = 0.0, da: float = 0.0) -> None:
        # Diagnostic banner so the user can see EXACTLY what's about to
        # be sent.  Especially valuable for the case where one axis is
        # calibrated (×0.1) and another isn't (×1.0) — the user can
        # spot "hey, why is my Y axis still ×1.0?" before clicking.
        cal = (self.serial_worker._cal_x,
               self.serial_worker._cal_y,
               self.serial_worker._cal_z)
        scaled = (dx * cal[0], dy * cal[1], dz * cal[2])
        # Compute the feed scale exactly as SerialWorker.jog_incremental
        # will: smallest scale among the moving axes.  That way the
        # user sees in the banner the same speed value the firmware
        # is about to receive.
        feed_user = self.jog_feed_spin.value()
        moving = [s for s, v in zip(cal, (dx, dy, dz)) if abs(v) > 1e-9]
        f_scale = min(moving) if moving else 1.0
        feed_sent = max(1, int(round(feed_user * f_scale)))
        self.state_label.setText(
            f"→ jog cmd  X{dx:+.3f} Y{dy:+.3f} Z{dz:+.3f}  F{feed_user}  "
            f"→ sent X{scaled[0]:+.3f} Y{scaled[1]:+.3f} Z{scaled[2]:+.3f}  "
            f"F{feed_sent}"
        )
        self.serial_worker.jog_incremental(
            dx=dx, dy=dy, dz=dz, da=da, feed=feed_user
        )

    def update_dro(self, pos: dict) -> None:
        for axis in ("X", "Y", "Z", "A"):
            value = pos.get(axis)
            text = f"{value:.3f}" if value is not None else "---"
            self.dro_labels[axis].setText(text)
        # Don't clobber a recent jog-diagnostic line on the state row.
        cur = self.state_label.text()
        if not cur.startswith("→ jog cmd"):
            self.state_label.setText(f"State: {pos.get('state', '---')}")

    # ------------------------------------------------------------------
    def _refresh_cal_label(self) -> None:
        """Re-render the per-axis calibration banner."""
        sw = self.serial_worker
        cx, cy, cz = sw._cal_x, sw._cal_y, sw._cal_z

        def _row(axis: str, scale: float) -> str:
            if abs(scale - 1.0) < 1e-6:
                return (f"<span style='color:#7c7e95'>"
                        f"{axis}×{scale:.4f} <i>(uncalibrated)</i></span>")
            colour = "#a6e3a1" if 0.5 <= scale <= 2.0 else "#f9e2af"
            return (f"<b style='color:{colour}'>"
                    f"{axis}×{scale:.4f}</b>")

        body = (f"<b style='color:#89b4fa'>Live calibration</b>: "
                f"{_row('X', cx)}  &nbsp; "
                f"{_row('Y', cy)}  &nbsp; "
                f"{_row('Z', cz)}")
        if any(abs(s - 1.0) < 1e-6 for s in (cx, cy, cz)):
            body += (
                "<br><span style='color:#fab387'>⚠ Some axes are at "
                "×1.0 — open Windows ▸ <b>Steps/mm Calibration</b> to "
                "calibrate them or your jogs will be wrong on those axes."
                "</span>"
            )
        self.cal_label.setText(body)

    def refresh_calibration_display(self) -> None:
        """Public hook for MainWindow to refresh after a wizard APPLY."""
        self._refresh_cal_label()

    # ------------------------------------------------------------------
    def _apply_envelope(self) -> None:
        """Push $130 / $131 / $132 to the firmware.

        Without this, the user's jogs are silently clipped at
        whatever travel limits the firmware was previously configured
        with — typically the FluidNC defaults or a tiny test value
        like 80 mm.   Calibration only changes steps/mm and cannot
        affect this; the soft-limit boundary lives in firmware.
        """
        if not getattr(self.serial_worker, "is_connected", False):
            QMessageBox.warning(
                self, "Not connected",
                "Connect to the controller first — the new soft-limit "
                "values need to be written to firmware before they "
                "take effect.",
            )
            return
        x = self.env_spins["X"].value()
        y = self.env_spins["Y"].value()
        z = self.env_spins["Z"].value()
        # Send each one separately so the user can see in the console
        # which line was rejected if their controller doesn't support
        # one of the addresses.
        for cmd in (f"$130={x:.3f}", f"$131={y:.3f}", f"$132={z:.3f}"):
            self.serial_worker.send_raw(cmd)
        self.state_label.setText(
            f"→ envelope updated  →  $130={x:.0f}  $131={y:.0f}  $132={z:.0f}"
        )

    def update_envelope_from_grbl(self, parsed: dict) -> None:
        """Called when ``$$`` parser yields fresh values from the controller.

        Lets the spinboxes mirror the firmware's actual current limits
        so the user is never surprised by mismatched values between
        the panel and the hardware.
        """
        for grbl_key, axis in ((130, "X"), (131, "Y"), (132, "Z")):
            if grbl_key in parsed and axis in self.env_spins:
                try:
                    self.env_spins[axis].setValue(float(parsed[grbl_key]))
                except (TypeError, ValueError):
                    pass

    def _emit_save_position(self) -> None:
        pos = self.serial_worker.machine_position
        if pos.get("X") is None:
            QMessageBox.warning(
                self, "No Position",
                "Machine position is unknown.\n\n"
                "Connect to the machine and click Refresh Status first."
            )
            return
        self.position_save_requested.emit(pos)
