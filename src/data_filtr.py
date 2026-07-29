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
    """
    logger.info("Вычисление статистики по режиссёрам и актёрам")

    # Настраиваем DuckDB для работы с ограниченной памятью
    conn = duckdb.connect(f"{config.ABSPATH}/imdb.duckdb")

    conn.execute("SET memory_limit = '512MB'")
    temp_dir = Path(f"{config.ABSPATH}/temp")
    temp_dir.mkdir(parents=True, exist_ok=True)
    conn.execute(f"SET temp_directory = '{temp_dir}'")

    # Получаем данные о режиссёрах и актёрах с рейтингами
    query = """
    WITH movie_ratings AS (
        SELECT
            b.tconst,
            b.primaryTitle,
            b.startYear,
            r.averageRating,
            r.numVotes
        FROM title_basics b
        JOIN title_ratings r ON b.tconst = r.tconst
        WHERE b.titleType = 'movie'
          AND b.startYear IS NOT NULL
          AND r.averageRating IS NOT NULL
    ),
    principals_with_rating AS (
        SELECT
            p.tconst,
            p.nconst,
            p.category,
            mr.startYear,
            mr.averageRating,
            mr.numVotes
        FROM title_principals p
        JOIN movie_ratings mr ON p.tconst = mr.tconst
        WHERE p.category IN ('director', 'actor', 'actress')
    )
    SELECT * FROM principals_with_rating
    ORDER BY startYear
    """


    logger.info("Выполнение запроса для статистики персон")
    df = conn.execute(query).df()
    conn.close()
    logger.info(f"Получено {len(df)} записей о персонах")

    director_avg = {}
    actor_avg = {}

    # Директора
    logger.info("Обработка режиссёров...")
    directors = df[df['category'] == 'director']
    if len(directors) > 0:
        director_stats = directors.groupby('nconst').agg({
            'averageRating': 'mean',
            'tconst': 'count'
        }).reset_index()
        director_stats.columns = ['nconst', 'avg_rating', 'movie_count']
        # Сохраняем только тех, у кого больше 1 фильма
        director_stats = director_stats[director_stats['movie_count'] >= 2]
        director_avg = dict(zip(director_stats['nconst'], director_stats['avg_rating']))
        logger.info(f"Найдено режиссёров: {len(director_avg)}")

    # Актёры
    logger.info("Обработка актёров...")
    actors = df[df['category'].isin(['actor', 'actress'])]
    if len(actors) > 0:
        actor_stats = actors.groupby('nconst').agg({
            'averageRating': 'mean',
            'tconst': 'count'
        }).reset_index()
        actor_stats.columns = ['nconst', 'avg_rating', 'movie_count']
        # Сохраняем только тех, у кого больше 2 фильмов (чтобы статистика была надёжнее)
        actor_stats = actor_stats[actor_stats['movie_count'] >= 3]
        actor_avg = dict(zip(actor_stats['nconst'], actor_stats['avg_rating']))
        logger.info(f"Найдено актёров: {len(actor_avg)}")

    del df, directors, actors, director_stats, actor_stats
    gc.collect()

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
    """
    conn = duckdb.connect(f"{config.ABSPATH}/imdb.duckdb")
    query = """
        SELECT
            tconst,
            nconst,
            category
        FROM title_principals
        WHERE category IN ('director', 'actor', 'actress')
    """
    df = conn.execute(query).df()
    conn.close()

    result = {}
    for _, row in df.iterrows():
        tconst = row['tconst']
        nconst = row['nconst']
        category = row['category']

        if tconst not in result:
            result[tconst] = {'directors': [], 'actors': []}

        if category == 'director':
            if nconst not in result[tconst]['directors']:
                result[tconst]['directors'].append(nconst)
        else:  # actor/actress
            if nconst not in result[tconst]['actors']:
                result[tconst]['actors'].append(nconst)

    logger.info(f"Найдено связей фильм-персона: {len(result)}")
    return result


def get_batches(
    genres: list,
    batch_size: int = 10000,
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
    """
    logger.info("Инициализация генератора батчей")

    # Настраиваем DuckDB для работы с ограниченной памятью
    conn = duckdb.connect(f"{config.ABSPATH}/imdb.duckdb")

    conn.execute("SET memory_limit = '512MB'")
    temp_dir = Path(f"{config.ABSPATH}/temp")
    temp_dir.mkdir(parents=True, exist_ok=True)
    conn.execute(f"SET temp_directory = '{temp_dir}'")

    logger.info("Загрузка статистики по режиссёрам и актёрам")
    director_avg, actor_avg = get_director_actor_stats()
    tconst_to_people = get_tconst_to_nconst()
    logger.info(f"Загружено {len(director_avg)} режиссёров и {len(actor_avg)} актёров")

    query = """
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
        ORDER BY b.startYear
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
                'tconst', 'primaryTitle', 'startYear', 'runtimeMinutes', 'genres', 'averageRating', 'numVotes'
            ])

            # Приведение к числам
            logger.debug("Преобразование типов данных")
            df_batch['startYear'] = pd.to_numeric(df_batch['startYear'], errors='coerce')
            df_batch['runtimeMinutes'] = pd.to_numeric(df_batch['runtimeMinutes'], errors='coerce')
            df_batch['averageRating'] = pd.to_numeric(df_batch['averageRating'], errors='coerce')
            df_batch['numVotes'] = pd.to_numeric(df_batch['numVotes'], errors='coerce')

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

            # Добавляем признаки режиссёра и актёров
            if use_director_stats:
                logger.debug("Добавление признаков режиссёра")
                numeric_df['director_avg_rating'] = df_batch['tconst'].apply(
                    lambda t: director_avg.get(tconst_to_people.get(t, {}).get('directors', [None])[0], np.nan)
                    if tconst_to_people.get(t, {}).get('directors') else np.nan
                ).astype(np.float32)

            if use_actor_stats:
                logger.debug("Добавление признаков актёров")
                # До 5 актёров
                for i in range(5):
                    col_name = f'actor_{i+1}_avg_rating'
                    numeric_df[col_name] = df_batch['tconst'].apply(
                        lambda t, idx=i: (
                            actor_avg.get(tconst_to_people.get(t, {}).get('actors', [None]*idx)[idx], np.nan)
                            if len(tconst_to_people.get(t, {}).get('actors', [])) > idx
                            else np.nan
                        )
                    ).astype(np.float32)

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