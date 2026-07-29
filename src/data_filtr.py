import gc
import pickle
from pathlib import Path
from typing import Generator, List, Optional, Tuple

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

def get_director_actor_stats():
    """
    Вычисляет средние рейтинги для режиссёров и актёров на основе их прошлых работ.
    Возвращает словари: director_avg, actor_avg
    
    ВАЖНО: Вся агрегация выполняется в DuckDB, чтобы не перегружать память.
    """
    logger.info("Вычисление статистики по режиссёрам и актёрам")

    # Настраиваем DuckDB для работы с ограниченной памятью
    conn = duckdb.connect(f"{config.ABSPATH}/imdb.duckdb")

    conn.execute("SET memory_limit = '256MB'")
    temp_dir = Path(f"{config.ABSPATH}/temp")
    temp_dir.mkdir(parents=True, exist_ok=True)
    conn.execute(f"SET temp_directory = '{temp_dir}'")

    # Получаем агрегированные данные для режиссёров напрямую из DuckDB
    director_query = """
    WITH movie_ratings AS (
        SELECT
            b.tconst,
            r.averageRating
        FROM title_basics b
        JOIN title_ratings r ON b.tconst = r.tconst
        WHERE b.titleType = 'movie'
          AND r.averageRating IS NOT NULL
    ),
    directors_agg AS (
        SELECT
            p.nconst,
            AVG(mr.averageRating) AS avg_rating,
            COUNT(*) AS movie_count
        FROM title_principals p
        JOIN movie_ratings mr ON p.tconst = mr.tconst
        WHERE p.category = 'director'
        GROUP BY p.nconst
        HAVING COUNT(*) >= 2
    )
    SELECT nconst, avg_rating FROM directors_agg
    """

    logger.info("Выполнение запроса для статистики режиссёров")
    director_df = conn.execute(director_query).df()
    director_avg = dict(zip(director_df['nconst'], director_df['avg_rating']))
    logger.info(f"Найдено режиссёров: {len(director_avg)}")
    
    del director_df
    gc.collect()

    # Получаем агрегированные данные для актёров напрямую из DuckDB
    actor_query = """
    WITH movie_ratings AS (
        SELECT
            b.tconst,
            r.averageRating
        FROM title_basics b
        JOIN title_ratings r ON b.tconst = r.tconst
        WHERE b.titleType = 'movie'
          AND r.averageRating IS NOT NULL
    ),
    actors_agg AS (
        SELECT
            p.nconst,
            AVG(mr.averageRating) AS avg_rating,
            COUNT(*) AS movie_count
        FROM title_principals p
        JOIN movie_ratings mr ON p.tconst = mr.tconst
        WHERE p.category IN ('actor', 'actress')
        GROUP BY p.nconst
        HAVING COUNT(*) >= 3
    )
    SELECT nconst, avg_rating FROM actors_agg
    """

    logger.info("Выполнение запроса для статистики актёров")
    actor_df = conn.execute(actor_query).df()
    actor_avg = dict(zip(actor_df['nconst'], actor_df['avg_rating']))
    logger.info(f"Найдено актёров: {len(actor_avg)}")
    
    del actor_df
    gc.collect()
    
    conn.close()

    logger.info(f"Статистика готова: {len(director_avg)} режиссёров, {len(actor_avg)} актёров")
    return director_avg, actor_avg


def get_nconst_mapping():
    """
    Создаёт маппинг nconst -> primaryName для актёров и режиссёров
    """
    conn = duckdb.connect(f"{config.ABSPATH}/imdb.duckdb")
    query = """
        SELECT nconst, primaryName, knownForTitles
        FROM name_basics
        WHERE nconst IS NOT NULL
    """
    df = conn.execute(query).df()
    conn.close()

    # Убираем NaN
    df = df.dropna(subset=['nconst'])
    mapping = dict(zip(df['nconst'], df['primaryName']))
    logger.info(f"Найдено имён: {len(mapping)}")
    return mapping


