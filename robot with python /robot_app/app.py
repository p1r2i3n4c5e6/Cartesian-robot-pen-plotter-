"""Main application entry point.

Run from the project root with:

    python3 robot.py

or equivalently:

    python3 -m robot_app
"""

from __future__ import annotations

import sys

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QApplication

from . import APP_NAME, APP_TAGLINE, APP_VERSION
from .config import IS_RPI, reexec_with_clean_env_if_needed
from .logging_setup import setup_logging
from .styles import INDUSTRIAL_STYLE
from .main_window import MainWindow


def main() -> int:
    reexec_with_clean_env_if_needed()
    log = setup_logging()
    log.info("Starting %s v%s (%s mode)",
             APP_NAME, APP_VERSION,
             "Raspberry Pi" if IS_RPI else "Desktop")

    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps)
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setStyleSheet(INDUSTRIAL_STYLE)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(APP_VERSION)

    window = MainWindow()
    window.show()
    return app.exec_()


if __name__ == "__main__":
    sys.exit(main())
