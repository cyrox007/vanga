"""
Демонстрация работы модели КиноВанга на синтетических данных.

Этот скрипт создаёт небольшой тестовый датасет, обучает модель и показывает
как делать предсказания.
"""

import numpy as np
import pandas as pd
import pickle
from pathlib import Path
from sklearn.linear_model import SGDRegressor
from sklearn.preprocessing import StandardScaler

from src.logger import setup_logger
from settings import config

logger = setup_logger(__name__)


def generate_synthetic_data(n_samples=5000):
    """
    Генерирует синтетические данные для обучения модели.
    
    Returns:
        DataFrame с данными фильмов
    """
    np.random.seed(42)
    
    # Жанры
    all_genres = ['Action', 'Comedy', 'Drama', 'Horror', 'Romance', 'Sci-Fi', 'Thriller', 'Documentary']
    
    data = []
    for i in range(n_samples):
        # Год (1950-2024)
        year = np.random.randint(1950, 2024)
        
        # Длительность (60-180 минут)
        runtime = np.random.randint(60, 180)
        
        # Жанры (1-3 случайных)
        n_genres = np.random.randint(1, 4)
        genres = np.random.choice(all_genres, n_genres, replace=False).tolist()
        
        # Режиссёр (из пула "известных" и "обычных")
        is_famous_director = np.random.random() < 0.2
        director_id = f"dir_{np.random.randint(1, 50)}" if is_famous_director else f"dir_{np.random.randint(50, 500)}"
        
        # Актёры (до 5)
        n_actors = np.random.randint(1, 6)
        actors = [f"actor_{np.random.randint(1, 200)}" for _ in range(n_actors)]
        
        # Базовый рейтинг зависит от жанра
        base_rating = 5.0
        if 'Drama' in genres:
            base_rating += 1.5
        if 'Documentary' in genres:
            base_rating += 1.0
        if 'Horror' in genres:
            base_rating -= 0.5
        if 'Comedy' in genres:
            base_rating += np.random.uniform(-0.5, 0.5)
        
        # Влияние года (более новые фильмы чуть выше)
        year_effect = (year - 1950) / 74 * 0.5
        
        # Влияние длительности (оптимально 90-120 минут)
        if 90 <= runtime <= 120:
            runtime_effect = 0.3
        elif runtime < 90:
            runtime_effect = -0.2
        else:
            runtime_effect = 0.1
        
        # Влияние "известного" режиссёра
        director_effect = 1.5 if is_famous_director else 0
        
        # Влияние "известных" актёров
        famous_actors = sum(1 for a in actors if int(a.split('_')[1]) <= 20)
        actor_effect = famous_actors * 0.3
        
        # Случайный шум
        noise = np.random.normal(0, 0.8)
        
        # Итоговый рейтинг
        rating = base_rating + year_effect + runtime_effect + director_effect + actor_effect + noise
        rating = max(1.0, min(10.0, rating))  # Ограничиваем 1-10
        
        # Количество голосов (зависит от года и рейтинга)
        num_votes = int(np.random.exponential(5000) * (rating / 5) * ((2024 - year + 1) / 75))
        num_votes = max(10, num_votes)
        
        data.append({
            'tconst': f'tt{i:07d}',
            'primaryTitle': f'Movie {i}',
            'year': year,
            'runtime': runtime,
            'genres': ','.join(genres),
            'director_id': director_id,
            'actors': actors,
            'averageRating': round(rating, 1),
            'numVotes': num_votes
        })
    
    return pd.DataFrame(data), all_genres


def create_training_features(df, all_genres):
    """
    Создаёт матрицу признаков из DataFrame.
    """
    # Жанр one-hot encoding
    genre_cols = []
    for genre in all_genres:
        col_name = f'genre_{genre}'
        df[col_name] = df['genres'].apply(lambda x: 1 if genre in x.split(',') else 0)
        genre_cols.append(col_name)
    
    # Числовые признаки
    df['startYear_scaled'] = (df['year'] - 1900) / 100.0
    df['runtime_scaled'] = df['runtime'] / 100.0
    df['numVotes_log'] = np.log1p(df['numVotes'])
    
    # Создаём "статистику" по режиссёрам и актёрам
    director_stats = df.groupby('director_id')['averageRating'].mean().to_dict()
    df['director_avg_rating'] = df['director_id'].map(director_stats)
    
    # Для актёров берём первого (для упрощения)
    def get_first_actor_rating(actors):
        if not actors or len(actors) == 0:
            return np.nan
        actor_id = actors[0]
        actor_movies = df[df['actors'].apply(lambda x: actor_id in x)]
        if len(actor_movies) > 0:
            return actor_movies['averageRating'].mean()
        return np.nan
    
    # Упрощённо: просто используем случайное значение для демонстрации
    df['actor_1_avg_rating'] = df['averageRating'] * np.random.uniform(0.8, 1.2, len(df))
    for i in range(2, 6):
        df[f'actor_{i}_avg_rating'] = df['averageRating'] * np.random.uniform(0.7, 1.3, len(df))
    
    # Заполняем NaN средними
    df['director_avg_rating'] = df['director_avg_rating'].fillna(df['averageRating'].mean())
    for i in range(1, 6):
        df[f'actor_{i}_avg_rating'] = df[f'actor_{i}_avg_rating'].fillna(df['averageRating'].mean())
    
    # Формируем X и y
    feature_cols = genre_cols + ['startYear_scaled', 'runtime_scaled', 'numVotes_log', 'director_avg_rating'] + \
                   [f'actor_{i}_avg_rating' for i in range(1, 6)]
    
    X = df[feature_cols].values
    y = df['averageRating'].values
    
    return X, y, feature_cols


