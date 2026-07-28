import numpy as np
import pandas as pd
import duckdb
import pickle
from pathlib import Path

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
    conn = duckdb.connect(f"{config.ABSPATH}/imdb.duckdb")
    
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
    
    df = conn.execute(query).df()
    conn.close()
    
    director_avg = {}
    actor_avg = {}
    
    # Для каждого человека считаем средний рейтинг ВСЕХ его работ (можно сделать скользящее среднее)
    # В реальном продакшене нужно делать leave-one-out, но для начала упростим
    
    # Директора
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


def get_batches(genres: list, batch_size=10000, use_director_stats=True, use_actor_stats=True):
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
    conn = duckdb.connect(f"{config.ABSPATH}/imdb.duckdb")
    
    # Предзагружаем статистику по режиссёрам и актёрам
    director_avg, actor_avg = get_director_actor_stats()
    tconst_to_people = get_tconst_to_nconst()
    
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
    """
    cursor = conn.execute(query)
    total = 0
    
    while True:
        rows = cursor.fetchmany(batch_size)
        if not rows:
            break
        
        df_batch = pd.DataFrame(rows, columns=[
            'tconst', 'primaryTitle', 'startYear', 'runtimeMinutes', 'genres', 'averageRating', 'numVotes'
        ])

        # Приведение к числам
        df_batch['startYear'] = pd.to_numeric(df_batch['startYear'], errors='coerce')
        df_batch['runtimeMinutes'] = pd.to_numeric(df_batch['runtimeMinutes'], errors='coerce')
        df_batch['averageRating'] = pd.to_numeric(df_batch['averageRating'], errors='coerce')
        df_batch['numVotes'] = pd.to_numeric(df_batch['numVotes'], errors='coerce')

        # Удаление NaN в ключевых колонках
        df_batch = df_batch.dropna(subset=['startYear', 'runtimeMinutes', 'averageRating', 'numVotes'])

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
        numeric_df = pd.DataFrame(index=df_batch.index)
        numeric_df['startYear'] = (df_batch['startYear'] - 1900) / 100.0
        numeric_df['runtimeMinutes'] = df_batch['runtimeMinutes'] / 100.0
        numeric_df['numVotes'] = np.log1p(df_batch['numVotes'])
        
        # Добавляем признаки режиссёра и актёров
        if use_director_stats:
            numeric_df['director_avg_rating'] = df_batch['tconst'].apply(
                lambda t: director_avg.get(tconst_to_people.get(t, {}).get('directors', [None])[0], np.nan)
                if tconst_to_people.get(t, {}).get('directors') else np.nan
            )
        
        if use_actor_stats:
            # До 5 актёров
            for i in range(5):
                col_name = f'actor_{i+1}_avg_rating'
                numeric_df[col_name] = df_batch['tconst'].apply(
                    lambda t, idx=i: (
                        actor_avg.get(tconst_to_people.get(t, {}).get('actors', [None]*idx)[idx], np.nan)
                        if len(tconst_to_people.get(t, {}).get('actors', [])) > idx
                        else np.nan
                    )
                )

        y = df_batch['averageRating'].astype(float)
        X = pd.concat([genre_df, numeric_df], axis=1)

        # --- ФИНАЛЬНАЯ ОЧИСТКА ОТ NaN ---
        mask = ~(X.isna().any(axis=1) | y.isna())
        X = X[mask]
        y = y[mask]
        
        if len(X) == 0:
            continue

        total += len(X)
        logger.info(f"Прочитано строк: {total}")
        yield X, y, df_batch.loc[mask, 'primaryTitle'].tolist(), df_batch.loc[mask, 'tconst'].tolist()
    
    conn.close()


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