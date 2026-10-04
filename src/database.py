import functools
from pathlib import Path
import duckdb
import shutil
import tempfile

from src.logger import setup_logger
from settings import config

logger = setup_logger(__name__)


def db_connector(func):
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        # Каждое соединение получает собственный DuckDB spill-каталог. Это
        # исключает удаление temp/training или временных файлов другого задания.
        temp_root = Path(config.ABSPATH) / "temp" / "duckdb"
        temp_root.mkdir(parents=True, exist_ok=True)
        job_temp_dir = Path(tempfile.mkdtemp(prefix="job-", dir=temp_root))

        # Ограничиваем рабочее соединение по памяти, чтобы подготовка данных
        # не вытесняла inference и соседние сервисы.
        db = duckdb.connect(config.IMDB_DB_PATH)
        try:
            db.execute("SET memory_limit = '600MB'")
            escaped_temp = str(job_temp_dir).replace("'", "''")
            db.execute(f"SET temp_directory = '{escaped_temp}'")
            db.execute("SET preserve_insertion_order = false")
            db.execute("SET threads = 2")

            new_args = (db,) + args
            kwargs.pop("db", None)
            return func(*new_args, **kwargs)
        except Exception as e:
            logger.error(f"Ошибка в декорированной функции {func.__name__}: {e}")
            raise
        finally:
            db.close()
            shutil.rmtree(job_temp_dir, ignore_errors=True)

    return wrapper


def cleanup_temp():
    """Совместимый безопасный cleanup без глобального удаления ``temp``.

    Раньше функция рекурсивно удаляла всё содержимое ``temp`` и могла стереть
    активный ``temp/training`` другого процесса. Теперь временные каталоги должны
    удаляться только тем заданием, которое их создало. Функция оставлена как
    no-op для старых call-sites; она никогда не удаляет чужие каталоги.
    """
    logger.info(
        "Глобальная очистка temp отключена: временные файлы очищаются владельцами заданий"
    )