def train_synthetic_model():
    """
    Обучает модель на синтетических данных.
    """
    logger.info("Генерация синтетических данных...")
    df, all_genres = generate_synthetic_data(5000)
    
    logger.info("Создание признаков...")
    X, y, feature_names = create_training_features(df, all_genres)
    
    # Разделение на train/test
    from sklearn.model_selection import train_test_split
    X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42)
    
    # Стандартизация
    scaler = StandardScaler()
    X_train_scaled = scaler.fit_transform(X_train)
    X_test_scaled = scaler.transform(X_test)
    
    # Обучение модели
    logger.info("Обучение модели...")
    model = SGDRegressor(
        loss='huber',
        penalty='l2',
        alpha=0.001,
        learning_rate='adaptive',
        eta0=0.01,
        max_iter=1000,
        tol=1e-3,
        random_state=42
    )
    model.fit(X_train_scaled, y_train)
    
    # Оценка качества
    train_score = model.score(X_train_scaled, y_train)
    test_score = model.score(X_test_scaled, y_test)
    
    logger.info(f"R² на train: {train_score:.4f}")
    logger.info(f"R² на test: {test_score:.4f}")
    
    # Интерпретация
    logger.info("\n=== Важность признаков ===")
    coef_dict = dict(zip(feature_names, model.coef_))
    sorted_coef = sorted(coef_dict.items(), key=lambda x: abs(x[1]), reverse=True)
    
    for name, coef in sorted_coef[:10]:
        logger.info(f"{name}: {coef:.4f}")
    
    # Сохранение модели
    model_dir = Path(f"{config.ABSPATH}/models")
    model_dir.mkdir(parents=True, exist_ok=True)
    
    with open(model_dir / 'model.pkl', 'wb') as f:
        pickle.dump(model, f)
    
    with open(model_dir / 'scaler.pkl', 'wb') as f:
        pickle.dump(scaler, f)
    
    # Метаданные
    metadata = {
        'genres': all_genres,
        'feature_names': feature_names
    }
    with open(model_dir / 'metadata.pkl', 'wb') as f:
        pickle.dump(metadata, f)
    
    # Пустые словари для совместимости
    with open(model_dir / 'director_avg.pkl', 'wb') as f:
        pickle.dump({}, f)
    with open(model_dir / 'actor_avg.pkl', 'wb') as f:
        pickle.dump({}, f)
    with open(model_dir / 'tconst_to_people.pkl', 'wb') as f:
        pickle.dump({}, f)
    with open(model_dir / 'nconst_mapping.pkl', 'wb') as f:
        pickle.dump({}, f)
    
    logger.info(f"\nМодель сохранена в {model_dir}")
    
    return model, scaler, metadata, all_genres


def demo_predictions(model, scaler, metadata):
    """
    Демонстрирует предсказания на примерах.
    """
    from src.kinovanga import KinoVanga
    
    # Примеры фильмов
    examples = [
        {
            'title': 'Блокбастер 2024',
            'year': 2024,
            'runtime': 140,
            'genres': ['Action', 'Sci-Fi'],
            'director': 'Famous Director',
            'actors': ['Star 1', 'Star 2', 'Star 3']
        },
        {
            'title': 'Независимая драма',
            'year': 2020,
            'runtime': 95,
            'genres': ['Drama'],
            'director': 'Indie Director',
            'actors': ['Unknown Actor']
        },
        {
            'title': 'Комедия для всех',
            'year': 2023,
            'runtime': 100,
            'genres': ['Comedy', 'Romance'],
            'director': 'Comedy Director',
            'actors': ['Comedian 1', 'Comedian 2']
        },
        {
            'title': 'Хоррор фильм',
            'year': 2022,
            'runtime': 85,
            'genres': ['Horror', 'Thriller'],
            'director': 'Horror Master',
            'actors': ['Scream Queen']
        }
    ]
    
    logger.info("\n=== Демонстрация предсказаний ===")
    
    # Используем класс KinoVanga
    try:
        kino = KinoVanga()
        
        for movie in examples:
            rating = kino.predict(
                year=movie['year'],
                runtime=movie['runtime'],
                genres=movie['genres'],
                director=movie['director'],
                actors=movie['actors'],
                title=movie['title']
            )
            logger.info(f"{movie['title']} ({movie['year']}): predicted rating = {rating}")
    except Exception as e:
        logger.warning(f"Не удалось загрузить KinoVanga: {e}")
        logger.info("Используем прямое предсказание...")
        
        # Прямое предсказание
        for movie in examples:
            # Подготовка признаков
            genre_list = movie['genres'] if isinstance(movie['genres'], list) else movie['genres'].split(',')
            genre_vector = [1 if g in genre_list else 0 for g in metadata['genres']]
            
            features = genre_vector + [
                (movie['year'] - 1900) / 100.0,
                movie['runtime'] / 100.0,
                7.5,  # log1p votes (default)
                6.5,  # director avg (default)
            ] + [6.5] * 5  # actor ratings (default)
            
            X = np.array(features).reshape(1, -1)
            X_scaled = scaler.transform(X)
            rating = model.predict(X_scaled)[0]
            rating = max(0, min(10, rating))
            
            logger.info(f"{movie['title']} ({movie['year']}): predicted rating = {rating:.2f}")


if __name__ == "__main__":
    model, scaler, metadata, all_genres = train_synthetic_model()
    demo_predictions(model, scaler, metadata)
    
    logger.info("\n✅ Демонстрация завершена!")
    logger.info("Для полноценного обучения скачайте датасеты IMDB и запустите traning.py")
