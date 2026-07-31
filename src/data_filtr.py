import gc
import pickle
from pathlib import Path
from typing import Generator, List, Optional, Tuple

import numpy as np
import pandas as pd
import duckdb

from src.database import db_connector
from src.logger import setup_logger
from settings import config
from src.normalize import extract_title_features, normalize_genre_str

logger = setup_logger(__name__)

@db_connector
def get_genre_counts(db: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    query = """
        SELECT unnest(string_split(genres, ',')) AS genre, COUNT(*) AS cnt
        FROM title_basics
        WHERE titleType = 'movie' AND genres IS NOT NULL
        GROUP BY genre
    """
    df = db.execute(query).df()
    return df.set_index('genre')['cnt']

@db_connector
def get_all_genres(db: duckdb.DuckDBPyConnection) -> list[str]:
    query = f""" 
        SELECT DISTINCT unnest(string_split(genres, ',')) AS genre
        FROM title_basics
        WHERE titleType = 'movie' AND genres IS NOT NULL
    """

    df_genres = db.execute(query).df()

    genres = df_genres['genre'].tolist()
    # Удаляем возможный пустой жанр

    genres: list[str] = [g for g in genres if g and g.strip()]
    logger.info(f"Найдено жанров: {len(genres)}")
    return genres

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
    # Открываем соединение с настройками для малой памяти
    conn = duckdb.connect(f"{config.ABSPATH}/imdb.duckdb")
    conn.execute("SET memory_limit = '700MB'")
    conn.execute("SET threads = 2")
    conn.execute("SET preserve_insertion_order = false")
    temp_dir = Path(f"{config.ABSPATH}/temp")
    temp_dir.mkdir(parents=True, exist_ok=True)
    conn.execute(f"SET temp_directory = '{temp_dir}'")

    # Запрос для получения пакета ID фильмов
    id_query = """
        SELECT tconst FROM title_basics 
        WHERE titleType = 'movie' AND startYear IS NOT NULL AND tconst > ?
        ORDER BY tconst 
        LIMIT ?
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
    last_tconst = ''
    
    try:
        while True:
            if max_batches is not None and batches_count >= max_batches:
                logger.info(f"Достигнут лимит батчей: {max_batches}")
                break

            # 1. Получаем только ID для текущего батча
            ids_rows = conn.execute(id_query, [last_tconst, batch_size]).fetchall()
            if not ids_rows:
                logger.info("Данные в базе исчерпаны")
                break

            tconsts_batch = [row[0] for row in ids_rows]
            last_tconst = tconsts_batch[-1]
            
            # 2. Обогащаем данные только для этих ID
            enriched_rows = conn.execute(enrich_query, [tconsts_batch]).fetchall()
            if not enriched_rows:
                # Если фильмы не имеют рейтингов (редко) – пропускаем
                continue
            logger.debug(f"Обогащено {len(enriched_rows)} записей")

            df_batch = pd.DataFrame(enriched_rows, columns=[
                'tconst', 'primaryTitle', 'startYear', 'runtimeMinutes', 'genres',
                'averageRating', 'numVotes', 'director_nconst', 'director_avg_rating',
                'actor_1_nconst', 'actor_2_nconst', 'actor_3_nconst',
                'actor_1_avg_rating', 'actor_2_avg_rating', 'actor_3_avg_rating'
            ])

            # Преобразование типов
            numeric_cols = ['startYear', 'runtimeMinutes', 'averageRating', 'numVotes',
                            'director_avg_rating', 'actor_1_avg_rating',
                            'actor_2_avg_rating', 'actor_3_avg_rating']
            
            for col in numeric_cols:
                df_batch[col] = pd.to_numeric(df_batch[col], errors='coerce')

            # Удаление NaN в ключевых колонках
            df_batch = df_batch.dropna(subset=['startYear', 'runtimeMinutes', 'averageRating', 'numVotes'])

            if len(df_batch) == 0:
                continue

           # Фильтрация по разумным границам (без квартилей)
            df_batch = df_batch[
                (df_batch['startYear'] >= 1900) & (df_batch['startYear'] <= 2030) &
                (df_batch['runtimeMinutes'] >= 10) & (df_batch['runtimeMinutes'] <= 300)
            ]
            if len(df_batch) == 0:
                continue

            # Числовые признаки
            numeric_df = pd.DataFrame(index=df_batch.index, dtype=np.float32)
            numeric_df['startYear'] = (df_batch['startYear'] - 1900) / 100.0
            numeric_df['runtimeMinutes'] = df_batch['runtimeMinutes'] / 100.0
            numeric_df['numVotes_log'] = np.log1p(df_batch['numVotes']).astype(np.float32)
            
            if use_director_stats:
                numeric_df['director_avg_rating'] = df_batch['director_avg_rating'].astype(np.float32)
            if use_actor_stats:
                for i in range(3):
                    col = f'actor_{i+1}_avg_rating'
                    numeric_df[col] = df_batch[col].astype(np.float32)

            title_features = df_batch['primaryTitle'].apply(extract_title_features).apply(pd.Series)
            for col in title_features.columns:
                if col.startswith('is_') or col in ['has_digit', 'has_colon']:
                    numeric_df[col] = title_features[col].astype(np.float32)
                else:
                    # длина, кол-во слов - тоже числовые
                    numeric_df[col] = title_features[col].astype(np.float32)

            # Категориальные признаки
            categorical_df = pd.DataFrame(index=df_batch.index)
            categorical_df['genres_combined'] = df_batch['genres'].fillna('Unknown').apply(normalize_genre_str)
            categorical_df['director_id'] = df_batch['director_nconst'].fillna('Unknown')
            categorical_df['actor_ids_combined'] = (
                df_batch['actor_1_nconst'].fillna('') + ',' +
                df_batch['actor_2_nconst'].fillna('') + ',' +
                df_batch['actor_3_nconst'].fillna('')
            ).str.strip(',')
            categorical_df['actor_ids_combined'] = categorical_df['actor_ids_combined'].replace('', 'Unknown')

            title_features_df = df_batch['primaryTitle'].apply(
                lambda x: pd.Series(extract_title_features(x))
            )
            # Добавляем в categorical_df или numeric_df
            for col in title_features_df.columns:
                categorical_df[col] = title_features_df[col].astype(str)  # как категориальные
                # или числовые, если это бинарные флаги - их лучше оставить числовыми

            y = df_batch['averageRating'].astype(np.float32)
            X = pd.concat([numeric_df, categorical_df], axis=1)

            # Очистка от NaN (только числовые)
            num_cols = numeric_df.columns.tolist()
            mask = ~X[num_cols].isna().any(axis=1)
            X = X[mask]
            y = y[mask]

            if len(X) == 0:
                continue

            total_processed += len(X)
            batches_count += 1
            logger.info(f"Батч {batches_count}: {len(X)} строк. Всего: {total_processed}")
            yield X, y, df_batch.loc[mask, 'primaryTitle'].tolist(), df_batch.loc[mask, 'tconst'].tolist()

             # Очистка памяти
            del df_batch, numeric_df, X, y, mask, ids_rows, enriched_rows, tconsts_batch
            gc.collect()
            
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
