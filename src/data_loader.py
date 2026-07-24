import duckdb
import yaml
import os
from src.logger import setup_logger

logger = setup_logger(__name__)

def create_cache(config):
    """
    Создаёт (или пересоздаёт) кеш-базу DuckDB из свежих архивов IMDb.
    """
    db_path = config['data']['cache_db']
    basics_url = config['data']['basics_url']
    ratings_url = config['data']['ratings_url']
    crew_url = config['data']['crew_url']
    sample_frac = config['data'].get('sample_frac', 1.0)
    random_state = config['data']['random_state']

    logger.info(f"Создание кеша в {db_path}")

    conn = duckdb.connect(db_path)

    # Создаём денормализованную таблицу с нужными колонками
    # Для sample_frac используем TABLESAMPLE (приблизительная выборка)
    sample_clause = f"TABLESAMPLE SYSTEM ({sample_frac*100})" if sample_frac < 1 else ""

    query = f"""
        CREATE OR REPLACE TABLE movies AS
        WITH director_avg AS (
            SELECT 
                directors, 
                AVG(averageRating) AS avg_rating
            FROM (
                SELECT 
                    c.tconst,
                    c.directors,
                    r.averageRating
                FROM read_csv_auto('{crew_url}', sep='\\t', compression='gzip') c
                JOIN read_csv_auto('{ratings_url}', sep='\\t', compression='gzip') r 
                    ON c.tconst = r.tconst
                WHERE c.directors != '\\N'
            )
            GROUP BY directors
        ),
        movies_with_director_avg AS (
            SELECT 
                b.tconst,
                b.primaryTitle,
                CAST(b.startYear AS INT) AS startYear,
                CAST(b.runtimeMinutes AS INT) AS runtimeMinutes,
                r.averageRating,
                r.numVotes,
                b.genres,
                SPLIT_PART(c.directors, ',', 1) AS main_director,
                CASE WHEN LOWER(b.primaryTitle) LIKE '%remake%' THEN 1 ELSE 0 END AS is_remake
            FROM read_csv_auto('{basics_url}', sep='\\t', compression='gzip') b
            INNER JOIN read_csv_auto('{ratings_url}', sep='\\t', compression='gzip') r 
                ON b.tconst = r.tconst
            LEFT JOIN read_csv_auto('{crew_url}', sep='\\t', compression='gzip') c 
                ON b.tconst = c.tconst
            WHERE b.titleType = 'movie'
                AND b.startYear != '\\N'
                AND b.runtimeMinutes != '\\N'
                AND r.averageRating IS NOT NULL
            {sample_clause}
        )
        SELECT 
            m.*,
            da.avg_rating AS director_avg_rating
        FROM movies_with_director_avg m
        LEFT JOIN director_avg da ON m.main_director = da.directors
    """

    conn.execute(query)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tconst ON movies(tconst);")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_year ON movies(startYear);")

    count = conn.execute("SELECT COUNT(*) FROM movies").fetchone()[0]
    logger.info(f"Кеш создан. Всего фильмов: {count}")
    conn.close()
    return db_path

def load_data(config):
    """
    Возвращает путь к кешу и соединение (для совместимости со старыми вызовами)
    """
    db_path = config['data']['cache_db']
    if not os.path.exists(db_path):
        create_cache(config)
    return db_path