import functools
from pathlib import Path
import duckdb

from src.logger import setup_logger
from settings import config

logger = setup_logger(__name__)

def db_connector(func):
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        db = duckdb.connect(f"{config.ABSPATH}/imdb.duckdb")

        # 1. Ограничиваем память до 60% от 1 ГБ (~600 МБ), чтобы оставить запас для ОС и других операций[reference:7]
        db = duckdb.connect(f"{config.ABSPATH}/imdb.duckdb")
        try:
            # Настройки памяти
            db.execute("SET memory_limit = '600MB'")
            temp_dir = Path(f"{config.ABSPATH}/temp")
            temp_dir.mkdir(parents=True, exist_ok=True)
            db.execute(f"SET temp_directory = '{temp_dir}'")
            db.execute("SET preserve_insertion_order = false")
            db.execute("SET threads = 2")

            # Вставляем db в начало позиционных аргументов
            new_args = (db,) + args
            # Удаляем db из kwargs, чтобы избежать конфликта
            kwargs.pop('db', None)

            return func(*new_args, **kwargs)
        except Exception as e:
            logger.error(f"Ошибка в декорированной функции {func.__name__}: {e}")
            raise  # Пробрасываем исключение дальше
        finally:
            db.close()
    return wrapper
