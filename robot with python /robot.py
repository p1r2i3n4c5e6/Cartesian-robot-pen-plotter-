#!/usr/bin/env python3
"""CNCjs Plotter Bridge — Industrial Edition launcher.

The actual application lives in the ``robot_app/`` package.  This file
keeps ``./robot.py`` working for the existing shell scripts and the
desktop launcher icons without forcing the user to learn a new path.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Allow `python3 robot.py` to run from any working directory.
HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from robot_app.app import main


if __name__ == "__main__":
    sys.exit(main())
