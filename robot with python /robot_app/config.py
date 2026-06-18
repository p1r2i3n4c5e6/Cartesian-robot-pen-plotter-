"""Global constants, presets, GRBL alarm/error tables, and runtime helpers."""

from __future__ import annotations

import os
import sys
from pathlib import Path


# ----------------------------------------------------------------------
# Paths
# ----------------------------------------------------------------------

PACKAGE_DIR: Path = Path(__file__).resolve().parent
PROJECT_DIR: Path = PACKAGE_DIR.parent
GCODE_DIR: Path = PROJECT_DIR / "gcode"
LOGS_DIR: Path = PROJECT_DIR / "logs"
SETTINGS_FILE: Path = PROJECT_DIR / "settings.json"

GCODE_DIR.mkdir(exist_ok=True)
LOGS_DIR.mkdir(exist_ok=True)


# ----------------------------------------------------------------------
# UI catalogue
# ----------------------------------------------------------------------

SUPPORTED_IMPORT_FORMATS = {
    "All Supported": "*.svg *.dxf *.png *.jpg *.jpeg *.bmp *.gif *.hpgl *.plt *.gcode *.nc *.ngc *.csv",
    "SVG Vector": "*.svg",
    "DXF CAD": "*.dxf",
    "HPGL Plot": "*.hpgl *.plt",
    "Raster Images": "*.png *.jpg *.jpeg *.bmp *.gif",
    "G-Code": "*.gcode *.nc *.ngc",
    "CSV Data": "*.csv",
}

COMMON_BAUD_RATES = [
    9600, 19200, 38400, 57600, 115200, 230400, 250000, 460800, 921600
]

CONNECTION_MODES = ("direct", "cncjs")


# ----------------------------------------------------------------------
# Pen / tool catalogue
# ----------------------------------------------------------------------

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


# ----------------------------------------------------------------------
# GRBL diagnostics
# ----------------------------------------------------------------------

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


# ----------------------------------------------------------------------
# USB VID/PID hints used to score auto-detected serial ports
# ----------------------------------------------------------------------

CONTROLLER_USB_VIDS = {
    0x10C4,  # Silicon Labs CP210x (most ESP32 dev boards)
    0x1A86,  # QinHeng CH340/CH341 (cheap ESP32 boards)
    0x303A,  # Espressif (native USB ESP32-S2/S3)
    0x239A,  # Adafruit
    0x16C0,  # Teensy
    0x2341,  # Arduino
    0x2A03,  # Arduino LLC
    0x0403,  # FTDI
}


def is_raspberry_pi() -> bool:
    """Cheap detection that's safe on every Linux distro."""
    try:
        with open('/proc/cpuinfo', 'r') as fh:
            content = fh.read().lower()
    except OSError:
        return False
    return 'raspberry pi' in content or 'bcm' in content


IS_RPI = is_raspberry_pi()


def reexec_with_clean_env_if_needed() -> None:
    """If the current process inherited Snap / ROS / MVS variables that break
    PyQt5, restart with a sanitized environment.  Idempotent.
    """
    if os.environ.get("ROBOT_ENV_CLEAN") == "1":
        return
    ld_library_path = os.environ.get("LD_LIBRARY_PATH", "")
    has_snap = any(k == "SNAP" or k.startswith("SNAP_") for k in os.environ)
    has_snap_lib = bool(os.environ.get("SNAP_LIBRARY_PATH"))
    has_risky = any(token in ld_library_path
                    for token in ("/snap/", "/opt/ros/", "/opt/MVS/"))
    if not (has_snap or has_snap_lib or has_risky):
        return
    keep_keys = {
        "HOME", "USER", "LOGNAME", "PATH", "SHELL", "LANG", "LC_ALL",
        "DISPLAY", "WAYLAND_DISPLAY", "XDG_SESSION_TYPE",
        "XDG_CURRENT_DESKTOP", "DESKTOP_SESSION", "XAUTHORITY",
        "XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS",
        "TERM", "COLORTERM", "QT_QPA_PLATFORM",
    }
    clean_env = {k: v for k, v in os.environ.items() if k in keep_keys and v}
    clean_env.setdefault("PATH", "/usr/local/bin:/usr/bin:/bin")
    clean_env["ROBOT_ENV_CLEAN"] = "1"
    try:
        os.execvpe(sys.executable, [sys.executable] + sys.argv, clean_env)
    except OSError:
        # Fall through and let the app try anyway.
        return