def get_tconst_to_nconst():
    """
    Возвращает словарь: tconst -> {'directors': [nconst], 'actors': [nconst]}
    
    ВАЖНО: Данные обрабатываются потоком через fetchmany, чтобы не перегружать память.
    """
    conn = duckdb.connect(f"{config.ABSPATH}/imdb.duckdb")
    conn.execute("SET memory_limit = '256MB'")
    temp_dir = Path(f"{config.ABSPATH}/temp")
    temp_dir.mkdir(parents=True, exist_ok=True)
    conn.execute(f"SET temp_directory = '{temp_dir}'")
    
    query = """
        SELECT
            tconst,
            nconst,
            category
        FROM title_principals
        WHERE category IN ('director', 'actor', 'actress')
    """
    cursor = conn.execute(query)
    
    result = {}
    batch_size = 100000
    total_processed = 0
    
    while True:
        rows = cursor.fetchmany(batch_size)
        if not rows:
            break
        
        for row in rows:
            tconst, nconst, category = row
            
            if tconst not in result:
                result[tconst] = {'directors': [], 'actors': []}

            if category == 'director':
                if nconst not in result[tconst]['directors']:
                    result[tconst]['directors'].append(nconst)
            else:  # actor/actress
                if nconst not in result[tconst]['actors']:
                    result[tconst]['actors'].append(nconst)
        
        total_processed += len(rows)
        if total_processed % 500000 == 0:
            logger.info(f"Обработано {total_processed} записей о персонах...")
    
    conn.close()
    logger.info(f"Найдено связей фильм-персона: {len(result)}")
    return result


