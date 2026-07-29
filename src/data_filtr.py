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
    Возвращает X (признаки) и y (рейтинг).

    Признаки:
    - binary жанры
    - startYear (нормализованный)
    - runtimeMinutes (нормализованный)
    - numVotes (логарифмированный)
    - director_avg_rating (средний рейтинг режиссёра)
    - actor_1_avg_rating, actor_2_avg_rating, ... (до 5 актёров)
    
    ВАЖНО: Все данные загружаются через DuckDB с JOIN'ами прямо в запросе.
    Статистика по персонам вычисляется заранее в DuckDB и сохраняется во временные таблицы.
    Никакие большие словари в память не загружаются.
    """
    logger.info("Инициализация генератора батчей")

    # Настраиваем DuckDB для работы с ограниченной памятью
    conn = duckdb.connect(f"{config.ABSPATH}/imdb.duckdb")

    conn.execute("SET memory_limit = '256MB'")
    temp_dir = Path(f"{config.ABSPATH}/temp")
    temp_dir.mkdir(parents=True, exist_ok=True)
    conn.execute(f"SET temp_directory = '{temp_dir}'")

    logger.info("Создание временных таблиц со статистикой по персонам")
    
    # Создаём временную таблицу со статистикой режиссёров
    conn.execute("""
        CREATE TEMP TABLE IF NOT EXISTS director_stats AS
        WITH movie_ratings AS (
            SELECT b.tconst, r.averageRating
            FROM title_basics b
            JOIN title_ratings r ON b.tconst = r.tconst
            WHERE b.titleType = 'movie' AND r.averageRating IS NOT NULL
        )
        SELECT p.nconst, AVG(mr.averageRating) AS avg_rating
        FROM title_principals p
        JOIN movie_ratings mr ON p.tconst = mr.tconst
        WHERE p.category = 'director'
        GROUP BY p.nconst
        HAVING COUNT(*) >= 2
    """)
    
    # Создаём временную таблицу со статистикой актёров
    conn.execute("""
        CREATE TEMP TABLE IF NOT EXISTS actor_stats AS
        WITH movie_ratings AS (
            SELECT b.tconst, r.averageRating
            FROM title_basics b
            JOIN title_ratings r ON b.tconst = r.tconst
            WHERE b.titleType = 'movie' AND r.averageRating IS NOT NULL
        )
        SELECT p.nconst, AVG(mr.averageRating) AS avg_rating
        FROM title_principals p
        JOIN movie_ratings mr ON p.tconst = mr.tconst
        WHERE p.category IN ('actor', 'actress')
        GROUP BY p.nconst
        HAVING COUNT(*) >= 3
    """)
    
    logger.info("Временные таблицы созданы")

    # Основной запрос с JOIN'ами для получения всех признаков сразу
    # Для каждого фильма берём первого режиссёра и первых 5 актёров
    query = """
        WITH movie_data AS (
            SELECT 
                b.tconst,
                b.primaryTitle,
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
        ),
        principals_ranked AS (
            SELECT 
                tconst,
                nconst,
                category,
                ROW_NUMBER() OVER (PARTITION BY tconst, category ORDER BY ordering) AS rn
            FROM title_principals
            WHERE category IN ('director', 'actor', 'actress')
        ),
        first_director AS (
            SELECT tconst, nconst AS director_nconst
            FROM principals_ranked
            WHERE category = 'director' AND rn = 1
        ),
        first_actors AS (
            SELECT tconst, nconst AS actor_nconst, rn AS actor_num
            FROM principals_ranked
            WHERE category IN ('actor', 'actress') AND rn <= 5
        ),
        director_ratings AS (
            SELECT fd.tconst, ds.avg_rating AS director_avg_rating
            FROM first_director fd
            LEFT JOIN director_stats ds ON fd.director_nconst = ds.nconst
        ),
        actor_ratings_pivot AS (
            SELECT 
                fa.tconst,
                MAX(CASE WHEN fa.actor_num = 1 THEN astat.avg_rating END) AS actor_1_avg_rating,
                MAX(CASE WHEN fa.actor_num = 2 THEN astat.avg_rating END) AS actor_2_avg_rating,
                MAX(CASE WHEN fa.actor_num = 3 THEN astat.avg_rating END) AS actor_3_avg_rating,
                MAX(CASE WHEN fa.actor_num = 4 THEN astat.avg_rating END) AS actor_4_avg_rating,
                MAX(CASE WHEN fa.actor_num = 5 THEN astat.avg_rating END) AS actor_5_avg_rating
            FROM first_actors fa
            LEFT JOIN actor_stats astat ON fa.actor_nconst = astat.nconst
            GROUP BY fa.tconst
        )
        SELECT 
            md.tconst,
            md.primaryTitle,
            md.startYear,
            md.runtimeMinutes,
            md.genres,
            md.averageRating,
            md.numVotes,
            dr.director_avg_rating,
            arp.actor_1_avg_rating,
            arp.actor_2_avg_rating,
            arp.actor_3_avg_rating,
            arp.actor_4_avg_rating,
            arp.actor_5_avg_rating
        FROM movie_data md
        LEFT JOIN director_ratings dr ON md.tconst = dr.tconst
        LEFT JOIN actor_ratings_pivot arp ON md.tconst = arp.tconst
        ORDER BY md.startYear
    """

    logger.info("Выполнение запроса к базе данных")
    cursor = conn.execute(query)
    total = 0
    batches_count = 0
    
    try:
        while True:
            # Проверка лимита батчей
            if max_batches is not None and batches_count >= max_batches:
                logger.info(f"Достигнут лимит батчей: {max_batches}")
                break

            rows = cursor.fetchmany(batch_size)
            if not rows:
                logger.info("Данные в базе исчерпаны")
                break

            logger.debug(f"Получено {len(rows)} строк из БД")

            df_batch = pd.DataFrame(rows, columns=[
                'tconst', 'primaryTitle', 'startYear', 'runtimeMinutes', 'genres', 
                'averageRating', 'numVotes', 'director_avg_rating',
                'actor_1_avg_rating', 'actor_2_avg_rating', 'actor_3_avg_rating',
                'actor_4_avg_rating', 'actor_5_avg_rating'
            ])

            # Приведение к числам
            logger.debug("Преобразование типов данных")
            df_batch['startYear'] = pd.to_numeric(df_batch['startYear'], errors='coerce')
            df_batch['runtimeMinutes'] = pd.to_numeric(df_batch['runtimeMinutes'], errors='coerce')
            df_batch['averageRating'] = pd.to_numeric(df_batch['averageRating'], errors='coerce')
            df_batch['numVotes'] = pd.to_numeric(df_batch['numVotes'], errors='coerce')
            
            # Приводим признаки актёров и режиссёров к float32
            for col in ['director_avg_rating', 'actor_1_avg_rating', 'actor_2_avg_rating', 
                       'actor_3_avg_rating', 'actor_4_avg_rating', 'actor_5_avg_rating']:
                df_batch[col] = pd.to_numeric(df_batch[col], errors='coerce').astype(np.float32)

            # Удаление NaN в ключевых колонках
            initial_count = len(df_batch)
            df_batch = df_batch.dropna(subset=['startYear', 'runtimeMinutes', 'averageRating', 'numVotes'])
            if len(df_batch) < initial_count:
                logger.debug(f"Удалено {initial_count - len(df_batch)} строк с NaN")

            # Фильтрация выбросов
            initial_count = len(df_batch)
            df_batch = df_batch[
                (df_batch['startYear'] >= 1900) & (df_batch['startYear'] <= 2030) &
                (df_batch['runtimeMinutes'] >= 10) & (df_batch['runtimeMinutes'] <= 300)
            ]
            if len(df_batch) < initial_count:
                logger.debug(f"Удалено {initial_count - len(df_batch)} строк как выбросы по году/длительности")

            q1 = df_batch['runtimeMinutes'].quantile(0.05)
            q3 = df_batch['runtimeMinutes'].quantile(0.95)
            initial_count = len(df_batch)
            df_batch = df_batch[(df_batch['runtimeMinutes'] >= q1) & (df_batch['runtimeMinutes'] <= q3)]
            if len(df_batch) < initial_count:
                logger.debug(f"Удалено {initial_count - len(df_batch)} строк как выбросы по квантилям")

            if len(df_batch) == 0:
                logger.debug("Батч пуст после фильтрации, пропускаем")
                continue

            # Бинарные жанры
            logger.debug(f"Создание признаков жанров для {len(df_batch)} записей")
            genre_df = pd.DataFrame(0, index=df_batch.index, columns=genres, dtype=np.float32)
            for idx, genres_str in enumerate(df_batch['genres']):
                if genres_str:
                    for g in genres_str.split(','):
                        if g in genres:
                            genre_df.loc[idx, g] = 1

            # Числовые признаки с ручным масштабированием
            logger.debug("Создание числовых признаков")
            numeric_df = pd.DataFrame(index=df_batch.index, dtype=np.float32)
            numeric_df['startYear'] = (df_batch['startYear'] - 1900) / 100.0
            numeric_df['runtimeMinutes'] = df_batch['runtimeMinutes'] / 100.0
            numeric_df['numVotes'] = np.log1p(df_batch['numVotes']).astype(np.float32)
            
            # Добавляем признаки режиссёра и актёров (уже есть в df_batch)
            if use_director_stats:
                logger.debug("Добавление признаков режиссёра")
                numeric_df['director_avg_rating'] = df_batch['director_avg_rating']

            if use_actor_stats:
                logger.debug("Добавление признаков актёров")
                for i in range(5):
                    col_name = f'actor_{i+1}_avg_rating'
                    numeric_df[col_name] = df_batch[col_name]

            y = df_batch['averageRating'].astype(np.float32)
            X = pd.concat([genre_df, numeric_df], axis=1)

            # --- ФИНАЛЬНАЯ ОЧИСТКА ОТ NaN ---
            initial_count = len(X)
            mask = ~(X.isna().any(axis=1) | y.isna())
            X = X[mask]
            y = y[mask]
            if len(X) < initial_count:
                logger.debug(f"Удалено {initial_count - len(X)} строк с NaN в признаках")

            if len(X) == 0:
                logger.debug("Батч пуст после очистки от NaN, пропускаем")
                continue

            total += len(X)
            batches_count += 1
            logger.info(f"Батч {batches_count}: обработано строк {len(X)}, всего: {total}")
            yield X, y, df_batch.loc[mask, 'primaryTitle'].tolist(), df_batch.loc[mask, 'tconst'].tolist()

            # Освобождаем память
            del df_batch, genre_df, numeric_df, X, y, mask
            gc.collect()

    except Exception as e:
        logger.error(f"Ошибка при генерации батчей: {e}")
        raise
    finally:
        conn.close()
        logger.info("Соединение с БД закрыто")

def save_metadata(all_genres, model, scaler, director_avg, actor_avg, tconst_to_people, nconst_mapping):
    """Сохраняет метаданные и вспомогательные структуры"""
    model_dir = Path(f"{config.ABSPATH}/models")
    model_dir.mkdir(parents=True, exist_ok=True)

    metadata = {
        'genres': all_genres,
        'feature_names': all_genres + ['startYear', 'runtimeMinutes', 'numVotes', 'director_avg_rating'] +
                         [f'actor_{i+1}_avg_rating' for i in range(5)]
    }

    with open(model_dir / 'metadata.pkl', 'wb') as f:
        pickle.dump(metadata, f)

    with open(model_dir / 'scaler.pkl', 'wb') as f:
        pickle.dump(scaler, f)

    with open(model_dir / 'director_avg.pkl', 'wb') as f:
        pickle.dump(director_avg, f)

    with open(model_dir / 'actor_avg.pkl', 'wb') as f:
        pickle.dump(actor_avg, f)

    with open(model_dir / 'tconst_to_people.pkl', 'wb') as f:
        pickle.dump(tconst_to_people, f)

    with open(model_dir / 'nconst_mapping.pkl', 'wb') as f:
        pickle.dump(nconst_mapping, f)

    logger.info("Метаданные сохранены")


def load_metadata():
    """Загружает метаданные и вспомогательные структуры"""
    model_dir = Path(f"{config.ABSPATH}/models")

    with open(model_dir / 'metadata.pkl', 'rb') as f:
        metadata = pickle.load(f)

    with open(model_dir / 'scaler.pkl', 'rb') as f:
        scaler = pickle.load(f)

    with open(model_dir / 'director_avg.pkl', 'rb') as f:
        director_avg = pickle.load(f)

    with open(model_dir / 'actor_avg.pkl', 'rb') as f:
        actor_avg = pickle.load(f)

    with open(model_dir / 'tconst_to_people.pkl', 'rb') as f:
        tconst_to_people = pickle.load(f)

    with open(model_dir / 'nconst_mapping.pkl', 'rb') as f:
        nconst_mapping = pickle.load(f)

    return metadata, scaler, director_avg, actor_avg, tconst_to_people, nconst_mapping