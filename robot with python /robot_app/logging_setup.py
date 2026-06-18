"""Single point for setting up the application logger.

Logs go to both stdout *and* a rotating file in `logs/robot.log` so a
machine in production keeps a permanent record of every command, alarm
and disconnect event.
"""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler

from .config import LOGS_DIR

_INITIALISED = False


def setup_logging(verbose: bool = False) -> logging.Logger:
    """Idempotent.  Returns the root robot logger."""
    global _INITIALISED
    logger = logging.getLogger("robot")
    if _INITIALISED:
        return logger

    logger.setLevel(logging.DEBUG if verbose else logging.INFO)
    logger.propagate = False

    fmt = logging.Formatter(
        fmt="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    console = logging.StreamHandler()
    console.setLevel(logging.INFO if not verbose else logging.DEBUG)
    console.setFormatter(fmt)
    logger.addHandler(console)

    try:
        log_file = LOGS_DIR / "robot.log"
        file_handler = RotatingFileHandler(
            log_file, maxBytes=2_000_000, backupCount=5, encoding="utf-8"
        )
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(fmt)
        logger.addHandler(file_handler)
    except OSError:
        # No filesystem access — keep going with console only.
        pass

    _INITIALISED = True
    return logger


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"robot.{name}")
