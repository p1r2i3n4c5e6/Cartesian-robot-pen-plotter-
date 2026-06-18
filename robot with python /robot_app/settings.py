"""Persistent settings loaded from / saved to ``settings.json``.

The keys are intentionally compatible with the layout used by
``robot2.py`` so a user's existing preferences carry over.
"""

from __future__ import annotations

import json
from typing import Any, Dict

from .config import SETTINGS_FILE
from .logging_setup import get_logger

log = get_logger("settings")


DEFAULT_SETTINGS: Dict[str, Any] = {
    # The user wants the cncjs web UI to come up automatically alongside
    # this app so the same job is visible in both places.  cncjs talks
    # GRBL to the controller; we talk Socket.IO to cncjs.
    "connection_mode": "cncjs",       # "direct" or "cncjs"
    "machine_port": "",
    "baud_rate": "115200",
    "cncjs_host": "127.0.0.1",
    "cncjs_port": 8000,
    "cncjs_command": "",
    "cncjs_token": "",
    "cncjs_username": "",
    "cncjs_password": "",
    "auto_launch_cncjs": True,
    "auto_open_browser": False,
    "auto_connect_machine": True,
    "plot_width": 216,
    "plot_height": 279,
    "feed_rate": 3000,
    "pen_up_z": 5.0,
    "pen_down_z": 0.0,
    "tool_changer": {
        "enabled": False,
        "slots": {},
        "safe_z": 15.0,
        "approach_dist": 100.0,
        "approach_axis": "Y",
        "approach_dir": -1,
        "travel_feed": 3000,
        "pickup_feed": 800,
        "dwell_ms": 300,
        "return_to_rack": True,
        "start_tool": 0,
    },
    "calibration": {
        # Software-side per-axis multipliers applied to every X/Y/Z
        # before G-code leaves the app.  1.0 = no compensation.  Set
        # via the Steps/mm Calibration wizard.  These are the SAFE
        # default for FluidNC, where firmware $100/$101/$102 may be
        # overridden by the YAML config on every boot.
        "x_scale": 1.0,
        "y_scale": 1.0,
        "z_scale": 1.0,
        # Last-used commanded / measured pair for each axis so the
        # wizard can re-populate the spin-boxes when re-opened.
        "x_commanded": 50.0, "x_measured": 50.0,
        "y_commanded": 50.0, "y_measured": 50.0,
        "z_commanded": 20.0, "z_measured": 20.0,
    },
    "homing": {
        "axes": {
            "X": {"enabled": True,  "direction": 1,  "pull_off": 1.0, "max_travel": 200.0},
            "Y": {"enabled": True,  "direction": 1,  "pull_off": 1.0, "max_travel": 200.0},
            "Z": {"enabled": True,  "direction": 1,  "pull_off": 1.0, "max_travel": 50.0},
            "A": {"enabled": False, "direction": 1,  "pull_off": 0.0, "max_travel": 360.0},
        },
        "seek_feed": 1500,
        "locate_feed": 100,
        "debounce_ms": 250,
    },
}


def load_settings() -> Dict[str, Any]:
    """Read the settings file; missing keys are filled from defaults."""
    data = dict(DEFAULT_SETTINGS)
    if not SETTINGS_FILE.exists():
        return data
    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as fh:
            stored = json.load(fh)
        if isinstance(stored, dict):
            data.update(stored)
            tc = dict(DEFAULT_SETTINGS["tool_changer"])
            tc.update(stored.get("tool_changer", {}) or {})
            data["tool_changer"] = tc

            # Deep-merge calibration so newly added keys (e.g. *_commanded
            # / *_measured) don't blow up older settings.json files.
            cal = dict(DEFAULT_SETTINGS["calibration"])
            cal.update(stored.get("calibration", {}) or {})
            data["calibration"] = cal

            # Deep-merge homing so older settings.json files inherit the
            # newly added per-axis defaults instead of failing with KeyError.
            home_default = DEFAULT_SETTINGS["homing"]
            home_stored = stored.get("homing", {}) or {}
            merged_axes = {axis: dict(home_default["axes"][axis])
                           for axis in home_default["axes"]}
            for axis, axis_cfg in (home_stored.get("axes") or {}).items():
                if axis in merged_axes and isinstance(axis_cfg, dict):
                    merged_axes[axis].update(axis_cfg)
            data["homing"] = {
                "axes": merged_axes,
                "seek_feed": home_stored.get("seek_feed", home_default["seek_feed"]),
                "locate_feed": home_stored.get("locate_feed", home_default["locate_feed"]),
                "debounce_ms": home_stored.get("debounce_ms", home_default["debounce_ms"]),
            }
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("settings.json unreadable: %s — using defaults", exc)
    return data


def save_settings(settings: Dict[str, Any]) -> bool:
    """Write to disk atomically; returns True on success."""
    tmp = SETTINGS_FILE.with_suffix(".json.tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(settings, fh, indent=2, sort_keys=True)
        tmp.replace(SETTINGS_FILE)
        return True
    except OSError as exc:
        log.error("Could not save settings: %s", exc)
        try:
            tmp.unlink()
        except OSError:
            pass
        return False
