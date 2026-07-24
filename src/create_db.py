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
    exists = conn.execute(
        f"SELECT COUNT(*) FROM information_schema.tables WHERE table_name = '{table_name}'"
    ).fetchone()[0] > 0

    if not exists:
         # Если таблицы нет — создаём
        conn.execute(f"""
            CREATE TABLE {table_name} AS 
            SELECT * FROM read_csv_auto(
                '{dataset}',
                delim='\\t',
                header=True,
                nullstr='\\N',
                AUTO_DETECT=TRUE
            )
        """)
        logger.info(f"Таблица {table_name} создана")
    else:
        # Если таблица есть — очищаем и вставляем новые данные
        conn.execute(f"TRUNCATE TABLE {table_name}")
        conn.execute(f"""
            INSERT INTO {table_name}
            SELECT * FROM read_csv_auto(
                '{dataset}',
                delim='\\t',
                header=True,
                nullstr='\\N',
                AUTO_DETECT=TRUE
            )
        """)
        logger.info(f"Таблица {table_name} полностью обновлена (TRUNCATE + INSERT)")
    
    conn.close()