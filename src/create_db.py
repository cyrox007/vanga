from pathlib import Path

import duckdb

from src.logger import setup_logger
from settings import config

logger = setup_logger(__name__)

def create_duckdb_table_direct(dataset_name: str, if_exists: str = "replace") -> None:
    conn = duckdb.connect(f"{config.ABSPATH}/imdb.duckdb")
    dataset = Path(f"{config.ABSPATH}/data/imdb") / f"{dataset_name}.tsv.gz"
    table_name = dataset_name.replace('.', '_')

    # Проверяем, существует ли таблица
    conn.execute("SET memory_limit = '1GB'")
    temp_dir = Path(f"{config.ABSPATH}/temp")
    temp_dir.mkdir(parents=True, exist_ok=True)
    conn.execute(f"SET temp_directory = '{temp_dir}'")
    
    # Лучше использовать CREATE OR REPLACE — атомарно и быстрее
    conn.execute(f"""
        CREATE OR REPLACE TABLE {table_name} AS 
        SELECT * FROM read_csv_auto(
            '{dataset}',
            delim='\\t',
            header=True,
            nullstr='\\N',
            sample_size=50000   -- или задайте явные типы
        )
    """)
    logger.info(f"Таблица {table_name} создана/обновлена")

    row_count = conn.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0]
    logger.info(f"Количество записей в {table_name}: {row_count}")
    conn.close()