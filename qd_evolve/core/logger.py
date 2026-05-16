"""Logging — SharedFileHandler for real-time log visibility."""

import logging
import sys
from datetime import datetime
from pathlib import Path

LOG_DIR = Path("logs")


class SharedFileHandler(logging.FileHandler):
    """FileHandler that opens/writes/flushes/closes per emit — safe for concurrent tail."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
            with open(self.baseFilename, "a", encoding="utf-8") as f:
                f.write(msg + "\n")
                f.flush()
        except Exception:
            self.handleError(record)


def setup_logging(level: str = "INFO", log_dir: str | Path | None = None) -> None:
    """Configure qd_evolve logger with file (all levels) + stderr (ERROR only)."""
    global LOG_DIR
    if log_dir is not None:
        LOG_DIR = Path(log_dir)

    LOG_DIR.mkdir(parents=True, exist_ok=True)

    lvl = getattr(logging, level.upper(), logging.INFO)

    root = logging.getLogger("qd_evolve")
    root.setLevel(lvl)
    root.handlers.clear()
    root.propagate = False

    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)s:%(funcName)s:%(lineno)d - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    fh = SharedFileHandler(LOG_DIR / f"{ts}.log", encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt)
    root.addHandler(fh)

    stderr = logging.StreamHandler(sys.stderr)
    stderr.setLevel(logging.ERROR)
    stderr.setFormatter(logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)s:%(funcName)s - %(message)s",
        datefmt="%H:%M:%S",
    ))
    root.addHandler(stderr)


logger = logging.getLogger("qd_evolve")