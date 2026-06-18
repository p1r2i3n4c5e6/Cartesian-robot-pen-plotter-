"""Automatic pen changer configuration panel."""

from __future__ import annotations

from PyQt5.QtCore import QSize, Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout, QGroupBox,
    QHBoxLayout, QInputDialog, QLabel, QMessageBox, QPushButton,
    QSpinBox, QStyle, QVBoxLayout, QWidget,
)

from ..config import PEN_PRESETS, TOOL_COLOR_MAP


class PenChangerPanel(QWidget):
    settings_changed = pyqtSignal()

    def __init__(self, serial_worker, parent=None):
        super().__init__(parent)
        self.serial_worker = serial_worker
        self.slot_data: dict = {}
        self._build()

    # ------------------------------------------------------------------
    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(4, 4, 4, 4)

        header = QLabel(
            "<b>Automatic Pen Changer</b><br>"
            "<span style='font-size:10px;color:#a6adc8'>"
            "Saved position = where the pen sits in the holder.<br>"
            "Robot approaches from <i>approach_dist</i> mm away, moves "
            "in to engage, retracts to leave the pen."
            "</span>"
        )
        header.setWordWrap(True)
        header.setStyleSheet("background: transparent;")
        root.addWidget(header)

        # Auto pen-changer is now ALWAYS-ON.  The check-box is kept on
        # screen so the user can see the state at a glance, but it is
        # forced to checked and made read-only so a stray click can't
        # accidentally turn it off and break the multi-colour workflow.
        self.enable_check = QCheckBox("Automatic pen changer (always on)")
        self.enable_check.setStyleSheet(
            "background: transparent; color:#a6e3a1; font-weight: bold;"
        )
        self.enable_check.setChecked(True)
        self.enable_check.setEnabled(False)        # read-only
        self.enable_check.setToolTip(
            "<b>Always enabled</b><br>"
            "Pen changes are driven automatically by the saved rack "
            "positions for every multi-colour job.  This switch is "
            "locked on so it can't be disabled by accident."
        )
        root.addWidget(self.enable_check)

        # Geometry group
        geom = QGroupBox("Approach Geometry")
        geom_lay = QFormLayout()

        self.approach_axis_combo = QComboBox()
        self.approach_axis_combo.addItem("Y", "Y")
        self.approach_axis_combo.addItem("X", "X")
        self.approach_axis_combo.currentIndexChanged.connect(
            lambda: self.settings_changed.emit())
        geom_lay.addRow("Approach axis:", self.approach_axis_combo)

        self.approach_dir_combo = QComboBox()
        self.approach_dir_combo.addItem("Forward (+) grabs pen", 1)
        self.approach_dir_combo.addItem("Backward (-) grabs pen", -1)
        self.approach_dir_combo.currentIndexChanged.connect(
            lambda: self.settings_changed.emit())
        geom_lay.addRow("Direction:", self.approach_dir_combo)

        self.approach_dist_spin = QDoubleSpinBox()
        self.approach_dist_spin.setRange(1, 500)
        self.approach_dist_spin.setValue(100)
        self.approach_dist_spin.setSingleStep(5)
        self.approach_dist_spin.setSuffix(" mm")
        self.approach_dist_spin.valueChanged.connect(
            lambda: self.settings_changed.emit())
        geom_lay.addRow("Approach distance:", self.approach_dist_spin)

        self.safe_z_spin = QDoubleSpinBox()
        self.safe_z_spin.setRange(0, 100)
        self.safe_z_spin.setValue(15)
        self.safe_z_spin.setSuffix(" mm")
        self.safe_z_spin.valueChanged.connect(
            lambda: self.settings_changed.emit())
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
        geom_lay.addRow("Pickup feed:", self.pickup_feed_spin)

        self.dwell_spin = QSpinBox()
        self.dwell_spin.setRange(0, 5000)
        self.dwell_spin.setValue(300)
        self.dwell_spin.setSuffix(" ms")
        self.dwell_spin.valueChanged.connect(
            lambda: self.settings_changed.emit())
        geom_lay.addRow("Dwell:", self.dwell_spin)

        self.return_check = QCheckBox("Return final pen to rack after job")
        self.return_check.setChecked(True)
        self.return_check.toggled.connect(lambda: self.settings_changed.emit())
        geom_lay.addRow(self.return_check)

        geom.setLayout(geom_lay)
        root.addWidget(geom)

        # Slot table
        slot_group = QGroupBox("Pen Rack Slots (X / Y / Z)")
        slot_lay = QVBoxLayout()
        hdr = QHBoxLayout()
        for text, w in [("Pen", 80), ("X", 60), ("Y", 60), ("Z", 60),
                        ("Save", 50), ("Goto", 50)]:
            lbl = QLabel(text)
            lbl.setFixedWidth(w)
            lbl.setStyleSheet(
                "background: transparent; color:#cdd6f4;"
                "font-weight: bold; font-size: 10px;"
            )
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
            color_label.setFixedWidth(80)
            text_color = "#ffffff" if sum(rgb) < 380 else "#1e1e2e"
            color_label.setStyleSheet(
                f"background-color: rgb({rgb[0]},{rgb[1]},{rgb[2]});"
                f"color: {text_color};"
                f"padding: 3px; border-radius: 3px;"
                f"font-weight: bold; font-size: 10px;"
            )
            row.addWidget(color_label)

            x_lbl = QLabel("---")
            y_lbl = QLabel("---")
            z_lbl = QLabel("---")
            for lbl in (x_lbl, y_lbl, z_lbl):
                lbl.setFixedWidth(60)
                lbl.setAlignment(Qt.AlignCenter)
                lbl.setStyleSheet(
                    "font-family:'Courier New'; font-size: 10px;"
                    "color:#cdd6f4; background:#181825;"
                    "padding: 2px; border-radius: 2px;"
                )
            row.addWidget(x_lbl)
            row.addWidget(y_lbl)
            row.addWidget(z_lbl)

            save_btn = QPushButton(" Save")
            save_btn.setIcon(
                self.style().standardIcon(QStyle.SP_DialogSaveButton)
            )
            save_btn.setIconSize(QSize(14, 14))
            save_btn.setFixedWidth(60)
            save_btn.setToolTip(
                f"<b>Save Slot for Pen T{tool} ({name})</b><br>"
                f"Stores the controller's current X / Y / Z position as "
                f"the rack slot for this pen. Jog to where the pen sits "
                f"in the holder, then click Save."
            )
            save_btn.setStyleSheet(
                "QPushButton { background:#45475a; color:#cdd6f4;"
                "  font-size: 10px; padding: 3px; border: 1px solid #585b70;"
                "  text-align: left; }"
                "QPushButton:hover { background:#585b70; color:#ffffff; }"
            )
            save_btn.clicked.connect(lambda _ch, t=tool: self._save_current_position(t))
            row.addWidget(save_btn)

            goto_btn = QPushButton(" Go")
            goto_btn.setIcon(
                self.style().standardIcon(QStyle.SP_MediaSeekForward)
            )
            goto_btn.setIconSize(QSize(14, 14))
            goto_btn.setFixedWidth(60)
            goto_btn.setToolTip(
                f"<b>Go to Slot for Pen T{tool} ({name})</b><br>"
                f"Lifts Z to the safe height, then moves the head over "
                f"the saved X / Y of this pen and lowers Z. Useful for "
                f"checking that the saved position is correct."
            )
            goto_btn.setStyleSheet(
                "QPushButton { background:#f9e2af; color:#1e1e2e;"
                "  font-size: 10px; padding: 3px;"
                "  text-align: left; }"
                "QPushButton:hover { background:#f5c84c; color:#1e1e2e; }"
            )
            goto_btn.clicked.connect(lambda _ch, t=tool: self._goto_slot(t))
            row.addWidget(goto_btn)

            slot_lay.addLayout(row)
            self.slot_rows[tool] = {"x": x_lbl, "y": y_lbl, "z": z_lbl}
        slot_group.setLayout(slot_lay)
        root.addWidget(slot_group)

        # Test buttons
        test_row = QHBoxLayout()
        test_btn = QPushButton(" Test Pickup Sequence")
        test_btn.setIcon(
            self.style().standardIcon(QStyle.SP_MediaPlay)
        )
        test_btn.setIconSize(QSize(18, 18))
        test_btn.setToolTip(
            "<b>Dry-run the Pen Pickup</b><br>"
            "Runs the full safe approach \u2192 engage \u2192 retract "
            "sequence on the FIRST saved pen slot so you can verify "
            "the geometry before drawing. The machine must be "
            "connected and homed."
        )
        test_btn.setStyleSheet(
            "QPushButton { background:#a6e3a1; color:#1e1e2e;"
            " font-weight: bold; padding: 6px;"
            " text-align: left; border-radius: 4px; }"
            "QPushButton:hover { background:#8ed388; color:#1e1e2e; }"
        )
        test_btn.clicked.connect(self._test_pickup_sequence)

        clear_btn = QPushButton(" Clear All Slots")
        clear_btn.setIcon(
            self.style().standardIcon(QStyle.SP_TrashIcon)
        )
        clear_btn.setIconSize(QSize(18, 18))
        clear_btn.setToolTip(
            "<b>Forget Every Saved Pen Position</b><br>"
            "Removes every X / Y / Z slot from this rack. "
            "You will be asked to confirm."
        )
        clear_btn.setStyleSheet(
            "QPushButton { background:#f38ba8; color:#1e1e2e;"
            " font-weight: bold; padding: 6px;"
            " text-align: left; border-radius: 4px; }"
            "QPushButton:hover { background:#eb6f92; color:#1e1e2e; }"
        )
        clear_btn.clicked.connect(self._clear_all_slots)
        test_row.addWidget(test_btn)
        test_row.addWidget(clear_btn)
        root.addLayout(test_row)
        root.addStretch()

    # ------------------------------------------------------------------
    def save_position_for_slot(self, pos_dict: dict) -> None:
        items = [f"T{p['tool']} {p['name']}" for p in PEN_PRESETS]
        choice, ok = QInputDialog.getItem(
            self, "Save Position",
            "Which pen slot for this position?", items, 0, False
        )
        if not ok:
            return
        tool = int(choice.split()[0][1:])
        self._store_slot(tool, pos_dict)

    def _save_current_position(self, tool: int) -> None:
        pos = self.serial_worker.machine_position
        if pos.get("X") is None:
            QMessageBox.warning(
                self, "No Position",
                "Machine position is unknown.\n"
                "Jog the machine and refresh status first."
            )
            return
        self._store_slot(tool, pos)

    def _store_slot(self, tool: int, pos: dict) -> None:
        preset = TOOL_COLOR_MAP.get(tool, {})
        self.slot_data[tool] = {
            "name": preset.get("name", f"Pen {tool}"),
            "x": float(pos.get("X", 0) or 0),
            "y": float(pos.get("Y", 0) or 0),
            "z": float(pos.get("Z", 0) or 0),
        }
        self._refresh_slot_display(tool)
        self.settings_changed.emit()

    def _refresh_slot_display(self, tool: int) -> None:
        row = self.slot_rows.get(tool)
        data = self.slot_data.get(tool)
        if not row or not data:
            return
        row["x"].setText(f"{data['x']:.2f}")
        row["y"].setText(f"{data['y']:.2f}")
        row["z"].setText(f"{data['z']:.2f}")

    def _refresh_all_displays(self) -> None:
        for tool in self.slot_rows:
            if tool in self.slot_data:
                self._refresh_slot_display(tool)

    def _goto_slot(self, tool: int) -> None:
        slot = self.slot_data.get(tool)
        if not slot:
            QMessageBox.warning(self, "No Position",
                                f"Pen T{tool} position not saved yet.")
            return
        if not self.serial_worker.is_connected:
            QMessageBox.warning(self, "Not Connected",
                                "Connect to the machine first.")
            return
        reply = QMessageBox.question(
            self, "Goto Position",
            (f"Move to:\n"
             f"X: {slot['x']:.2f}\n"
             f"Y: {slot['y']:.2f}\n"
             f"Z: {slot['z']:.2f}\n\n"
             f"Proceed?"),
            QMessageBox.Yes | QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        safe_z = self.safe_z_spin.value()
        sw = self.serial_worker
        sw.send_raw(f"G0 Z{safe_z:.3f}")
        sw.send_raw(f"G0 X{slot['x']:.3f} Y{slot['y']:.3f}")
        sw.send_raw(f"G0 Z{slot['z']:.3f}")

    def _clear_all_slots(self) -> None:
        reply = QMessageBox.question(
            self, "Clear All Slots",
            "Remove all saved pen positions?",
            QMessageBox.Yes | QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        self.slot_data.clear()
        for tool in self.slot_rows:
            row = self.slot_rows[tool]
            row["x"].setText("---")
            row["y"].setText("---")
            row["z"].setText("---")
        self.settings_changed.emit()

    def _test_pickup_sequence(self) -> None:
        if not self.serial_worker.is_connected:
            QMessageBox.warning(self, "Not Connected",
                                "Connect to the machine first.")
            return
        if not self.slot_data:
            QMessageBox.warning(self, "No Slots",
                                "Save at least one pen position first.")
            return
        first_tool = sorted(self.slot_data.keys())[0]
        slot = self.slot_data[first_tool]
        axis = self.approach_axis_combo.currentData() or "Y"
        direction = self.approach_dir_combo.currentData()
        dist = self.approach_dist_spin.value()
        safe_z = self.safe_z_spin.value()
        travel_f = self.travel_feed_spin.value()
        pickup_f = self.pickup_feed_spin.value()

        if axis == "X":
            ax = slot["x"] - direction * dist
            ay = slot["y"]
        else:
            ax = slot["x"]
            ay = slot["y"] - direction * dist

        reply = QMessageBox.question(
            self, "Test Pickup Sequence",
            (f"Test on {slot['name']} (T{first_tool}):\n\n"
             f"1. Safe Z = {safe_z:.2f} mm\n"
             f"2. Pre-approach ({ax:.1f}, {ay:.1f})\n"
             f"3. Lower Z = {slot['z']:.2f} mm\n"
             f"4. Engage to ({slot['x']:.1f}, {slot['y']:.1f})\n"
             f"5. Retract\n"
             f"6. Lift to safe Z\n\nProceed?"),
            QMessageBox.Yes | QMessageBox.No,
        )
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

    # ------------------------------------------------------------------
    def get_tool_changer_config(self) -> dict:
        return {
            "enabled": self.enable_check.isChecked(),
            "slots": {int(k): dict(v) for k, v in self.slot_data.items()},
            "safe_z": self.safe_z_spin.value(),
            "approach_dist": self.approach_dist_spin.value(),
            "approach_axis": self.approach_axis_combo.currentData() or "Y",
            "approach_dir": self.approach_dir_combo.currentData() or 1,
            "travel_feed": self.travel_feed_spin.value(),
            "pickup_feed": self.pickup_feed_spin.value(),
            "dwell_ms": self.dwell_spin.value(),
            "return_to_rack": self.return_check.isChecked(),
            "start_tool": 0,
        }

    def load_from_settings(self, cfg: dict) -> None:
        if not cfg:
            return
        # Auto pen-changer is force-enabled — ignore any saved value so
        # an older settings file with enabled=False can't undo the lock.
        self.enable_check.setChecked(True)
        self.approach_dist_spin.setValue(cfg.get("approach_dist", 100))
        axis_idx = self.approach_axis_combo.findData(cfg.get("approach_axis", "Y"))
        if axis_idx >= 0:
            self.approach_axis_combo.setCurrentIndex(axis_idx)
        dir_idx = self.approach_dir_combo.findData(cfg.get("approach_dir", -1))
        if dir_idx >= 0:
            self.approach_dir_combo.setCurrentIndex(dir_idx)
        self.safe_z_spin.setValue(cfg.get("safe_z", 15))
        self.travel_feed_spin.setValue(cfg.get("travel_feed", 3000))
        self.pickup_feed_spin.setValue(cfg.get("pickup_feed", 800))
        self.dwell_spin.setValue(cfg.get("dwell_ms", 300))
        self.return_check.setChecked(cfg.get("return_to_rack", True))
        slots = cfg.get("slots") or {}
        normalised = {}
        for key, value in slots.items():
            try:
                tool = int(key)
            except (TypeError, ValueError):
                continue
            if isinstance(value, dict) and "x" in value:
                normalised[tool] = {
                    "name": value.get("name", TOOL_COLOR_MAP.get(tool, {}).get("name", f"Pen {tool}")),
                    "x": float(value["x"]),
                    "y": float(value["y"]),
                    "z": float(value["z"]),
                }
        self.slot_data = normalised
        self._refresh_all_displays()
