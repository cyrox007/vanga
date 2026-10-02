from __future__ import annotations

import os
from pathlib import Path

import duckdb

from settings import config
from src.create_db import (
    create_derived_tables,
    create_duckdb_table_direct,
    create_indexes,
)
from src.data_loader import download_imdb_dataset
from src.database import cleanup_temp
from src.logger import setup_logger


logger = setup_logger(__name__)

DATASETS = [
    "title.basics",
    "title.ratings",
    "title.principals",
    "title.crew",
    "name.basics",
]


def _validate_database(path: Path) -> None:
    conn = duckdb.connect(str(path), read_only=True)
    try:
        required = {
            "title_basics": 1,
            "title_ratings": 1,
            "title_principals": 1,
            "title_crew": 1,
            "title_writers": 1,
            "name_basics": 1,
        }
        for table_name, minimum in required.items():
            count = conn.execute(
                f"SELECT COUNT(*) FROM {table_name}"
            ).fetchone()[0]
            if count < minimum:
                raise RuntimeError(
                    f"Проверка новой IMDb БД не пройдена: "
                    f"{table_name} содержит {count} строк"
                )
            logger.info(f"Проверка {table_name}: {count} строк")
    finally:
        conn.close()


def _build_staged_database(target: Path) -> None:
    staged = target.with_name(target.name + ".next")
    staged_wal = Path(str(staged) + ".wal")
    staged.unlink(missing_ok=True)
    staged_wal.unlink(missing_ok=True)

    original_path = config.IMDB_DB_PATH
    config.IMDB_DB_PATH = str(staged)
    try:
        # Порядок важен: следующие таблицы ссылаются на предыдущие.
        create_duckdb_table_direct("title.basics")
        create_duckdb_table_direct("title.ratings")
        create_duckdb_table_direct("title.principals")
        create_duckdb_table_direct("title.crew")
        create_derived_tables()
        create_duckdb_table_direct("name.basics")
        create_indexes()

        _validate_database(staged)

        target.parent.mkdir(parents=True, exist_ok=True)
        os.replace(staged, target)
        logger.info(f"Новая IMDb БД атомарно опубликована: {target}")
    except Exception:
        staged.unlink(missing_ok=True)
        staged_wal.unlink(missing_ok=True)
        raise
    finally:
        config.IMDB_DB_PATH = original_path


def main() -> int:
    changed = False

    for dataset in DATASETS:
        if download_imdb_dataset(dataset):
            changed = True

    target = Path(config.IMDB_DB_PATH)
    if target.exists() and not changed:
        logger.info("IMDb datasets не изменились — пересборка БД не требуется")
        return 0

    logger.info("Собираем новую IMDb БД в staging-файле")
    _build_staged_database(target)
    cleanup_temp()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
