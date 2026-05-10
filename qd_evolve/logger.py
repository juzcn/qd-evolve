from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

from loguru import logger

LOG_DIR = Path(__file__).resolve().parent.parent / "logs"


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
        str(log_file),
        level=level,
        rotation="10 MB",
        retention="7 days",
        encoding="utf-8",
        buffering=1,
        format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function}:{line} - {message}",
    )
