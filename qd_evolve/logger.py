
import logging
import sys
from datetime import datetime
from pathlib import Path

LOG_DIR = Path(__file__).resolve().parent.parent / "logs"


class _SharedFileHandler(logging.FileHandler):
    """Opens the file per-write instead of holding it open, allowing external
    readers/watchers to see every line immediately and delete the file freely."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            with open(self.baseFilename, self.mode, encoding=self.encoding) as f:
                f.write(self.format(record) + self.terminator)
                f.flush()
        except Exception:
            self.handleError(record)


def setup_logging(level: str = "INFO") -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = LOG_DIR / f"{timestamp}.log"

    lvl = getattr(logging, level.upper(), logging.INFO)

    root = logging.getLogger("qd_evolve")
    root.setLevel(lvl)
    root.handlers.clear()
    root.propagate = False

    file_handler = _SharedFileHandler(str(log_file), encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)s:%(funcName)s:%(lineno)d - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))
    root.addHandler(file_handler)

    stderr_handler = logging.StreamHandler(sys.stderr)
    stderr_handler.setLevel(logging.ERROR)
    stderr_handler.setFormatter(logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)s:%(funcName)s - %(message)s",
        datefmt="%H:%M:%S",
    ))
    root.addHandler(stderr_handler)


logger = logging.getLogger("qd_evolve")
