import numpy as np
import pandas as pd
import duckdb

from src.logger import setup_logger
from settings import config

logger = setup_logger(__name__)

def get_genre_counts():
    conn = duckdb.connect(f"{config.ABSPATH}/imdb.duckdb")
    query = """
        SELECT unnest(string_split(genres, ',')) AS genre, COUNT(*) AS cnt
        FROM title_basics
        WHERE titleType = 'movie' AND genres IS NOT NULL
        GROUP BY genre
    """
    df = conn.execute(query).df()
    conn.close()
    return df.set_index('genre')['cnt']


def get_all_genres():
    conn = duckdb.connect(f"{config.ABSPATH}/imdb.duckdb")
    query = f""" 
        SELECT DISTINCT unnest(string_split(genres, ',')) AS genre
        FROM title_basics
        WHERE titleType = 'movie' AND genres IS NOT NULL
    """

    df_genres = conn.execute(query).df()
    conn.close()

    genres = df_genres['genre'].tolist()
    # Удаляем возможный пустой жанр

    genres = [g for g in genres if g and g.strip()]
    logger.info(f"Найдено жанров: {len(genres)}")
    return genres

def get_batches(genres: list, batch_size=10000):
    conn = duckdb.connect(f"{config.ABSPATH}/imdb.duckdb")
    query = """
        SELECT 
            b.tconst,
            b.startYear,
            b.runtimeMinutes,
            b.genres,
            r.averageRating,
            r.numVotes
        FROM title_basics b
        JOIN title_ratings r ON b.tconst = r.tconst
        WHERE b.titleType = 'movie'
          AND b.startYear IS NOT NULL
          AND b.runtimeMinutes IS NOT NULL
          AND b.genres IS NOT NULL
          AND r.averageRating IS NOT NULL
    """
    cursor = conn.execute(query)
    total = 0
    while True:
        rows = cursor.fetchmany(batch_size)
        if not rows:
            break
        df_batch = pd.DataFrame(rows, columns=[
            'tconst', 'startYear', 'runtimeMinutes', 'genres', 'averageRating', 'numVotes'
        ])

        # Приведение к числам
        df_batch['startYear'] = pd.to_numeric(df_batch['startYear'], errors='coerce')
        df_batch['runtimeMinutes'] = pd.to_numeric(df_batch['runtimeMinutes'], errors='coerce')
        df_batch['averageRating'] = pd.to_numeric(df_batch['averageRating'], errors='coerce')

        # Удаление NaN в ключевых колонках
        df_batch = df_batch.dropna(subset=['startYear', 'runtimeMinutes', 'averageRating'])

        # Фильтрация выбросов
        df_batch = df_batch[
            (df_batch['startYear'] >= 1900) & (df_batch['startYear'] <= 2030) &
            (df_batch['runtimeMinutes'] >= 10) & (df_batch['runtimeMinutes'] <= 300)
        ]

        q1 = df_batch['runtimeMinutes'].quantile(0.05)
        q3 = df_batch['runtimeMinutes'].quantile(0.95)
        df_batch = df_batch[(df_batch['runtimeMinutes'] >= q1) & (df_batch['runtimeMinutes'] <= q3)]
        if len(df_batch) == 0:
            continue

        # Бинарные жанры
        genre_df = pd.DataFrame(0, index=df_batch.index, columns=genres)
        for idx, genres_str in enumerate(df_batch['genres']):
            if genres_str:
                for g in genres_str.split(','):
                    if g in genres:
                        genre_df.loc[idx, g] = 1

        # Числовые признаки с ручным масштабированием
        numeric_df = df_batch[['startYear', 'runtimeMinutes']].astype(float)
        numeric_df['startYear'] = (numeric_df['startYear'] - 1900) / 100.0
        numeric_df['runtimeMinutes'] = numeric_df['runtimeMinutes'] / 100.0
        numeric_df['numVotes'] = np.log1p(df_batch['numVotes'])  # логарифмируем

        y = df_batch['averageRating'].astype(float)
        X = pd.concat([genre_df, numeric_df], axis=1)

        # --- ФИНАЛЬНАЯ ОЧИСТКА ОТ NaN ---
        # Объединяем X и y, удаляем строки с любыми NaN, затем разделяем
        mask = ~(X.isna().any(axis=1) | y.isna())
        X = X[mask]
        y = y[mask]
        if len(X) == 0:
            continue

        total += len(X)
        logger.info(f"Прочитано строк: {total}")
        yield X, y
    conn.close()