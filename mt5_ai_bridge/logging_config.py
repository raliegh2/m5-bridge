"""Centralised logging setup: console + rotating file.

``console_level`` can be raised (e.g. WARNING) to keep the console quiet while
the file keeps full INFO detail -- used so the terminal can show a single
compact status line instead of a log line every loop.
"""

import logging
import os
from logging.handlers import RotatingFileHandler
from typing import Optional

_ROOT = "mt5_ai_bridge"
_CONFIGURED = False


def setup_logging(level: str = "INFO", log_dir: str = "logs",
                  log_file: str = "bridge.log",
                  console_level: Optional[str] = None) -> logging.Logger:
    """Configure the package root logger once and return it."""
    global _CONFIGURED
    logger = logging.getLogger(_ROOT)

    if _CONFIGURED:
        logger.setLevel(level)
        return logger

    logger.setLevel(logging.DEBUG)   # handlers decide what shows
    logger.propagate = False

    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    console = logging.StreamHandler()
    console.setFormatter(fmt)
    console.setLevel(console_level or level)
    logger.addHandler(console)

    try:
        os.makedirs(log_dir, exist_ok=True)
        file_handler = RotatingFileHandler(
            os.path.join(log_dir, log_file),
            maxBytes=2_000_000, backupCount=5, encoding="utf-8",
        )
        file_handler.setFormatter(fmt)
        file_handler.setLevel(level)
        logger.addHandler(file_handler)
    except OSError:
        logger.warning("Could not create log file in %s; console only.", log_dir)

    _CONFIGURED = True
    return logger


def get_logger(name: str = "") -> logging.Logger:
    """Return a child logger under the package root."""
    return logging.getLogger(f"{_ROOT}.{name}" if name else _ROOT)
