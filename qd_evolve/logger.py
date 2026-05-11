from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

from loguru import logger

LOG_DIR = Path(__file__).resolve().parent.parent / "logs"


class _BinLog:
    """Binary unbuffered file sink. buffering=0 guarantees real-time disk writes."""

    def __init__(self, path: Path) -> None:
        self._f = open(str(path), "ab", buffering=0)

    def write(self, message: str) -> None:
        self._f.write(message.encode())

    def flush(self) -> None:
        pass  # buffering=0, no user-space buffer to flush

    def close(self) -> None:
        self._f.close()


def setup_logging(level: str = "INFO") -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = LOG_DIR / f"{timestamp}.log"

    logger.remove()
    logger.add(
        sys.stderr,
        level="CRITICAL",
        format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan> - <level>{message}</level>",
    )
    logger.add(
        _BinLog(log_file),
        level=level,
        format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function}:{line} - {message}",
    )
