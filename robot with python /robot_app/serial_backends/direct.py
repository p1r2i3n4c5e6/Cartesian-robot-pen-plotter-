"""Direct pyserial backend — talks to the GRBL controller without cncjs."""

from __future__ import annotations

import re
import threading
import time
from collections import deque
from typing import Optional

from PyQt5.QtCore import QObject, QThread, QTimer, pyqtSignal

from ..config import GRBL_ALARMS, GRBL_ERRORS
from ..logging_setup import get_logger

try:
    import serial  # type: ignore
    SERIAL_AVAILABLE = True
except ImportError:  # pragma: no cover - import guarded at runtime
    SERIAL_AVAILABLE = False
    serial = None  # type: ignore


log = get_logger("direct")


# ----------------------------------------------------------------------
# Reader thread
# ----------------------------------------------------------------------

class _SerialReader(QThread):
    """Background reader that reads complete newline-terminated lines."""

    line_received = pyqtSignal(str)
    error = pyqtSignal(str)
    closed = pyqtSignal()

    def __init__(self, ser):
        super().__init__()
        self._ser = ser
        self._stop = False

    def stop(self) -> None:
        self._stop = True

    def run(self) -> None:  # noqa: D401
        buf = bytearray()
        while not self._stop:
            try:
                data = self._ser.read(256)
            except Exception as exc:  # serial closed or USB unplugged
                self.error.emit(str(exc))
                break
            if not data:
                continue
            buf.extend(data)
            while b"\n" in buf:
                line, _, rest = buf.partition(b"\n")
                buf = bytearray(rest)
                try:
                    text = line.decode("utf-8", errors="replace").strip()
                except Exception:
                    text = ""
                if text:
                    self.line_received.emit(text)
        self.closed.emit()


# ----------------------------------------------------------------------
# Backend
# ----------------------------------------------------------------------

