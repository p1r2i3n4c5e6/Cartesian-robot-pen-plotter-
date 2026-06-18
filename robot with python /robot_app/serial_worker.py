"""Unified worker that the GUI talks to.

Owns one of two backends (direct pyserial or cncjs Socket.IO) and
re-exports the same set of Qt signals regardless of which backend is
active.  The GUI never has to know about the implementation detail.
"""

from __future__ import annotations

import glob
import os
import re
from typing import List, Optional

from PyQt5.QtCore import QObject, pyqtSignal

from .config import CONTROLLER_USB_VIDS
from .logging_setup import get_logger
from .serial_backends.cncjs import CncjsBackend, SOCKETIO_AVAILABLE
from .serial_backends.direct import DirectSerialBackend, SERIAL_AVAILABLE

log = get_logger("worker")


# ----------------------------------------------------------------------
# Port enumeration helpers
# ----------------------------------------------------------------------

def list_serial_ports() -> List[dict]:
    """Return a sorted list of dicts describing every plausible port.

    Each entry: ``{"device","label","priority"}``.  Priority puts the
    most likely controller first (ESP32 / Arduino / FTDI).
    """
    if not SERIAL_AVAILABLE:
        return []

    import serial.tools.list_ports as ports_mod  # local import; pyserial loaded

    discovered: List[dict] = []
    seen = set()
    for p in ports_mod.comports():
        device = p.device
        if device in seen:
            continue
        seen.add(device)
        priority = 50
        if re.match(r"/dev/ttyACM\d+", device):
            priority = 10
        elif re.match(r"/dev/ttyUSB\d+", device):
            priority = 20
        elif re.match(r"COM\d+", device):
            priority = 30
        elif re.match(r"/dev/ttyS\d+", device):
            priority = 90
            desc = (p.description or "").lower()
            if desc in ("n/a", "", "ttysx"):
                continue  # skip ghost serial ports

        try:
            vid = int(p.vid) if p.vid is not None else None
        except (TypeError, ValueError):
            vid = None
        if vid in CONTROLLER_USB_VIDS:
            priority -= 5  # boost known controllers

        label = device
        desc = p.description or ""
        if desc and desc.lower() != "n/a":
            label += f"  —  {desc[:50]}"
        if p.manufacturer:
            label += f"  [{p.manufacturer}]"
        discovered.append({
            "device": device, "label": label, "priority": priority,
        })

    # Persistent /dev/serial/by-id symlinks (only on Linux)
    for link in glob.glob("/dev/serial/by-id/*"):
        try:
            target = os.path.realpath(link)
        except OSError:
            continue
        if any(d["device"] == target for d in discovered):
            continue
        name = os.path.basename(link)
        discovered.append({
            "device": target,
            "label": f"{target}  —  {name}",
            "priority": 5,  # by-id is the most stable identifier
        })

    discovered.sort(key=lambda d: (d["priority"], d["device"]))
    return discovered


def autodetect_port() -> str:
    """Return the highest-priority candidate, or empty string."""
    candidates = list_serial_ports()
    return candidates[0]["device"] if candidates else ""


# ----------------------------------------------------------------------
# Worker
# ----------------------------------------------------------------------

