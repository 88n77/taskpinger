"""Простий кольоровий логер + файловий лог logs/YYYY-MM-DD.log."""
import logging
import os
import sys
from datetime import datetime

LOG_DIR = "logs"

ORANGE = "\033[38;5;208m"
GREEN = "\033[32m"
RED = "\033[31m"
YELLOW = "\033[33m"
CYAN = "\033[36m"
RESET = "\033[0m"


class ColorFormatter(logging.Formatter):
    COLORS = {
        logging.INFO: CYAN,
        logging.WARNING: YELLOW,
        logging.ERROR: RED,
        logging.CRITICAL: RED,
        logging.DEBUG: GREEN,
    }

    def format(self, record):
        color = self.COLORS.get(record.levelno, RESET)
        ts = datetime.now().strftime("%H:%M:%S")
        return f"{color}[{ts}] {record.getMessage()}{RESET}"


def setup_logger(name: str = "taskpinger") -> logging.Logger:
    os.makedirs(LOG_DIR, exist_ok=True)
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)

    # Консоль
    ch = logging.StreamHandler(sys.stdout)
    ch.setFormatter(ColorFormatter())
    logger.addHandler(ch)

    # Файл
    log_file = os.path.join(LOG_DIR, f"{datetime.now():%Y-%m-%d}.log")
    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setFormatter(logging.Formatter("[%(asctime)s] %(levelname)s %(message)s"))
    logger.addHandler(fh)

    return logger


log = setup_logger()
