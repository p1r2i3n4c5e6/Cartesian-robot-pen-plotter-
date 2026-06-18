"""Homing & origin definition panel.

Lets the user:

* Enable / disable homing per axis (X / Y / Z / A) and pick search
  direction (positive vs negative).
* Configure GRBL global homing rates ($24 / $25 / $26 / $27) and
  per-axis max travel ($130–$133) and apply them in one click.
* Read the controller's current settings back via ``$$``.
* Trigger a full or per-axis homing cycle (``$H``, ``$HX``…).
* Capture the current position as **work zero** (``G10 L20 P1``) per
  axis or for everything at once.
* Save / recall a **G28 predefined position** without leaving the GUI.
* Define a **manual machine origin** with ``G92 X0 Y0 Z0`` for
  switch-less plotters.

Every choice is also persisted in ``settings.json`` under the
``homing`` key so the next session starts with the same setup.
"""

from __future__ import annotations

import re
from typing import Dict, Optional

from PyQt5.QtCore import QSize, Qt, QTimer, pyqtSignal
from PyQt5.QtWidgets import (
    QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout, QGridLayout,
    QGroupBox, QHBoxLayout, QLabel, QMessageBox, QPushButton, QSpinBox,
    QStyle, QVBoxLayout, QWidget,
)


_AXES = ("X", "Y", "Z", "A")