class SerialWorker(QObject):
    """GUI-facing facade wrapping the two backends."""

    data_received = pyqtSignal(str)
    connection_status = pyqtSignal(bool, str)
    server_status_changed = pyqtSignal(bool, str)
    machine_position_changed = pyqtSignal(dict)
    alarm_triggered = pyqtSignal(str)
    error_triggered = pyqtSignal(str)
    progress_updated = pyqtSignal(int, int)
    streaming_finished = pyqtSignal(bool, str)
    mode_changed = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self._mode = "direct"
        self._direct = DirectSerialBackend()
        self._cncjs = CncjsBackend()
        self._wire(self._direct, with_server_signal=False)
        self._wire(self._cncjs, with_server_signal=True)
        # Software-side calibration applied to every jog the
        # SerialWorker forwards.  These mirror the multipliers held
        # by the GCodeGenerator so user-driven jogs (paper setup,
        # axis nudge, etc.) physically move the requested distance
        # even when the firmware has the wrong steps/mm.  Set via
        # ``set_calibration_scales`` from MainWindow whenever the
        # calibration wizard updates the values.
        self._cal_x: float = 1.0
        self._cal_y: float = 1.0
        self._cal_z: float = 1.0

    # ------------------------------------------------------------------
    def _wire(self, backend, *, with_server_signal: bool) -> None:
        backend.data_received.connect(self.data_received)
        backend.connection_status.connect(self.connection_status)
        backend.machine_position_changed.connect(self.machine_position_changed)
        backend.alarm_triggered.connect(self.alarm_triggered)
        backend.error_triggered.connect(self.error_triggered)
        backend.progress_updated.connect(self.progress_updated)
        backend.streaming_finished.connect(self.streaming_finished)
        if with_server_signal:
            backend.server_status_changed.connect(self.server_status_changed)

    # ------------------------------------------------------------------
    @property
    def mode(self) -> str:
        return self._mode

    def set_mode(self, mode: str) -> None:
        mode = (mode or "direct").lower()
        if mode not in ("direct", "cncjs"):
            mode = "direct"
        if mode == self._mode:
            return
        # If switching while connected, drop existing connection cleanly first.
        try:
            self.disconnect_serial()
        except Exception:
            pass
        self._mode = mode
        self.mode_changed.emit(mode)
        log.info("Connection mode set to %s", mode)

    @property
    def backend(self):
        return self._direct if self._mode == "direct" else self._cncjs

    @property
    def is_connected(self) -> bool:
        return self.backend.is_connected

    @property
    def is_running(self) -> bool:
        return self.backend.is_running

    @property
    def is_paused(self) -> bool:
        return self.backend.is_paused

    @property
    def active_port(self) -> str:
        return self.backend.active_port

    @property
    def active_baudrate(self) -> int:
        return self.backend.active_baudrate

    @property
    def machine_position(self) -> dict:
        return dict(self.backend.machine_position)

    # ------------------------------------------------------------------
    # cncjs configuration pass-through (used regardless of current mode)
    # ------------------------------------------------------------------
    def configure_cncjs(self, **kwargs) -> None:
        self._cncjs.configure(**kwargs)

    @property
    def cncjs_host(self) -> str:
        return self._cncjs.host

    @property
    def cncjs_port(self) -> int:
        return self._cncjs.port

    def resolve_cncjs_command(self) -> str:
        return self._cncjs._resolve_command()

    def start_cncjs_server(self, open_browser: Optional[bool] = None) -> bool:
        return self._cncjs.start_server(open_browser=open_browser)

    def open_cncjs_browser(self) -> None:
        self._cncjs.open_browser()

    # ------------------------------------------------------------------
    # Connection
    # ------------------------------------------------------------------
    def connect_serial(self, port: str, baudrate: int) -> bool:
        if not port:
            port = autodetect_port()
        if not port:
            self.connection_status.emit(False, "No serial port available")
            return False
        return self.backend.connect(port, baudrate)

    def disconnect_serial(self) -> None:
        self.backend.disconnect()

    def shutdown(self) -> None:
        try:
            self._direct.disconnect()
        except Exception:
            pass
        try:
            self._cncjs.shutdown()
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Forwarders
    # ------------------------------------------------------------------
    def send_raw(self, command: str) -> bool:
        return self.backend.send_raw(command)

    def send_gcode(self, gcode_content: str, job_name: str = "") -> bool:
        return self.backend.send_gcode(gcode_content, job_name)

    def pause(self) -> None:
        self.backend.pause()

    def resume(self) -> None:
        self.backend.resume()

    def emergency_stop(self) -> None:
        self.backend.emergency_stop()

    def unlock_alarm(self) -> None:
        self.backend.unlock_alarm()

    def home(self) -> None:
        self.backend.home()

    def get_status(self) -> None:
        self.backend.get_status()

    def set_calibration_scales(self, x: float = 1.0, y: float = 1.0,
                               z: float = 1.0) -> None:
        """Apply the same software calibration the generator uses.

        Without this, jogs from the paper-setup panel (which use
        :meth:`jog_incremental`) bypass the generator entirely and
        the firmware's wrong steps/mm shows through — e.g. clicking
        "jog 1 mm" makes the head move 10 mm.  After calling this,
        ``jog_incremental(dx=1)`` sends ``X<1 × _cal_x>`` to the
        firmware so the head physically moves 1 mm.
        """
        def _safe(v):
            try:
                f = float(v)
            except (TypeError, ValueError):
                return 1.0
            if f <= 0:
                return 1.0
            return max(0.1, min(10.0, f))
        self._cal_x = _safe(x)
        self._cal_y = _safe(y)
        self._cal_z = _safe(z)
        log.info(
            "jog calibration scales updated: X×%.4f  Y×%.4f  Z×%.4f",
            self._cal_x, self._cal_y, self._cal_z,
        )

    def jog_incremental(self, **kwargs) -> bool:
        """Scale **distance and feed** by the active software calibration.

        When firmware ``steps_per_mm`` is wrong by a factor of S (e.g.
        firmware says 80 but actual is 8 → S=10), every move is wrong
        by S in *both* dimensions: the head goes S× too far AND moves
        at S× the requested mm/min.  Calibration must compensate for
        both — scaling only distance leaves the head over-speeding,
        which the user reported as "robot is still as fast after I
        calibrated, only the distance changed".

        For multi-axis jogs we use the smallest scale among the
        moving axes for the feed value.  This is the conservative
        choice (no axis ever runs faster than its own calibrated
        limit) and is exactly correct for the common case where the
        user calibrated all axes to the same factor.

        The A axis is left raw because it's typically a servo
        gripper / rotational axis whose ``steps_per_mm`` is unrelated
        to the X/Y/Z linear calibration.
        """
        moving_axes: list[str] = []
        if "dx" in kwargs and abs(float(kwargs["dx"])) > 1e-9:
            moving_axes.append("X")
            kwargs["dx"] = float(kwargs["dx"]) * self._cal_x
        if "dy" in kwargs and abs(float(kwargs["dy"])) > 1e-9:
            moving_axes.append("Y")
            kwargs["dy"] = float(kwargs["dy"]) * self._cal_y
        if "dz" in kwargs and abs(float(kwargs["dz"])) > 1e-9:
            moving_axes.append("Z")
            kwargs["dz"] = float(kwargs["dz"]) * self._cal_z
        # Match feed scaling to the dominant axis(es) on this jog so
        # that physical mm/min equals what the user typed in the Jog
        # panel feed-rate spinbox.
        if "feed" in kwargs and moving_axes:
            axis_scales = {"X": self._cal_x, "Y": self._cal_y, "Z": self._cal_z}
            f_scale = min(axis_scales[a] for a in moving_axes)
            try:
                feed_in = float(kwargs["feed"])
            except (TypeError, ValueError):
                feed_in = 1200.0
            scaled = max(1.0, feed_in * f_scale)
            kwargs["feed"] = int(round(scaled))
        return self.backend.jog_incremental(**kwargs)


# Re-export availability flags so panels can show user-friendly hints.
__all__ = [
    "SerialWorker", "list_serial_ports", "autodetect_port",
    "SERIAL_AVAILABLE", "SOCKETIO_AVAILABLE",
]