def get_batches(
    genres: list,
    batch_size: int = 5000,
    use_director_stats: bool = True,
    use_actor_stats: bool = True,
    max_batches: Optional[int] = None
) -> Generator[Tuple[pd.DataFrame, pd.Series, List[str], List[str]], None, None]:
    """
    Генератор батчей для обучения модели.
    
    АРХИТЕКТУРА ДЛЯ МАЛОЙ ПАМЯТИ (1GB):
    Вместо одного гигантского запроса ко всей базе, мы:
    1. Берем пакет ID фильмов (tconst) через LIMIT/OFFSET.
    2. Обогащаем этот пакет данными через JOIN (только для этих ID).
    3. Возвращаем батч.
    
    Это гарантирует, что в памяти никогда нет больше batch_size строк.
    """
    logger.info("Инициализация генератора батчей")

    # Настраиваем DuckDB для работы с ограниченной памятью
    conn = duckdb.connect(f"{config.ABSPATH}/imdb.duckdb")
    
    # Выделяем до 700MB под DuckDB (остальное留给 pandas/python при 1GB RAM)
    conn.execute("SET memory_limit='700MB'")
    conn.execute("SET threads=2")
    conn.execute("SET preserve_insertion_order=false")
    
    temp_dir = Path(f"{config.ABSPATH}/temp")
    temp_dir.mkdir(parents=True, exist_ok=True)
    conn.execute(f"SET temp_directory='{temp_dir}'")

    logger.info("Настройки DuckDB применены (Memory=700MB, Threads=2)")

    # Запрос для получения пакета ID фильмов
    id_query = """
        SELECT tconst 
        FROM title_basics 
        WHERE titleType = 'movie' AND startYear IS NOT NULL
        ORDER BY tconst 
        LIMIT ? OFFSET ?
    """

    # Вспомогательный запрос для обогащения данных по списку tconst
    enrich_query = """
        WITH batch_movies AS (
            SELECT 
                b.tconst,
                b.primaryTitle,
                b.startYear,
                b.runtimeMinutes,
                b.genres,
                r.averageRating,
                r.numVotes
            FROM UNNEST(?) AS t(tconst)
            JOIN title_basics b ON b.tconst = t.tconst
            JOIN title_ratings r ON r.tconst = b.tconst
            WHERE b.titleType = 'movie'
              AND b.startYear IS NOT NULL
              AND b.runtimeMinutes IS NOT NULL
              AND b.genres IS NOT NULL
              AND r.averageRating IS NOT NULL
        ),
        -- Получаем первого режиссера через MIN(ordering)
        director_ord AS (
            SELECT tconst, MIN(ordering) as min_ord
            FROM title_principals
            WHERE category = 'director' AND tconst IN (SELECT tconst FROM batch_movies)
            GROUP BY tconst
        ),
        first_director AS (
            SELECT p.tconst, p.nconst
            FROM title_principals p
            JOIN director_ord d ON p.tconst = d.tconst AND p.ordering = d.min_ord
        ),
        -- Предварительно агрегируем статистику режиссеров для этого батча
        director_stats_batch AS (
            SELECT p.nconst, AVG(r.averageRating) as avg_rating
            FROM title_principals p
            JOIN title_basics b ON p.tconst = b.tconst
            JOIN title_ratings r ON b.tconst = r.tconst
            WHERE p.category = 'director'
              AND p.nconst IN (SELECT nconst FROM first_director)
              AND b.titleType = 'movie'
            GROUP BY p.nconst
            HAVING COUNT(*) >= 2
        ),
        -- Получаем первых 3 актеров через QUALIFY ROW_NUMBER
        actor_ord AS (
            SELECT tconst, ordering, nconst
            FROM title_principals
            WHERE category IN ('actor', 'actress') 
              AND tconst IN (SELECT tconst FROM batch_movies)
            QUALIFY ROW_NUMBER() OVER (PARTITION BY tconst ORDER BY ordering) <= 3
        ),
        -- Предварительно агрегируем статистику актеров для этого батча
        actor_stats_batch AS (
            SELECT p.nconst, AVG(r.averageRating) as avg_rating
            FROM title_principals p
            JOIN title_basics b ON p.tconst = b.tconst
            JOIN title_ratings r ON b.tconst = r.tconst
            WHERE p.category IN ('actor', 'actress')
              AND p.nconst IN (SELECT nconst FROM actor_ord)
              AND b.titleType = 'movie'
            GROUP BY p.nconst
            HAVING COUNT(*) >= 3
        ),
        -- Pivot актеров
        actors_pivot AS (
            SELECT 
                tconst,
                MAX(CASE WHEN ordering = 1 THEN nconst END) as actor_1_nconst,
                MAX(CASE WHEN ordering = 2 THEN nconst END) as actor_2_nconst,
                MAX(CASE WHEN ordering = 3 THEN nconst END) as actor_3_nconst
            FROM actor_ord
            GROUP BY tconst
        )
        SELECT 
            bm.tconst,
            bm.primaryTitle,
            bm.startYear,
            bm.runtimeMinutes,
            bm.genres,
            bm.averageRating,
            bm.numVotes,
            fd.nconst as director_nconst,
            ds.avg_rating as director_avg_rating,
            ap.actor_1_nconst,
            ap.actor_2_nconst,
            ap.actor_3_nconst,
            a1s.avg_rating as actor_1_avg_rating,
            a2s.avg_rating as actor_2_avg_rating,
            a3s.avg_rating as actor_3_avg_rating
        FROM batch_movies bm
        LEFT JOIN first_director fd ON bm.tconst = fd.tconst
        LEFT JOIN director_stats_batch ds ON fd.nconst = ds.nconst
        LEFT JOIN actors_pivot ap ON bm.tconst = ap.tconst
        LEFT JOIN actor_stats_batch a1s ON ap.actor_1_nconst = a1s.nconst
        LEFT JOIN actor_stats_batch a2s ON ap.actor_2_nconst = a2s.nconst
        LEFT JOIN actor_stats_batch a3s ON ap.actor_3_nconst = a3s.nconst
    """

    total_processed = 0
    batches_count = 0
    offset = 0
    
    try:
        while True:
            if max_batches is not None and batches_count >= max_batches:
                logger.info(f"Достигнут лимит батчей: {max_batches}")
                break

            # 1. Получаем только ID для текущего батча
            ids_rows = conn.execute(id_query, [batch_size, offset]).fetchall()
            
            if not ids_rows:
                logger.info("Данные в базе исчерпаны")
                break

            tconsts_batch = [row[0] for row in ids_rows]
            logger.debug(f"Получено {len(tconsts_batch)} ID фильмов (Offset: {offset})")
            
            # 2. Обогащаем данные только для этих ID
            enriched_rows = conn.execute(enrich_query, [tconsts_batch]).fetchall()
            
            if not enriched_rows:
                offset += batch_size
                continue

            logger.debug(f"Обогащено {len(enriched_rows)} записей")

            df_batch = pd.DataFrame(enriched_rows, columns=[
                'tconst', 'primaryTitle', 'startYear', 'runtimeMinutes', 'genres', 
                'averageRating', 'numVotes', 'director_nconst', 'director_avg_rating',
                'actor_1_nconst', 'actor_2_nconst', 'actor_3_nconst',
                'actor_1_avg_rating', 'actor_2_avg_rating', 'actor_3_avg_rating'
            ])

            # Преобразование типов данных
            numeric_cols = ['startYear', 'runtimeMinutes', 'averageRating', 'numVotes',
                            'director_avg_rating', 'actor_1_avg_rating', 'actor_2_avg_rating', 'actor_3_avg_rating']
            
            for col in numeric_cols:
                df_batch[col] = pd.to_numeric(df_batch[col], errors='coerce')

            # Удаление NaN в ключевых колонках
            df_batch = df_batch.dropna(subset=['startYear', 'runtimeMinutes', 'averageRating', 'numVotes'])

            if len(df_batch) == 0:
                offset += batch_size
                continue

            # Фильтрация выбросов
            df_batch = df_batch[
                (df_batch['startYear'] >= 1900) & (df_batch['startYear'] <= 2030) &
                (df_batch['runtimeMinutes'] >= 10) & (df_batch['runtimeMinutes'] <= 300)
            ]
            
            if len(df_batch) > 0:
                q1 = df_batch['runtimeMinutes'].quantile(0.05)
                q3 = df_batch['runtimeMinutes'].quantile(0.95)
                df_batch = df_batch[(df_batch['runtimeMinutes'] >= q1) & (df_batch['runtimeMinutes'] <= q3)]

            if len(df_batch) == 0:
                offset += batch_size
                continue

            # Бинарные жанры (для совместимости со старым кодом, но можно убрать)
            genre_df = pd.DataFrame(0, index=df_batch.index, columns=genres, dtype=np.float32)
            for idx, genres_str in enumerate(df_batch['genres']):
                if isinstance(genres_str, str):
                    for g in genres_str.split(','):
                        if g in genres:
                            genre_df.loc[idx, g] = 1

            # Числовые признаки
            numeric_df = pd.DataFrame(index=df_batch.index, dtype=np.float32)
            numeric_df['startYear'] = (df_batch['startYear'] - 1900) / 100.0
            numeric_df['runtimeMinutes'] = df_batch['runtimeMinutes'] / 100.0
            numeric_df['numVotes'] = np.log1p(df_batch['numVotes']).astype(np.float32)
            
            if use_director_stats:
                numeric_df['director_avg_rating'] = df_batch['director_avg_rating'].astype(np.float32)

            if use_actor_stats:
                for i in range(3):
                    col_name = f'actor_{i+1}_avg_rating'
                    numeric_df[col_name] = df_batch[col_name].astype(np.float32)

            # Категориальные признаки для CatBoost
            categorical_df = pd.DataFrame(index=df_batch.index)
            categorical_df['genres_combined'] = df_batch['genres'].fillna('Unknown')
            categorical_df['director_id'] = df_batch['director_nconst'].fillna('Unknown')
            categorical_df['actor_ids_combined'] = (
                df_batch['actor_1_nconst'].fillna('') + ',' +
                df_batch['actor_2_nconst'].fillna('') + ',' +
                df_batch['actor_3_nconst'].fillna('')
            ).str.strip(',')
            categorical_df['actor_ids_combined'] = categorical_df['actor_ids_combined'].replace('', 'Unknown')

            y = df_batch['averageRating'].astype(np.float32)
            X = pd.concat([numeric_df, categorical_df], axis=1)

            # Финальная очистка от NaN (только для числовых признаков)
            numeric_cols = numeric_df.columns.tolist()
            mask = ~X[numeric_cols].isna().any(axis=1)
            X = X[mask]
            y = y[mask]

            if len(X) == 0:
                offset += batch_size
                continue

            total_processed += len(X)
            batches_count += 1
            logger.info(f"Батч {batches_count}: {len(X)} строк. Всего: {total_processed}")
            
            yield X, y, df_batch.loc[mask, 'primaryTitle'].tolist(), df_batch.loc[mask, 'tconst'].tolist()

            # Очистка памяти
            del df_batch, genre_df, numeric_df, X, y, mask, ids_rows, enriched_rows, tconsts_batch
            gc.collect()
            
            offset += batch_size

    except Exception as e:
        logger.error(f"Ошибка при генерации батчей: {e}")
        raise
    finally:
        conn.close()
        logger.info("Соединение с БД закрыто")

def save_metadata(all_genres, model, scaler):
    """Сохраняет метаданные и scaler. Статистика теперь встроена в SQL запросы."""
    model_dir = Path(f"{config.ABSPATH}/models")
    model_dir.mkdir(parents=True, exist_ok=True)
    
    metadata = {
        'genres': all_genres,
        'feature_names': all_genres + ['startYear', 'runtimeMinutes', 'numVotes', 'director_avg_rating'] +
                         [f'actor_{i+1}_avg_rating' for i in range(3)]
    }
    
    with open(model_dir / 'metadata.pkl', 'wb') as f:
        pickle.dump(metadata, f)
    
    with open(model_dir / 'scaler.pkl', 'wb') as f:
        pickle.dump(scaler, f)
    
    logger.info("Метаданные сохранены")


def load_metadata():
    """Загружает метаданные и scaler"""
    model_dir = Path(f"{config.ABSPATH}/models")
    
    with open(model_dir / 'metadata.pkl', 'rb') as f:
        metadata = pickle.load(f)
    
    with open(model_dir / 'scaler.pkl', 'rb') as f:
        scaler = pickle.load(f)
    
    return metadata, scaler