class HomingPanel(QWidget):
    """Configure homing + work zero for every axis."""

    settings_changed = pyqtSignal()
    # Emitted with the parsed ``$$`` dict whenever the user clicks
    # "Read settings" so other panels (notably the Jog panel's travel
    # envelope group) can mirror the firmware's actual current values.
    grbl_settings_loaded = pyqtSignal(dict)

    # GRBL $$ keys we care about
    _GRBL_KEYS = (22, 23, 24, 25, 26, 27, 130, 131, 132, 133)

    def __init__(self, serial_worker, parent=None):
        super().__init__(parent)
        self.serial_worker = serial_worker
        self._reading_grbl = False
        self._read_buffer: Dict[int, float] = {}
        self._current_position: Dict[str, Optional[float]] = {
            a: None for a in _AXES
        }

        self._build()
        self.serial_worker.machine_position_changed.connect(self._update_dro)
        self.serial_worker.data_received.connect(self._collect_grbl_setting)

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------
    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(4, 4, 4, 4)

        intro = QLabel(
            "<b>Define homing &amp; origin per axis.</b><br>"
            "<span style='font-size:10px;color:#a6adc8;'>"
            "Tick which axes have limit switches, pick search direction, "
            "set max travel, then <i>Apply to GRBL</i>. Use the work-zero "
            "and G28 buttons to define where the program origin sits."
            "</span>"
        )
        intro.setWordWrap(True)
        intro.setStyleSheet("background: transparent;")
        root.addWidget(intro)

        # ---- per-axis grid -------------------------------------------
        per_axis = QGroupBox("Per-axis Homing Configuration")
        grid = QGridLayout()
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(4)

        headers = ("Axis", "Home?", "Direction", "Pull-off", "Max Travel")
        for col, text in enumerate(headers):
            lbl = QLabel(text)
            lbl.setStyleSheet(
                "background: transparent; color:#89b4fa;"
                "font-weight: bold; font-size: 11px;"
            )
            lbl.setAlignment(Qt.AlignCenter)
            grid.addWidget(lbl, 0, col)

        self.home_checks: Dict[str, QCheckBox] = {}
        self.dir_combos: Dict[str, QComboBox] = {}
        self.pulloff_spins: Dict[str, QDoubleSpinBox] = {}
        self.max_travel: Dict[str, QDoubleSpinBox] = {}

        defaults = {
            "X": (True,  1, 1.0, 200.0),
            "Y": (True,  1, 1.0, 200.0),
            "Z": (True,  1, 1.0, 50.0),
            "A": (False, 1, 0.0, 360.0),
        }

        for row, axis in enumerate(_AXES, start=1):
            enabled, direction, pull, travel = defaults[axis]

            axis_lbl = QLabel(axis)
            axis_lbl.setAlignment(Qt.AlignCenter)
            axis_lbl.setStyleSheet(
                "color:#a6e3a1; background:#181825;"
                "padding:4px; border-radius:3px;"
                "font-weight:bold; font-family:'Courier New';"
            )
            grid.addWidget(axis_lbl, row, 0)

            chk = QCheckBox()
            chk.setChecked(enabled)
            chk.toggled.connect(lambda _v: self.settings_changed.emit())
            chk.setStyleSheet("background: transparent;")
            self.home_checks[axis] = chk
            grid.addWidget(chk, row, 1, Qt.AlignCenter)

            combo = QComboBox()
            combo.addItem("Positive (+)", 1)
            combo.addItem("Negative (-)", -1)
            combo.setCurrentIndex(0 if direction > 0 else 1)
            combo.currentIndexChanged.connect(
                lambda _i: self.settings_changed.emit()
            )
            self.dir_combos[axis] = combo
            grid.addWidget(combo, row, 2)

            pull_spin = QDoubleSpinBox()
            pull_spin.setRange(0.0, 50.0)
            pull_spin.setSingleStep(0.5)
            pull_spin.setSuffix(" mm")
            pull_spin.setValue(pull)
            pull_spin.valueChanged.connect(lambda _v: self.settings_changed.emit())
            self.pulloff_spins[axis] = pull_spin
            grid.addWidget(pull_spin, row, 3)

            max_spin = QDoubleSpinBox()
            max_spin.setRange(0.0, 5000.0)
            max_spin.setSingleStep(1.0)
            max_spin.setSuffix(" mm")
            max_spin.setValue(travel)
            max_spin.valueChanged.connect(lambda _v: self.settings_changed.emit())
            self.max_travel[axis] = max_spin
            grid.addWidget(max_spin, row, 4)

        per_axis.setLayout(grid)
        root.addWidget(per_axis)

        # ---- global rates --------------------------------------------
        rates = QGroupBox("Homing Rates ($24 / $25 / $26)")
        rates_lay = QFormLayout()
        self.seek_feed = QSpinBox()
        self.seek_feed.setRange(10, 20000)
        self.seek_feed.setValue(1500)
        self.seek_feed.setSuffix(" mm/min")
        self.seek_feed.setToolTip("$25 — fast approach feed")
        self.seek_feed.valueChanged.connect(lambda _v: self.settings_changed.emit())
        rates_lay.addRow("Seek rate ($25):", self.seek_feed)

        self.locate_feed = QSpinBox()
        self.locate_feed.setRange(10, 5000)
        self.locate_feed.setValue(100)
        self.locate_feed.setSuffix(" mm/min")
        self.locate_feed.setToolTip("$24 — slow precision feed")
        self.locate_feed.valueChanged.connect(lambda _v: self.settings_changed.emit())
        rates_lay.addRow("Locate rate ($24):", self.locate_feed)

        self.debounce = QSpinBox()
        self.debounce.setRange(0, 1000)
        self.debounce.setValue(250)
        self.debounce.setSuffix(" ms")
        self.debounce.setToolTip("$26 — switch debounce")
        self.debounce.valueChanged.connect(lambda _v: self.settings_changed.emit())
        rates_lay.addRow("Debounce ($26):", self.debounce)

        rates.setLayout(rates_lay)
        root.addWidget(rates)

        # ---- apply / read --------------------------------------------
        rw_row = QHBoxLayout()
        apply_btn = QPushButton(" Apply to GRBL")
        apply_btn.setIcon(self.style().standardIcon(QStyle.SP_DialogApplyButton))
        apply_btn.setIconSize(QSize(18, 18))
        apply_btn.setToolTip(
            "<b>Write Settings</b><br>"
            "Sends <code>$22</code> / <code>$23</code> / <code>$24</code> / "
            "<code>$25</code> / <code>$26</code> / <code>$27</code> and "
            "<code>$130</code>\u2013<code>$133</code> to GRBL using the "
            "values in the form above. Restart the controller after "
            "changing direction-invert masks."
        )
        apply_btn.setStyleSheet(
            "QPushButton { background:#7c3aed; color:white; padding:7px;"
            "  font-weight:bold; border-radius:4px;"
            "  text-align:left; }"
            "QPushButton:hover { background:#6d28d9; color:white; }"
        )
        apply_btn.clicked.connect(self._apply_to_grbl)
        rw_row.addWidget(apply_btn)

        read_btn = QPushButton(" Read from GRBL")
        read_btn.setIcon(self.style().standardIcon(QStyle.SP_BrowserReload))
        read_btn.setIconSize(QSize(18, 18))
        read_btn.setToolTip(
            "<b>Read Settings</b><br>"
            "Sends <code>$$</code> and pulls the homing-related "
            "parameters back into the form so you can review what is "
            "actually saved on the controller."
        )
        read_btn.setStyleSheet(
            "QPushButton { background:#45475a; color:#cdd6f4; padding:7px;"
            "  border:1px solid #585b70; border-radius:4px;"
            "  text-align:left; }"
            "QPushButton:hover { background:#585b70; color:#ffffff; }"
        )
        read_btn.clicked.connect(self._read_from_grbl)
        rw_row.addWidget(read_btn)
        root.addLayout(rw_row)

        # ---- homing actions ------------------------------------------
        cycle = QGroupBox("Homing Cycle Actions")
        cycle_lay = QGridLayout()
        cycle_lay.setSpacing(4)
        all_btn = self._make_action_btn(
            " $H  Home All", "#2196F3", "white", self._home_all,
            icon=QStyle.SP_DirHomeIcon,
            tooltip=(
                "<b>Run Full Homing Cycle</b><br>"
                "Sends <code>$H</code>. Every enabled axis seeks its "
                "limit switch, backs off the pull-off distance and "
                "machine zero is established. Make sure the path is "
                "clear before clicking!"
            ),
        )
        cycle_lay.addWidget(all_btn, 0, 0, 1, 2)
        for col, axis in enumerate(_AXES):
            btn = self._make_action_btn(
                f" $H{axis}", "#1976D2", "white",
                lambda _ch, a=axis: self._home_axis(a),
                icon=QStyle.SP_ArrowUp if axis in ("Y", "Z")
                else QStyle.SP_ArrowRight,
                tooltip=(
                    f"<b>Home {axis} axis only</b><br>"
                    f"Sends <code>$H{axis}</code>. Only the {axis} axis "
                    f"moves toward its limit switch."
                ),
            )
            cycle_lay.addWidget(btn, 1, col)
        cycle.setLayout(cycle_lay)
        root.addWidget(cycle)

        # ---- DRO + work zero -----------------------------------------
        wz = QGroupBox("Work Zero (G54) — current position")
        wz_lay = QVBoxLayout()
        dro_row = QHBoxLayout()
        self.dro_labels: Dict[str, QLabel] = {}
        for axis in _AXES:
            lbl = QLabel(f"{axis}: ---")
            lbl.setAlignment(Qt.AlignCenter)
            lbl.setStyleSheet(
                "color:#a6e3a1; background:#181825;"
                "padding:4px; border-radius:3px;"
                "font-family:'Courier New', monospace;"
                "font-size:11px;"
            )
            self.dro_labels[axis] = lbl
            dro_row.addWidget(lbl)
        wz_lay.addLayout(dro_row)

        zero_row = QHBoxLayout()
        zero_all = self._make_action_btn(
            " Set ALL to 0,0,0", "#a6e3a1", "#1e1e2e",
            lambda _ch: self._set_work_zero(_AXES),
            icon=QStyle.SP_DialogApplyButton,
            tooltip=(
                "<b>Set Work Origin</b><br>"
                "Sends <code>G10 L20 P1 X0 Y0 Z0 A0</code>. The "
                "controller's current position becomes the new work "
                "origin (G54)."
            ),
        )
        zero_row.addWidget(zero_all)
        for axis in _AXES:
            btn = self._make_action_btn(
                f" Zero {axis}", "#f9e2af", "#1e1e2e",
                lambda _ch, a=axis: self._set_work_zero((a,)),
                icon=QStyle.SP_DialogResetButton,
                tooltip=(
                    f"<b>Zero {axis} only</b><br>"
                    f"Sends <code>G10 L20 P1 {axis}0</code> \u2014 makes "
                    f"the current {axis} position the new work-zero for "
                    f"that one axis."
                ),
            )
            zero_row.addWidget(btn)
        wz_lay.addLayout(zero_row)

        goto_row = QHBoxLayout()
        goto_btn = self._make_action_btn(
            " Go to Work Zero (X0 Y0)", "#89b4fa", "#1e1e2e",
            lambda _ch: self._goto_work_zero(),
            icon=QStyle.SP_MediaSeekBackward,
            tooltip=(
                "<b>Return to Work Zero</b><br>"
                "Lifts Z to safe height first, then issues <code>G0 X0 "
                "Y0</code> in the work coordinate system."
            ),
        )
        goto_row.addWidget(goto_btn)
        wz_lay.addLayout(goto_row)
        wz.setLayout(wz_lay)
        root.addWidget(wz)

        # ---- G28 predefined position ---------------------------------
        g28 = QGroupBox("G28 Predefined Position")
        g28_lay = QHBoxLayout()
        set_g28_btn = self._make_action_btn(
            " Set Current as G28 (G28.1)", "#cba6f7", "#1e1e2e",
            lambda _ch: self._set_g28(),
            icon=QStyle.SP_DialogSaveButton,
            tooltip=(
                "<b>Save G28 Position</b><br>"
                "Sends <code>G28.1</code>. The controller stores the "
                "current machine position as the predefined G28 "
                "location and remembers it across power cycles."
            ),
        )
        goto_g28_btn = self._make_action_btn(
            " Go to G28 (safe Z first)", "#cba6f7", "#1e1e2e",
            lambda _ch: self._goto_g28(),
            icon=QStyle.SP_MediaPlay,
            tooltip=(
                "<b>Move to G28</b><br>"
                "Sends <code>G0 Z[safe]</code> followed by <code>G28</code> "
                "so the head rises before the X / Y move \u2014 prevents "
                "crashing the pen across the work surface."
            ),
        )
        g28_lay.addWidget(set_g28_btn)
        g28_lay.addWidget(goto_g28_btn)
        g28.setLayout(g28_lay)
        root.addWidget(g28)

        # ---- manual home override ------------------------------------
        manual = QGroupBox("Manual Machine Origin (no limit switches)")
        manual_lay = QVBoxLayout()
        warn = QLabel(
            "If your plotter has no limit switches, jog to the desired "
            "origin and click below — this issues <b>G92 X0 Y0 Z0</b> so "
            "the controller treats the current position as machine zero."
        )
        warn.setWordWrap(True)
        warn.setStyleSheet(
            "background: transparent; color:#f9e2af; font-size: 10px;"
        )
        manual_lay.addWidget(warn)
        define_btn = self._make_action_btn(
            " Define Current Position as Origin (G92 X0 Y0 Z0)",
            "#f38ba8", "#1e1e2e", lambda _ch: self._manual_define_origin(),
            icon=QStyle.SP_DirHomeIcon,
            tooltip=(
                "<b>Manual Origin</b><br>"
                "Sends <code>G92 X0 Y0 Z0</code>. For plotters without "
                "limit switches, jog the head to where you want machine "
                "zero and click \u2014 GRBL treats the present position "
                "as origin until you reset."
            ),
        )
        manual_lay.addWidget(define_btn)
        manual.setLayout(manual_lay)
        root.addWidget(manual)

        root.addStretch()

    # ------------------------------------------------------------------
    def _make_action_btn(self, label, bg, fg, callback,
                        icon=None, tooltip: str = "") -> QPushButton:
        btn = QPushButton(label)
        if icon is not None:
            btn.setIcon(self.style().standardIcon(icon))
            btn.setIconSize(QSize(18, 18))
        if tooltip:
            btn.setToolTip(tooltip)
        btn.setStyleSheet(
            f"QPushButton {{"
            f"  background-color: {bg}; color: {fg};"
            f"  padding: 7px; font-weight: bold; border-radius: 4px;"
            f"  text-align: left;"
            f"}} "
            f"QPushButton:hover {{"
            f"  background-color: {bg}; color: {fg};"
            f"  border: 2px solid #89b4fa;"
            f"}}"
        )
        btn.clicked.connect(callback)
        return btn

    # ------------------------------------------------------------------
    # GRBL settings round-trip
    # ------------------------------------------------------------------
    def _apply_to_grbl(self) -> None:
        if not self._require_connection():
            return
        any_homing = any(c.isChecked() for c in self.home_checks.values())
        # $23 = direction-invert mask: bit set => negative search direction
        mask = 0
        for idx, axis in enumerate(_AXES):
            if self.dir_combos[axis].currentData() < 0:
                mask |= (1 << idx)

        commands = [
            f"$22={'1' if any_homing else '0'}",
            f"$23={mask}",
            f"$24={self.locate_feed.value()}",
            f"$25={self.seek_feed.value()}",
            f"$26={self.debounce.value()}",
            # Pull-off is a single value on classic GRBL — pick the max
            f"$27={max(s.value() for s in self.pulloff_spins.values()):.3f}",
            f"$130={self.max_travel['X'].value():.3f}",
            f"$131={self.max_travel['Y'].value():.3f}",
            f"$132={self.max_travel['Z'].value():.3f}",
            # $133 only exists on grblHAL; harmless if rejected
            f"$133={self.max_travel['A'].value():.3f}",
        ]
        for cmd in commands:
            self.serial_worker.send_raw(cmd)
        self.settings_changed.emit()
        QMessageBox.information(
            self, "Applied",
            "Homing settings sent to controller.\n"
            "Watch the console for any 'error:' lines that may indicate "
            "an unsupported parameter on classic GRBL (for example $133)."
        )

    def _read_from_grbl(self) -> None:
        if not self._require_connection():
            return
        self._reading_grbl = True
        self._read_buffer = {}
        self.serial_worker.send_raw("$$")
        QTimer.singleShot(1500, self._apply_read_buffer)

    def _collect_grbl_setting(self, line: str) -> None:
        if not self._reading_grbl:
            return
        match = re.match(r"^\$(\d+)\s*=\s*([\-0-9.]+)", line.strip())
        if not match:
            return
        key = int(match.group(1))
        if key in self._GRBL_KEYS:
            try:
                self._read_buffer[key] = float(match.group(2))
            except ValueError:
                pass

    def _apply_read_buffer(self) -> None:
        self._reading_grbl = False
        b = self._read_buffer
        if not b:
            QMessageBox.warning(
                self, "No Response",
                "GRBL did not return any $$ data within 1.5 s.\n"
                "Check the connection and try again."
            )
            return
        if 22 in b:
            enabled = bool(int(b[22]))
            for chk in self.home_checks.values():
                chk.setChecked(enabled)
        if 23 in b:
            mask = int(b[23])
            for i, axis in enumerate(_AXES):
                negative = bool(mask & (1 << i))
                idx = self.dir_combos[axis].findData(-1 if negative else 1)
                if idx >= 0:
                    self.dir_combos[axis].setCurrentIndex(idx)
        if 24 in b:
            self.locate_feed.setValue(int(b[24]))
        if 25 in b:
            self.seek_feed.setValue(int(b[25]))
        if 26 in b:
            self.debounce.setValue(int(b[26]))
        if 27 in b:
            for spin in self.pulloff_spins.values():
                spin.setValue(b[27])
        for grbl_key, axis in zip((130, 131, 132, 133), _AXES):
            if grbl_key in b:
                self.max_travel[axis].setValue(b[grbl_key])
        # Broadcast the parsed dict so the Jog panel's envelope spins
        # can mirror the firmware's actual current $130 / $131 / $132.
        self.grbl_settings_loaded.emit(dict(b))
        QMessageBox.information(
            self, "Read OK",
            f"Loaded {len(b)} setting(s) from GRBL."
        )

    # ------------------------------------------------------------------
    # Cycle actions
    # ------------------------------------------------------------------
    def _home_all(self) -> None:
        if not self._require_connection():
            return
        if not self._confirm("Home ALL axes?\nMake sure the path is clear."):
            return
        self.serial_worker.send_raw("$H")

    def _home_axis(self, axis: str) -> None:
        if not self._require_connection():
            return
        if not self._confirm(f"Home only {axis}?\nMake sure the path is clear."):
            return
        self.serial_worker.send_raw(f"$H{axis}")

    # ------------------------------------------------------------------
    # Work zero (G10 L20 P1) — non-destructive
    # ------------------------------------------------------------------
    def _set_work_zero(self, axes) -> None:
        if not self._require_connection():
            return
        words = " ".join(f"{a}0" for a in axes)
        self.serial_worker.send_raw(f"G10 L20 P1 {words}")

    def _goto_work_zero(self) -> None:
        if not self._require_connection():
            return
        # Lift Z first, then move to X0 Y0 in the active WCS.
        self.serial_worker.send_raw("G90")
        self.serial_worker.send_raw("G0 Z5")
        self.serial_worker.send_raw("G54 G0 X0 Y0")

    # ------------------------------------------------------------------
    # G28 predefined
    # ------------------------------------------------------------------
    def _set_g28(self) -> None:
        if not self._require_connection():
            return
        self.serial_worker.send_raw("G28.1")

    def _goto_g28(self) -> None:
        if not self._require_connection():
            return
        self.serial_worker.send_raw("G90")
        self.serial_worker.send_raw("G0 Z5")
        self.serial_worker.send_raw("G28")

    # ------------------------------------------------------------------
    # Manual machine origin (G92) — for switch-less plotters
    # ------------------------------------------------------------------
    def _manual_define_origin(self) -> None:
        if not self._require_connection():
            return
        if not self._confirm(
            "This will issue G92 X0 Y0 Z0 and treat the CURRENT machine "
            "position as the new origin. Use it only if your machine has "
            "no limit switches.\n\nProceed?"
        ):
            return
        self.serial_worker.send_raw("G92 X0 Y0 Z0")

    # ------------------------------------------------------------------
    def _require_connection(self) -> bool:
        if self.serial_worker.is_connected:
            return True
        QMessageBox.warning(
            self, "Not Connected",
            "Connect to the machine first."
        )
        return False

    def _confirm(self, message: str) -> bool:
        return QMessageBox.question(
            self, "Confirm", message,
            QMessageBox.Yes | QMessageBox.No,
        ) == QMessageBox.Yes

    # ------------------------------------------------------------------
    def _update_dro(self, pos: dict) -> None:
        for axis in _AXES:
            value = pos.get(axis)
            self._current_position[axis] = value
            text = f"{axis}: {value:.3f}" if value is not None else f"{axis}: ---"
            self.dro_labels[axis].setText(text)

    # ------------------------------------------------------------------
    # Settings persistence
    # ------------------------------------------------------------------
    def get_homing_config(self) -> dict:
        return {
            "axes": {
                axis: {
                    "enabled": self.home_checks[axis].isChecked(),
                    "direction": int(self.dir_combos[axis].currentData() or 1),
                    "pull_off": float(self.pulloff_spins[axis].value()),
                    "max_travel": float(self.max_travel[axis].value()),
                }
                for axis in _AXES
            },
            "seek_feed": int(self.seek_feed.value()),
            "locate_feed": int(self.locate_feed.value()),
            "debounce_ms": int(self.debounce.value()),
        }

    def load_homing_config(self, cfg: Optional[dict]) -> None:
        if not cfg:
            return
        axes_cfg = cfg.get("axes", {}) or {}
        for axis in _AXES:
            data = axes_cfg.get(axis, {}) or {}
            if "enabled" in data:
                self.home_checks[axis].setChecked(bool(data["enabled"]))
            if "direction" in data:
                idx = self.dir_combos[axis].findData(
                    -1 if int(data["direction"]) < 0 else 1
                )
                if idx >= 0:
                    self.dir_combos[axis].setCurrentIndex(idx)
            if "pull_off" in data:
                self.pulloff_spins[axis].setValue(float(data["pull_off"]))
            if "max_travel" in data:
                self.max_travel[axis].setValue(float(data["max_travel"]))
        if "seek_feed" in cfg:
            self.seek_feed.setValue(int(cfg["seek_feed"]))
        if "locate_feed" in cfg:
            self.locate_feed.setValue(int(cfg["locate_feed"]))
        if "debounce_ms" in cfg:
            self.debounce.setValue(int(cfg["debounce_ms"]))