class DirectSerialBackend(QObject):
    """pyserial-based backend.

    The GUI connects to the public signals defined on
    :class:`robot_app.serial_worker.SerialWorker` and forwards calls
    here through a thin facade.
    """

    data_received = pyqtSignal(str)
    connection_status = pyqtSignal(bool, str)
    machine_position_changed = pyqtSignal(dict)
    alarm_triggered = pyqtSignal(str)
    error_triggered = pyqtSignal(str)
    progress_updated = pyqtSignal(int, int)
    streaming_finished = pyqtSignal(bool, str)

    GRBL_BUFFER_BYTES = 127          # GRBL serial RX buffer is 128 — leave 1 byte margin

    def __init__(self):
        super().__init__()
        self._ser = None
        self._reader: Optional[_SerialReader] = None
        self._stream_thread: Optional[threading.Thread] = None
        self._stream_lock = threading.RLock()
        self._stop_stream = threading.Event()
        self._pause_stream = threading.Event()
        # Cache of GRBL's last reported Work Coordinate Offset.  Used to
        # convert MPos status reports into WPos when the controller is
        # configured to broadcast machine coords only.
        self._wco: dict = {"X": 0.0, "Y": 0.0, "Z": 0.0, "A": 0.0}

        self.is_connected = False
        self.is_running = False
        self.is_paused = False
        self.active_port = ""
        self.active_baudrate = 115200
        self.machine_state = "Unknown"
        self.machine_position = {"X": None, "Y": None, "Z": None, "A": None}
        self._grbl_version = ""
        self._oks_pending = deque()         # bytes-per-pending-line, for character-counting protocol
        self._ok_event = threading.Event()
        self._send_lock = threading.RLock()
        # One-shot Hold notification flag — re-armed when GRBL leaves Hold.
        self._hold_announced = False

        # Heartbeat polling (live DRO without user pressing ?)
        self._status_timer = QTimer(self)
        self._status_timer.setInterval(250)
        self._status_timer.timeout.connect(self._heartbeat)

    # ------------------------------------------------------------------
    # Connection
    # ------------------------------------------------------------------
    def connect(self, port: str, baudrate: int) -> bool:
        if not SERIAL_AVAILABLE:
            self.connection_status.emit(
                False, "pyserial not installed (pip install pyserial)"
            )
            return False
        if not port:
            self.connection_status.emit(False, "No serial port selected")
            return False

        with self._send_lock:
            self.disconnect()
            try:
                self._ser = serial.Serial(
                    port=port,
                    baudrate=baudrate,
                    bytesize=serial.EIGHTBITS,
                    parity=serial.PARITY_NONE,
                    stopbits=serial.STOPBITS_ONE,
                    timeout=0.1,
                    write_timeout=2,
                )
            except (serial.SerialException, OSError) as exc:
                msg = self._explain_open_error(port, exc)
                self.connection_status.emit(False, msg)
                log.error("Serial open failed: %s", msg)
                return False

            # Some boards reset on DTR — wait for grbl banner
            time.sleep(2.0)
            self._ser.reset_input_buffer()
            self._ser.reset_output_buffer()

            self.active_port = port
            self.active_baudrate = baudrate
            self.is_connected = True

            self._reader = _SerialReader(self._ser)
            self._reader.line_received.connect(self._on_line)
            self._reader.error.connect(self._on_reader_error)
            self._reader.closed.connect(self._on_reader_closed)
            self._reader.start()

            self._status_timer.start()
            log.info("Direct serial connected: %s @ %s", port, baudrate)
            self.connection_status.emit(
                True, f"Direct serial: {port} @ {baudrate}"
            )

            # Soft reset + wake-up
            QTimer.singleShot(150, lambda: self.send_raw("?"))
            QTimer.singleShot(400, lambda: self.send_raw("$$"))
            return True

    def disconnect(self) -> None:
        with self._send_lock:
            self._stop_stream.set()
            self.is_running = False
            self.is_paused = False
            self._status_timer.stop()
            if self._reader:
                self._reader.stop()
                if self._reader.isRunning():
                    self._reader.wait(1500)
                self._reader = None
            if self._ser is not None:
                try:
                    self._ser.close()
                except Exception:
                    pass
                self._ser = None
            if self.is_connected:
                self.is_connected = False
                self.connection_status.emit(
                    False, f"Disconnected from {self.active_port}"
                )

    # ------------------------------------------------------------------
    # Sending
    # ------------------------------------------------------------------
    def send_raw(self, command: str) -> bool:
        if not self.is_connected or self._ser is None:
            return False
        text = (command or "").rstrip("\r\n")
        if not text:
            return False
        try:
            if text == "?":
                self._ser.write(b"?")
                return True
            if text == "!":
                self._ser.write(b"!")
                self.is_paused = True
                self._pause_stream.set()
                return True
            if text == "~":
                self._ser.write(b"~")
                self.is_paused = False
                self._pause_stream.clear()
                return True
            if text == "\x18":
                self._ser.write(b"\x18")
                self._oks_pending.clear()
                return True
            payload = (text + "\n").encode("utf-8")
            self._ser.write(payload)
            self._oks_pending.append(len(payload))
            self.data_received.emit(f"> {text}")
            return True
        except Exception as exc:
            self.error_triggered.emit(str(exc))
            return False

    def send_gcode(self, gcode_content: str, job_name: str = "") -> bool:
        if not self.is_connected:
            self.data_received.emit("⚠ Cannot stream — not connected")
            return False
        if self.is_running:
            self.data_received.emit("⚠ A job is already running")
            return False
        lines = [l.strip() for l in gcode_content.splitlines() if l.strip()]
        if not lines:
            return False

        self._stop_stream.clear()
        self._pause_stream.clear()
        self.is_running = True
        self.is_paused = False
        self._hold_announced = False
        self.progress_updated.emit(0, len(lines))

        self._stream_thread = threading.Thread(
            target=self._stream_worker,
            args=(lines, job_name or "job"),
            daemon=True, name="grbl-stream",
        )
        self._stream_thread.start()
        return True

    # ------------------------------------------------------------------
    # Stream worker (character-counting GRBL protocol)
    # ------------------------------------------------------------------
    def _stream_worker(self, lines, job_name) -> None:
        total = len(lines)
        sent = 0
        log.info("Streaming %d lines (%s)", total, job_name)
        try:
            for idx, line in enumerate(lines, start=1):
                if self._stop_stream.is_set():
                    break
                while self._pause_stream.is_set():
                    time.sleep(0.05)
                    if self._stop_stream.is_set():
                        break
                if self._stop_stream.is_set():
                    break
                if line.startswith(";") or line.startswith("("):
                    continue
                payload = (line + "\n").encode("utf-8")
                # Wait until the controller buffer has room
                wait_deadline = time.time() + 30
                while (
                    sum(self._oks_pending) + len(payload)
                    > self.GRBL_BUFFER_BYTES
                ):
                    if self._stop_stream.is_set() or time.time() > wait_deadline:
                        break
                    time.sleep(0.01)
                with self._send_lock:
                    if self._ser is None:
                        break
                    try:
                        self._ser.write(payload)
                    except Exception as exc:
                        self.error_triggered.emit(str(exc))
                        break
                    self._oks_pending.append(len(payload))
                sent += 1
                self.progress_updated.emit(sent, total)
            # Drain remaining ok responses with a short timeout
            drain_deadline = time.time() + 10
            while self._oks_pending and time.time() < drain_deadline:
                time.sleep(0.05)
        finally:
            success = not self._stop_stream.is_set()
            self.is_running = False
            self.is_paused = False
            message = "Job finished" if success else "Stopped by user"
            self.streaming_finished.emit(success, message)

    # ------------------------------------------------------------------
    # Standard control commands
    # ------------------------------------------------------------------
    def pause(self) -> None:
        if self.is_connected:
            self.send_raw("!")

    def resume(self) -> None:
        if self.is_connected:
            self.send_raw("~")

    def emergency_stop(self) -> None:
        # Tell the streaming worker to abort, then send the GRBL real-time
        # soft-reset.  Critically we MUST clear our pending-ok accounting
        # because GRBL flushes its serial buffer on 0x18 — if we don't
        # zero the deque, the *next* job will deadlock waiting for okays
        # that will never come.
        self._stop_stream.set()
        self.is_running = False
        self.is_paused = False
        with self._send_lock:
            self._oks_pending.clear()
            if self.is_connected and self._ser is not None:
                try:
                    self._ser.write(b"\x18")
                except Exception:
                    pass
        self.data_received.emit("↑ SOFT RESET (0x18)")
        # GRBL needs ~1 s to re-emit its banner; nudge with a status query
        QTimer.singleShot(1100, lambda: self.is_connected and self.send_raw("?"))

    def unlock_alarm(self) -> None:
        if not self.is_connected or self._ser is None:
            return
        # Some boards need a flush before $X is accepted while in ALARM
        # state.  Bypass the deque so the byte limit can never gate it.
        with self._send_lock:
            try:
                self._ser.write(b"$X\n")
                self._oks_pending.append(len(b"$X\n"))
                self.data_received.emit("> $X (clear alarm)")
            except Exception as exc:
                self.error_triggered.emit(f"Unlock failed: {exc}")

    def home(self) -> None:
        if self.is_connected:
            self.send_raw("$H")

    def get_status(self) -> None:
        if self.is_connected:
            self.send_raw("?")

    def jog_incremental(self, dx=0.0, dy=0.0, dz=0.0, da=0.0, feed=1200) -> bool:
        """Queue a relative-mode jog using ``G91 G0`` instead of ``$J=``.

        ``$J=`` is GRBL's interactive jog command — when a new ``$J=``
        arrives while one is in flight, the previous jog is *cancelled*
        mid-motion.  That meant the user clicking ``+X`` rapidly 12
        times (12 × 50 mm = 600 mm intended) only ever physically
        moved ~90 mm, because every click cancelled the previous
        before it finished accelerating.

        Switching to ``G91 G0 ... F<feed>`` (followed by ``G90`` to
        restore absolute mode) makes each jog a regular move that
        queues into the planner buffer.  Successive clicks now run
        back-to-back instead of cancelling each other, and the user
        gets the total accumulated distance they asked for.
        """
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
        # Send G91 / G0 / G90 as THREE separate lines, exactly matching
        # the calibration-wizard JOG path that the user reports works
        # correctly.  Some GRBL builds parse modal-group changes on the
        # SAME line as a motion ambiguously — splitting eliminates
        # that variable entirely so the user gets identical behaviour
        # whether they jog from the Calibration wizard or the regular
        # Jog panel.
        log.info(
            "jog→ axes=%s feed=%d (G91/G0/G90 split mode)",
            " ".join(axes), feed_value,
        )
        ok1 = self.send_raw("G91")
        ok2 = self.send_raw(f"G0 {' '.join(axes)} F{feed_value}")
        ok3 = self.send_raw("G90")
        return bool(ok1 and ok2 and ok3)

    # ------------------------------------------------------------------
    # Inbound line parsing
    # ------------------------------------------------------------------
    def _on_line(self, line: str) -> None:
        self.data_received.emit(line)
        if line == "ok" or line.startswith("ok"):
            if self._oks_pending:
                self._oks_pending.popleft()
            self._ok_event.set()
            return
        if line.startswith("<") and line.endswith(">"):
            self._parse_status_report(line)
            return
        if line.startswith("Grbl") or line.startswith("GrblHAL"):
            self._grbl_version = line
            return
        if line.startswith("ALARM:"):
            code = line.replace("ALARM:", "").strip()
            try:
                msg = GRBL_ALARMS.get(int(code), f"Unknown alarm {code}")
            except ValueError:
                msg = f"Alarm: {code}"
            self.alarm_triggered.emit(msg)
            return
        if line.startswith("error:"):
            code = line.replace("error:", "").strip()
            try:
                msg = GRBL_ERRORS.get(int(code), f"Error code {code}")
            except ValueError:
                msg = f"Error: {code}"
            self.error_triggered.emit(msg)
            # Pop a pending ok so the buffer accounting recovers
            if self._oks_pending:
                self._oks_pending.popleft()
            return

    def _parse_status_report(self, line: str) -> None:
        """Parse <...> status reports and emit WORK-COORD positions.

        GRBL ships either ``MPos:`` (machine coords) or ``WPos:`` (work
        coords) per the ``$10`` setting plus an occasional ``WCO:``
        (work-coord offset).  The canvas + g-code generator always speak
        in work coords — so the glider must track WPos.  When only MPos
        is reported we cache the most recent WCO and subtract it.
        """
        body = line[1:-1]
        parts = body.split("|")
        if not parts:
            return
        self.machine_state = parts[0] or self.machine_state
        mpos: dict = {}
        wpos: dict = {}
        for part in parts[1:]:
            if ":" not in part:
                continue
            key, value = part.split(":", 1)
            if key not in ("MPos", "WPos", "WCO"):
                continue
            coords = [t.strip() for t in value.split(",")]
            target = (mpos if key == "MPos"
                      else wpos if key == "WPos"
                      else self._wco)
            for axis, tok in zip(("X", "Y", "Z", "A"), coords):
                try:
                    target[axis] = float(tok)
                except ValueError:
                    continue
        # WPos wins if reported, else derive from MPos - WCO
        if wpos:
            for axis, v in wpos.items():
                self.machine_position[axis] = v
        elif mpos:
            for axis, v in mpos.items():
                self.machine_position[axis] = v - self._wco.get(axis, 0.0)
        payload = dict(self.machine_position)
        payload["state"] = self.machine_state
        self.machine_position_changed.emit(payload)
        # Surface a clear console hint exactly once when GRBL drops into
        # Hold mid-stream and the user did NOT explicitly pause.  This is
        # the symptom the user reported as "stream starts then stuck on
        # Hold" — the stream worker has no way to recover from external
        # holds (door switch, smart-buffer pause, ...).  Telling them in
        # plain English is the difference between "frozen app" and "oh,
        # I just need to click Resume".
        in_hold = self.machine_state.startswith("Hold")
        if in_hold and self.is_running and not self.is_paused:
            if not self._hold_announced:
                self._hold_announced = True
                self.data_received.emit(
                    "⏸ Machine entered HOLD mid-stream. "
                    "Click Resume (~) on the Safety panel to continue, "
                    "or Emergency Stop to abort."
                )
        elif not in_hold and self._hold_announced:
            self._hold_announced = False
            self.data_received.emit("▶ Machine resumed")

    def _on_reader_error(self, msg: str) -> None:
        self.error_triggered.emit(f"Serial reader: {msg}")
        self.disconnect()

    def _on_reader_closed(self) -> None:
        # Reader thread exited — make sure UI knows
        if self.is_connected:
            self.disconnect()

    def _heartbeat(self) -> None:
        if not self.is_connected or self._ser is None:
            return
        # Try to grab the send lock without blocking; if a stream worker
        # is mid-burst we simply skip this status query and try again on
        # the next 250ms tick.
        if not self._send_lock.acquire(blocking=False):
            return
        try:
            self._ser.write(b"?")
        except Exception:
            pass
        finally:
            self._send_lock.release()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _explain_open_error(port: str, exc: BaseException) -> str:
        text = str(exc)
        if "Permission denied" in text or "PermissionError" in text:
            return (
                f"Permission denied opening {port}. "
                f"Run: sudo usermod -aG dialout $USER  (then log out/in)"
            )
        if "Resource busy" in text or "could not open port" in text.lower():
            return (
                f"{port} is busy. Another app (cncjs / Arduino IDE / "
                f"another instance) is holding it. Close it first."
            )
        if "No such file" in text or "FileNotFoundError" in text:
            return f"{port} does not exist. Plug in the controller."
        return f"Open {port} failed: {text}"
