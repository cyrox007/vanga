import duckdb
import pandas as pd

def get_db_connection(db_path):
    return duckdb.connect(db_path)

def create_or_refresh_cache(config):
    """
    Создаёт (или пересоздаёт) таблицу movies в DuckDB.
    Вычисляет is_remake и director_avg_rating прямо в SQL.
    """
    db_path = config['data']['cache_db']
    basics_url = config['data']['basics_url']
    ratings_url = config['data']['ratings_url']
    crew_url = config['data']['crew_url']
    sample_frac = config['data']['sample_frac']

    conn = duckdb.connect(db_path)
    
    # Основной запрос с вычислением признаков
    query = f"""
    CREATE OR REPLACE TABLE movies AS
    WITH director_avg AS (
        SELECT 
            directors, 
            AVG(averageRating) AS avg_rating
        FROM (
            SELECT 
                c.directors,
                r.averageRating
            FROM read_csv_auto('{crew_url}', sep='\\t', compression='gzip') c
            JOIN read_csv_auto('{ratings_url}', sep='\\t', compression='gzip') r 
                ON c.tconst = r.tconst
            WHERE c.directors != '\\N'
        )
        GROUP BY directors
    ),
    movie_base AS (
        SELECT 
            b.tconst,
            b.primaryTitle,
            CAST(b.startYear AS INT) AS startYear,
            CAST(b.runtimeMinutes AS INT) AS runtimeMinutes,
            r.averageRating,
            r.numVotes,
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
    )
    SELECT 
        mb.*,
        da.avg_rating AS director_avg_rating
    FROM movie_base mb
    LEFT JOIN director_avg da ON mb.main_director = da.directors
    """
    if sample_frac and sample_frac < 1.0:
        query = f"CREATE OR REPLACE TABLE movies AS SELECT * FROM ({query}) TABLESAMPLE SYSTEM ({sample_frac*100})"
    
    conn.execute(query)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tconst ON movies(tconst);")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_year ON movies(startYear);")
    
    count = conn.execute("SELECT COUNT(*) FROM movies").fetchone()[0]
    print(f"Кеш создан. Всего фильмов: {count}")
    conn.close()
    return count

def get_total_rows(conn):
    return conn.execute("SELECT COUNT(*) FROM movies").fetchone()[0]

def fetch_chunk(conn, chunk_size, offset):
    """Возвращает чанк с нужными колонками для обучения."""
    cols = [
        "primaryTitle", "startYear", "runtimeMinutes",
        "averageRating", "main_director", "director_avg_rating", "is_remake"
    ]
    cols_str = ", ".join(cols)
    query = f"""
        SELECT {cols_str}
        FROM movies
        ORDER BY tconst
        LIMIT {chunk_size} OFFSET {offset}
    """
    return conn.execute(query).df()

def fetch_sample(conn, limit):
    """Возвращает подвыборку числовых признаков для обучения импьютера."""
    query = f"""
        SELECT startYear, runtimeMinutes
        FROM movies
        ORDER BY tconst
        LIMIT {limit}
    """
    return conn.execute(query).df()