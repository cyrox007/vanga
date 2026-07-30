import gc
from pathlib import Path

import duckdb

from src.database import db_connector
from src.logger import setup_logger
from settings import config

logger = setup_logger(__name__)

@db_connector
def create_duckdb_table_direct(db: duckdb.DuckDBPyConnection, dataset_name: str, if_exists: str = "replace") -> None:
    dataset = Path(f"{config.ABSPATH}/data/imdb") / f"{dataset_name}.tsv.gz"
    table_name = dataset_name.replace('.', '_')
    
    # Лучше использовать CREATE OR REPLACE — атомарно и быстрее
    query = f"""
        CREATE OR REPLACE TABLE {table_name} AS 
        SELECT * FROM read_csv_auto(
            '{dataset}',
            delim='\\t',
            header=True,
            nullstr='\\N',
            sample_size=50000   -- или задайте явные типы
        )
    """

    if table_name == "title_basics":
        query += """ 
            WHERE titleType = 'movie'
                AND startYear IS NOT NULL
                AND runtimeMinutes IS NOT NULL
                AND genres IS NOT NULL
        """

    if table_name == "title_ratings":
        query += """ 
            WHERE tconst IN (SELECT tconst FROM title_basics)
         """

    if table_name == "title_principals":
        query += """ 
            WHERE tconst IN (SELECT tconst FROM title_basics)
                AND category IN ('director', 'actor', 'actress')
         """

    if table_name == "name_basics":
        query += """ 
            WHERE nconst IN (SELECT DISTINCT nconst FROM title_principals)
         """
        
    db.execute(query)
    logger.info(f"Таблица {table_name} создана/обновлена")

    row_count = db.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0]
    logger.info(f"Количество записей в {table_name}: {row_count}")
    

@db_connector
def create_indexes(db: duckdb.DuckDBPyConnection) -> None:
    # Список индексов: (таблица, имя_индекса, столбец)
    indexes = [
        ("title_basics", "idx_title_basics_tconst", "tconst"),
        ("title_basics", "idx_title_basics_titleType", "titleType"),
        ("title_ratings", "idx_title_ratings_tconst", "tconst"),
        ("title_principals", "idx_title_principals_tconst", "tconst"),
        ("title_principals", "idx_title_principals_nconst", "nconst"),
        ("title_principals", "idx_title_principals_category", "category"),
        ("name_basics", "idx_name_basics_nconst", "nconst"),
        ("name_basics", "idx_name_basics_primaryName", "primaryName"),
    ]

    for table, idx_name, column in indexes:
        logger.info(f"Создание индекса {idx_name} на {table}({column})...")
        try:
            db.execute(f"CREATE INDEX IF NOT EXISTS {idx_name} ON {table}({column});")
            logger.info(f"Индекс {idx_name} создан.")
        except Exception as e:
            logger.error(f"Ошибка при создании индекса {idx_name}: {e}")
        # Принудительно освобождаем память и сбрасываем WAL
        db.execute("PRAGMA force_checkpoint;")
        gc.collect()

    logger.info("Все индексы созданы.")