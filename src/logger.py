import logging
import os
from logging.handlers import RotatingFileHandler


def setup_logger(name):
    logger = logging.getLogger(name)

    if logger.handlers:
        return logger

    logger.setLevel(logging.INFO)

    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
    )

    console = logging.StreamHandler()
    console.setFormatter(formatter)

    os.makedirs(".logs", exist_ok=True)

    file = RotatingFileHandler(
        ".logs/app.log",
        maxBytes=5_000_000,
        backupCount=3,
        encoding="utf-8"
    )

    file.setFormatter(formatter)

    logger.addHandler(console)
    logger.addHandler(file)

    return logger