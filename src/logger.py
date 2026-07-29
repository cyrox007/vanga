import logging
import os
from logging.handlers import RotatingFileHandler
import sys


def setup_logger(name: str, level: str = "INFO") -> logging.Logger:
    logger = logging.getLogger(name)

    if logger.handlers:
        return logger

    # Устанавливаем уровень логирования
    log_level = getattr(logging, level.upper(), logging.INFO)
    logger.setLevel(log_level)

    # Формат с деталями для отладки
    detailed_formatter = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)s:%(lineno)d | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )

    # Упрощённый формат для консоли
    console_formatter = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )

    # Консольный обработчик
    console = logging.StreamHandler(sys.stdout)
    console.setLevel(log_level)
    console.setFormatter(console_formatter)

    # Создаём директорию для логов
    os.makedirs(".logs", exist_ok=True)

    # Файловый обработчик с ротацией
    file = RotatingFileHandler(
        ".logs/app.log",
        maxBytes=10_000_000,  # 10 MB
        backupCount=5,
        encoding="utf-8"
    )
    file.setLevel(log_level)
    file.setFormatter(detailed_formatter)

    logger.addHandler(console)
    logger.addHandler(file)

    # Логгируем информацию о запуске
    logger.info(f"Логгер '{name}' инициализирован (уровень: {level})")

    return logger