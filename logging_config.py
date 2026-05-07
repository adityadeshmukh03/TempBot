from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path


def setup_logging(base_dir: str = "logs", level: int = logging.INFO) -> logging.Logger:
    log_dir = Path(base_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("trading_bot")
    logger.setLevel(level)
    logger.propagate = False

    if logger.handlers:
        return logger

    fmt = logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s")
    for name in ("runtime.log", "errors.log", "trades.log", "latency.log"):
        handler = RotatingFileHandler(log_dir / name, maxBytes=2_000_000, backupCount=5)
        handler.setFormatter(fmt)
        handler.setLevel(logging.ERROR if name == "errors.log" else level)
        logger.addHandler(handler)

    console = logging.StreamHandler()
    console.setFormatter(fmt)
    console.setLevel(level)
    logger.addHandler(console)
    return logger

