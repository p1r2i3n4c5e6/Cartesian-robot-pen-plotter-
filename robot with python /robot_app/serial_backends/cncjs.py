"""cncjs Socket.IO backend.

Routes the GRBL traffic through a local cncjs server so the user can
also see the job in the cncjs browser UI.  Keeps the same public API
as :class:`DirectSerialBackend`.

Notes on the protocol fix that was wrong in robot2.py:

cncjs `socketio` events expect *positional* arguments — never tuples.
Correct calls:

    sio.emit("open",  port, {...})
    sio.emit("close", port)
    sio.emit("write", port, payload, context)
    sio.emit("command", port, name, *args)

Tuple-style emits silently no-op on cncjs >= 1.10.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import threading
import time
import webbrowser
from typing import Optional
from urllib.error import URLError
from urllib.parse import urljoin
from urllib.request import Request, urlopen

from PyQt5.QtCore import QObject, Qt, QTimer, pyqtSignal, pyqtSlot

from ..config import GRBL_ALARMS, GRBL_ERRORS
from ..logging_setup import get_logger

try:
    import socketio as socketio_client  # type: ignore
    SOCKETIO_AVAILABLE = True
except ImportError:  # pragma: no cover - import guard at runtime
    socketio_client = None  # type: ignore
    SOCKETIO_AVAILABLE = False


log = get_logger("cncjs")


class CncjsBackend(QObject):
    """Socket.IO bridge to a local cncjs instance.

    The class can also start (and later kill) the cncjs subprocess when
    requested.
    """

    data_received = pyqtSignal(str)
    connection_status = pyqtSignal(bool, str)
    server_status_changed = pyqtSignal(bool, str)
    machine_position_changed = pyqtSignal(dict)
    alarm_triggered = pyqtSignal(str)
    error_triggered = pyqtSignal(str)
    progress_updated = pyqtSignal(int, int)
    streaming_finished = pyqtSignal(bool, str)
    # Internal bridge: socket.io callbacks fire on a background event-loop
    # thread, but QTimer / Socket.IO writes need to happen on the Qt main
    # thread.  Emitting this signal hops execution back to the slot via
    # Qt.QueuedConnection, fixing the "gcode:start never fires" bug.
    _request_start_sender = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.host = "127.0.0.1"
        self.port = 8000
        self.controller_type = "Grbl"
        self.token = ""
        self.username = ""
        self.password = ""
        self.command_path = ""
        self.watch_directory = ""
        self.auto_open_browser = False

        self._socket: Optional["socketio_client.Client"] = None
        self._socket_connected = False
        self._session_token = ""
        self._authenticated_user = ""
        self._browser_opened = False
        self._cncjs_process: Optional[subprocess.Popen] = None
        self._started_cncjs = False
        self._server_lock = threading.RLock()
        # Cached WCO so MPos→WPos conversion still works when GRBL
        # only sends machine coords for most reports.
        self._wco: dict = {"X": 0.0, "Y": 0.0, "Z": 0.0, "A": 0.0}

        self.is_connected = False
        self.is_running = False
        self.is_paused = False
        self.active_port = ""
        self.active_baudrate = 115200
        self.machine_state = "Unknown"
        self.machine_position = {"X": None, "Y": None, "Z": None, "A": None}
        self._job_finished = False
        self._stop_requested = False
        # workflow:state tracking — only treat 'idle' as job-finished if
        # we previously observed the workflow actually run.  cncjs emits
        # an initial 'idle' immediately after gcode:load (before our
        # gcode:start), and the old code mistook that for completion,
        # which is part of why streaming appeared to "stop" right after
        # starting.
        self._workflow_seen_running = False
        # Track whether we already logged a "machine entered Hold" hint
        # so the console isn't spammed by every 250 ms status report.
        self._hold_announced = False
        # Hop-to-main-thread plumbing for the gcode:start dispatch.  We
        # connect with QueuedConnection so the slot runs on the thread
        # that constructed this QObject (the Qt main thread).
        self._request_start_sender.connect(
            self._main_thread_start_sender, Qt.QueuedConnection
        )

    # ------------------------------------------------------------------
    # Configuration
    # ------------------------------------------------------------------
    def configure(self, *, host, port, controller_type="Grbl",
                  token="", username="", password="",
                  command_path="", watch_directory="",
                  auto_open_browser=False):
        self.host = (host or "127.0.0.1").strip()
        self.port = int(port or 8000)
        self.controller_type = controller_type or "Grbl"
        self.token = (token or "").strip()
        self.username = (username or "").strip()
        self.password = password or ""
        self.command_path = (command_path or "").strip()
        self.watch_directory = str(watch_directory or "")
        self.auto_open_browser = bool(auto_open_browser)

    @property
    def server_url(self) -> str:
        return f"http://{self.host}:{self.port}/"

    # ------------------------------------------------------------------
    # cncjs server lifecycle
    # ------------------------------------------------------------------
    def _server_http_ready(self) -> bool:
        try:
            with urlopen(self.server_url, timeout=2):
                return True
        except (URLError, OSError, ValueError):
            return False

    def _resolve_command(self) -> str:
        candidates = []
        if self.command_path:
            candidates.append(self.command_path)
        for name in ("cncjs", "cnc"):
            found = shutil.which(name)
            if found:
                candidates.append(found)
        for c in candidates:
            if not c:
                continue
            if os.path.isabs(c):
                if os.path.exists(c):
                    return c
            elif shutil.which(c):
                return c
        return ""

    def start_server(self, open_browser: Optional[bool] = None) -> bool:
        if open_browser is None:
            open_browser = self.auto_open_browser
        with self._server_lock:
            if self._server_http_ready():
                self.server_status_changed.emit(
                    True, f"cncjs ready at {self.server_url}"
                )
                if open_browser:
                    self.open_browser()
                return True
            command = self._resolve_command()
            if not command:
                self.server_status_changed.emit(
                    False,
                    "cncjs command not found — install with: "
                    "npm install -g cncjs",
                )
                return False
            launch = [command,
                      "--host", self.host,
                      "--port", str(self.port),
                      "--controller", self.controller_type]
            if self.watch_directory:
                launch.extend(["--watch-directory", self.watch_directory])
            try:
                self._cncjs_process = subprocess.Popen(
                    launch,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                )
                self._started_cncjs = True
                log.info("Started cncjs subprocess pid=%s", self._cncjs_process.pid)
            except Exception as exc:
                self.server_status_changed.emit(
                    False, f"Failed to start cncjs: {exc}"
                )
                return False

            deadline = time.time() + 25
            while time.time() < deadline:
                if self._cncjs_process.poll() is not None:
                    break
                if self._server_http_ready():
                    self.server_status_changed.emit(
                        True,
                        f"cncjs running at {self.server_url} "
                        f"(PID {self._cncjs_process.pid})",
                    )
                    if open_browser:
                        self.open_browser()
                    return True
                time.sleep(0.4)
            exit_code = self._cncjs_process.poll()
            self.server_status_changed.emit(
                False,
                f"cncjs did not become ready (exit={exit_code})",
            )
            return False

    def open_browser(self) -> None:
        if self._browser_opened:
            return
        try:
            webbrowser.open(self.server_url, new=1, autoraise=True)
            self._browser_opened = True
        except Exception as exc:
            self.data_received.emit(f"⚠ Could not open browser: {exc}")

    def stop_server(self) -> None:
        if not (self._started_cncjs and self._cncjs_process
                and self._cncjs_process.poll() is None):
            return
        try:
            os.killpg(os.getpgid(self._cncjs_process.pid), signal.SIGTERM)
        except Exception:
            try:
                self._cncjs_process.terminate()
            except Exception:
                pass
        try:
            self._cncjs_process.wait(timeout=5)
        except Exception:
            try:
                os.killpg(os.getpgid(self._cncjs_process.pid), signal.SIGKILL)
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Authentication / Socket.IO setup
    # ------------------------------------------------------------------
    def _signin(self) -> bool:
        if self._session_token:
            return True
        attempts = []
        if self.token:
            attempts.append({"token": self.token})
        if self.username and self.password:
            attempts.append({"name": self.username, "password": self.password})
        if not attempts:
            attempts.append({})

        last_error = None
        for payload in attempts:
            data = json.dumps(payload).encode("utf-8")
            req = Request(
                urljoin(self.server_url, "api/signin"),
                data=data,
                headers={"Content-Type": "application/json",
                         "Accept": "application/json"},
                method="POST",
            )
            try:
                with urlopen(req, timeout=8) as resp:
                    body = json.loads(resp.read().decode("utf-8"))
            except Exception as exc:
                last_error = exc
                continue
            tok = body.get("token", "")
            if not tok:
                last_error = "No token returned"
                continue
            self._session_token = tok
            self._authenticated_user = body.get("name", "")
            self.server_status_changed.emit(
                True, f"Authenticated with cncjs as "
                f"{self._authenticated_user or 'guest'}",
            )
            return True
        if last_error:
            self.data_received.emit(f"⚠ cncjs signin: {last_error}")
        return False

    def _install_handlers(self) -> None:
        sio = self._socket
        if sio is None:
            return

        @sio.event
        def connect():
            self._socket_connected = True
            self.server_status_changed.emit(
                True, f"Socket.IO connected ({self.server_url})"
            )
            try:
                sio.emit("list")
            except Exception:
                pass

        @sio.event
        def disconnect():
            self._socket_connected = False
            was_connected = self.is_connected
            self.is_connected = False
            self.is_running = False
            self.is_paused = False
            self.server_status_changed.emit(False, "Socket.IO disconnected")
            if was_connected:
                self.connection_status.emit(False, "Machine disconnected")

        @sio.on("serialport:open")
        def on_open(opts):
            opts = opts or {}
            self.active_port = (opts.get("port") or opts.get("path")
                                or self.active_port)
            self.active_baudrate = opts.get("baudrate", self.active_baudrate)
            self.is_connected = True
            self.connection_status.emit(
                True,
                f"cncjs connected: {self.active_port} @ {self.active_baudrate}",
            )

        @sio.on("serialport:close")
        def on_close(opts):
            opts = opts or {}
            port = opts.get("port") or opts.get("path") or self.active_port
            self.is_connected = False
            self.is_running = False
            self.is_paused = False
            self.connection_status.emit(False, f"Disconnected from {port}")

        @sio.on("serialport:error")
        def on_error(payload):
            payload = payload or {}
            err = payload.get("err", payload)
            port = payload.get("port", self.active_port)
            self.error_triggered.emit(str(err))
            self.data_received.emit(f"⚠ cncjs serial error on {port}: {err}")

        @sio.on("serialport:read")
        def on_read(data):
            text = "" if data is None else str(data)
            for raw in text.splitlines() or [text]:
                line = raw.strip()
                if not line:
                    continue
                self.data_received.emit(line)
                if line.startswith("<") and line.endswith(">"):
                    self._parse_status(line)
                elif line.startswith("ALARM:"):
                    code = line.replace("ALARM:", "").strip()
                    try:
                        msg = GRBL_ALARMS.get(int(code), f"Alarm {code}")
                    except ValueError:
                        msg = f"Alarm: {code}"
                    self.alarm_triggered.emit(msg)
                elif line.startswith("error:"):
                    code = line.replace("error:", "").strip()
                    try:
                        msg = GRBL_ERRORS.get(int(code), f"Error {code}")
                    except ValueError:
                        msg = f"Error: {code}"
                    self.error_triggered.emit(msg)

        @sio.on("serialport:write")
        def on_write(*args):
            if not args:
                return
            data = args[0]
            if isinstance(data, dict):
                return
            text = "" if data is None else str(data).rstrip()
            if text:
                self.data_received.emit(f"> {text}")

        @sio.on("sender:status")
        def on_sender_status(status):
            status = status or {}
            total = int(status.get("total") or 0)
            sent = int(status.get("sent") or status.get("received") or 0)
            # Once we've finished, cncjs keeps re-broadcasting status with
            # total=0 — ignore those, otherwise the console progress bar
            # would visibly snap back to 0 % the moment it hits 100 %.
            if total > 0 and not self._job_finished:
                self.progress_updated.emit(min(sent, total), total)
            if (self.is_running and status.get("finishTime")
                    and not self._job_finished):
                self._job_finished = True
                self.is_running = False
                self.is_paused = False
                # Force the bar to show full completion before notifying
                if total > 0:
                    self.progress_updated.emit(total, total)
                self.streaming_finished.emit(
                    not self._stop_requested,
                    "Completed cncjs job",
                )
                self._stop_requested = False

        @sio.on("workflow:state")
        def on_workflow(state):
            value = (state.get("state") if isinstance(state, dict) else str(state)) or ""
            value = value.lower()
            if value == "paused":
                self.is_paused = True
            elif value == "running":
                self.is_running = True
                self.is_paused = False
                self._workflow_seen_running = True
            elif value in ("idle", "stopped"):
                # Only treat 'idle' as completion if we ACTUALLY saw the
                # workflow run, otherwise the initial idle that cncjs
                # emits right after gcode:load (before our gcode:start)
                # would fire a premature streaming_finished and snap the
                # progress bar / state machine into a confused state.
                if (self._workflow_seen_running or self._stop_requested) \
                        and (self.is_running or self._stop_requested) \
                        and not self._job_finished:
                    self._job_finished = True
                    success = not self._stop_requested
                    self.is_running = False
                    self.is_paused = False
                    self._workflow_seen_running = False
                    self.streaming_finished.emit(
                        success,
                        "Completed cncjs job" if success else "Stopped by user",
                    )
                    self._stop_requested = False

    def _ensure_socket(self) -> bool:
        if not SOCKETIO_AVAILABLE:
            self.server_status_changed.emit(
                False,
                "python-socketio missing — pip install 'python-socketio[client]'",
            )
            return False
        if self._socket and self._socket_connected:
            return True
        if not self.start_server(open_browser=self.auto_open_browser):
            return False
        if not self._signin():
            return False
        if self._socket:
            try:
                self._socket.disconnect()
            except Exception:
                pass
        self._socket = socketio_client.Client(
            reconnection=True, logger=False, engineio_logger=False,
        )
        self._install_handlers()
        url = self.server_url.rstrip("/")
        if self._session_token:
            url = f"{url}?token={self._session_token}"
        try:
            self._socket.connect(
                url, transports=["websocket"], wait=True, wait_timeout=10
            )
            return True
        except Exception as exc:
            self.server_status_changed.emit(
                False, f"Could not connect Socket.IO: {exc}"
            )
            return False

    # ------------------------------------------------------------------
    # Public API mirrors DirectSerialBackend
    # ------------------------------------------------------------------
    def connect(self, port: str, baudrate: int) -> bool:
        if not self._ensure_socket():
            return False
        if self.is_connected:
            self.disconnect()
            time.sleep(0.2)
        self.active_port = port
        self.active_baudrate = baudrate
        try:
            self._socket.emit(
                "open", port,
                {"baudrate": baudrate, "controllerType": self.controller_type},
            )
        except Exception as exc:
            self.connection_status.emit(False, f"open failed: {exc}")
            return False
        deadline = time.time() + 10
        while time.time() < deadline:
            if self.is_connected:
                return True
            time.sleep(0.1)
        self.connection_status.emit(False, f"Timeout opening {port} via cncjs")
        return False

    def disconnect(self) -> None:
        if self._socket and self._socket_connected and self.active_port:
            try:
                self._socket.emit("close", self.active_port)
            except Exception:
                pass
        self.is_connected = False
        self.is_running = False
        self.is_paused = False

    def shutdown(self) -> None:
        try:
            self.disconnect()
        except Exception:
            pass
        try:
            if self._socket and self._socket_connected:
                self._socket.disconnect()
        except Exception:
            pass
        self.stop_server()

    @pyqtSlot()
    def _main_thread_start_sender(self) -> None:
        """Run on the Qt main thread (via QueuedConnection).
        Actually fires the cncjs ``gcode:start`` command and clears any
        leftover Hold state with a real-time cycle-start byte.  This is
        what the load_ack callback used to attempt with QTimer — but
        that approach silently no-op'd because load_ack runs on the
        socketio background thread which has no Qt event loop.
        """
        if self._stop_requested or not self.is_running:
            # Job was cancelled before the sender could even start.
            return
        if not (self._socket and self._socket_connected and self.active_port):
            self.is_running = False
            self._job_finished = True
            self.streaming_finished.emit(False, "cncjs disconnected before start")
            return
        # Belt-and-braces: send a real-time cycle-start so any leftover
        # Hold state from a previous session does not silently swallow
        # the first move of the new job.
        try:
            self._socket.emit(
                "write", self.active_port, "~",
                {"source": "robot.py:precycle"},
            )
        except Exception:
            pass
        if not self._emit_command("gcode:start"):
            self.is_running = False
            self._job_finished = True
            self.streaming_finished.emit(False, "Could not start cncjs sender")
            return
        self.data_received.emit("▶ Started cncjs sender")

    def _emit_command(self, name: str, *args, callback=None) -> bool:
        if not (self._socket and self._socket_connected and self.active_port):
            return False
        try:
            if callback:
                self._socket.emit(
                    "command", self.active_port, name, *args, callback=callback
                )
            else:
                self._socket.emit("command", self.active_port, name, *args)
            return True
        except Exception as exc:
            self.data_received.emit(f"⚠ cncjs command failed: {exc}")
            return False

    def send_raw(self, command: str) -> bool:
        if not self.is_connected:
            return False
        text = (command or "").rstrip("\r\n")
        if not text:
            return False
        if text == "?":
            return self._emit_command("statusreport")
        if text == "!":
            self.is_paused = True
            return self._emit_command("feedhold")
        if text == "~":
            self.is_paused = False
            return self._emit_command("cyclestart")
        if text == "\x18":
            return self._emit_command("reset")
        try:
            self._socket.emit(
                "write", self.active_port, text + "\n",
                {"source": "robot.py"},
            )
            return True
        except Exception as exc:
            self.data_received.emit(f"⚠ Write failed: {exc}")
            return False

    def send_gcode(self, gcode_content: str, job_name: str = "") -> bool:
        if not self.is_connected:
            self.data_received.emit("⚠ Cannot stream — not connected")
            return False
        if self.is_running:
            self.data_received.emit("⚠ cncjs already running a job")
            return False
        lines = [l for l in gcode_content.splitlines() if l.strip()]
        if not lines:
            return False
        self.is_running = True
        self.is_paused = False
        self._stop_requested = False
        self._job_finished = False
        self._workflow_seen_running = False
        self._hold_announced = False
        self.progress_updated.emit(0, len(lines))
        if not job_name:
            job_name = "drawing.gcode"

        def load_ack(*args):
            # NOTE: this callback runs on python-socketio's BACKGROUND
            # event-loop thread, NOT the Qt main thread.  We must not
            # touch QTimer / QObject methods directly here — emitting a
            # Qt signal is the only thread-safe way to schedule work on
            # the main thread.
            if args and args[0]:
                err_msg = str(args[0])
                self.is_running = False
                self._job_finished = True
                self.error_triggered.emit(err_msg)
                self.streaming_finished.emit(False, f"Load failed: {err_msg}")
                return
            self.data_received.emit(f"✅ Loaded {job_name}")
            # Hop to the main thread to actually kick off the sender.
            self._request_start_sender.emit()

        try:
            self._socket.emit(
                "command", self.active_port, "gcode:load",
                job_name, gcode_content,
                {"name": job_name, "source": "robot.py"},
                callback=load_ack,
            )
            self.data_received.emit(
                f"📤 Uploading {job_name} ({len(lines)} lines)..."
            )
            return True
        except Exception as exc:
            self.is_running = False
            self.streaming_finished.emit(False, f"Send failed: {exc}")
            return False

    def pause(self) -> None:
        if self.is_connected:
            self.is_paused = True
            if not self._emit_command("gcode:pause"):
                self._emit_command("feedhold")

    def resume(self) -> None:
        if self.is_connected:
            self.is_paused = False
            if not self._emit_command("gcode:resume"):
                self._emit_command("cyclestart")

    def emergency_stop(self) -> None:
        self._stop_requested = True
        self.is_running = False
        self.is_paused = False
        if not self.is_connected:
            return
        # cncjs needs gcode:stop FIRST so the sender disengages, then
        # reset to actually halt GRBL.  Calling reset before stop leaves
        # the sender in a half-armed state where the next job won't run.
        self._emit_command("gcode:stop", {"force": True})
        self._emit_command("reset")
        # Belt-and-braces: also send a raw 0x18 so soft-reset reaches GRBL
        # even if cncjs swallowed the high-level command.
        try:
            self._socket.emit(
                "write", self.active_port, "\x18",
                {"source": "robot.py"},
            )
        except Exception:
            pass
        self.data_received.emit("↑ ESTOP → cncjs gcode:stop + reset")

    def unlock_alarm(self) -> None:
        if not self.is_connected:
            return
        # The high-level cncjs `unlock` command sometimes fails when
        # GRBL is in a freshly-reset ALARM state.  Send the raw `$X` as
        # a fallback so the user always gets out of ALARM with one click.
        if not self._emit_command("unlock"):
            try:
                self._socket.emit(
                    "write", self.active_port, "$X\n",
                    {"source": "robot.py"},
                )
            except Exception as exc:
                self.error_triggered.emit(f"Unlock failed: {exc}")
        self.data_received.emit("> $X (cncjs unlock)")

    def home(self) -> None:
        if self.is_connected:
            self._emit_command("homing")

    def get_status(self) -> None:
        if self.is_connected:
            self._emit_command("statusreport")

    def jog_incremental(self, dx=0.0, dy=0.0, dz=0.0, da=0.0, feed=1200) -> bool:
        """See ``DirectSerialBackend.jog_incremental`` for the rationale.

        ``$J=`` was cancelling in-flight jogs whenever the user clicked
        a jog button rapidly, so rapid clicks ended up moving ~90 mm
        instead of the intended 600 mm.  Switching to ``G91 G0 ...``
        (queued through the planner) fixes that.
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
            f = max(int(feed), 1)
        except (TypeError, ValueError):
            f = 1200
        # 3-line split (G91 / G0 / G90) — matches the calibration
        # wizard exactly.  See DirectSerialBackend for why combined
        # `G91 G0 ...` is unsafe on some GRBL forks.
        ok1 = self.send_raw("G91")
        ok2 = self.send_raw(f"G0 {' '.join(axes)} F{f}")
        ok3 = self.send_raw("G90")
        return bool(ok1 and ok2 and ok3)

    # ------------------------------------------------------------------
    def _parse_status(self, line: str) -> None:
        """Parse cncjs-forwarded GRBL status report and emit WORK coords.

        Mirrors the direct backend: prefer WPos, otherwise compute it as
        ``MPos - WCO`` so the canvas glider always shows where the pen
        actually is in the user's drawing reference frame.
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
        if wpos:
            for axis, v in wpos.items():
                self.machine_position[axis] = v
        elif mpos:
            for axis, v in mpos.items():
                self.machine_position[axis] = v - self._wco.get(axis, 0.0)
        payload = dict(self.machine_position)
        payload["state"] = self.machine_state
        self.machine_position_changed.emit(payload)
        # Hold visibility: surface a clear console hint exactly once when
        # the controller drops into Hold while we believe the job should
        # be running.  No spam — we re-arm the announcement once it
        # leaves Hold.
        in_hold = self.machine_state.startswith("Hold")
        if in_hold and self.is_running and not self.is_paused:
            if not self._hold_announced:
                self._hold_announced = True
                self.data_received.emit(
                    "⏸ Machine entered HOLD mid-stream. "
                    "Click Resume (~) on the Safety panel to continue, "
                    "or Emergency Stop (0x18) to abort."
                )
        elif not in_hold and self._hold_announced:
            self._hold_announced = False
            self.data_received.emit("▶ Machine resumed")
