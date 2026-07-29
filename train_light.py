"""
Облегченное обучение модели на синтетических данных.
Не требует загрузки IMDB датасетов и работает при ограниченном месте на диске.
"""

import numpy as np
import pandas as pd
from sklearn.linear_model import SGDRegressor
from sklearn.preprocessing import StandardScaler
from pathlib import Path
import pickle
from src.logger import setup_logger

logger = setup_logger(__name__)

# Конфигурация
GENRES = ["Action", "Comedy", "Drama", "Horror", "Romance", "Sci-Fi", "Thriller", "Documentary"]
N_SAMPLES = 5000  # количество синтетических примеров


def generate_synthetic_data(n_samples=N_SAMPLES):
    """
    Генерирует синтетические данные для обучения модели.
    Создаёт реалистичные зависимости между признаками и рейтингом.
    """
    np.random.seed(42)

    # Жанры (one-hot encoding)
    genre_data = {}
    for genre in GENRES:
        # Некоторые жанры более популярны
        prob = 0.3 if genre in ["Action", "Comedy", "Drama"] else 0.15
        genre_data[genre] = np.random.binomial(1, prob, n_samples)

    # Год выхода (1950-2023)
    start_year = np.random.randint(1950, 2024, n_samples)
    start_year_norm = (start_year - 1900) / 100.0

    # Длительность (60-180 минут)
    runtime = np.random.randint(60, 181, n_samples)
    runtime_norm = runtime / 100.0

    # Количество голосов (логарифм)
    num_votes = np.random.exponential(10, n_samples)
    num_votes_log = np.log1p(num_votes)

    # Рейтинг режиссёра (средний 6.5, std 1.0)
    director_avg = np.clip(np.random.normal(6.5, 1.0, n_samples), 1.0, 10.0)

    # Рейтинги актёров (5 актёров)
    actor_ratings = []
    for i in range(5):
        # Первый актёр обычно более известный
        mean_rating = 6.8 - i * 0.2
        std_rating = 1.0 - i * 0.1
        actor_avg = np.clip(np.random.normal(mean_rating, max(std_rating, 0.5), n_samples), 1.0, 10.0)
        actor_ratings.append(actor_avg)

    # Формируем целевой рейтинг с реалистичными зависимостями
    rating = (
        5.0  # базовый рейтинг
        + 0.3 * genre_data["Drama"]  # драмы чуть выше
        + 0.2 * genre_data["Sci-Fi"]  # фантастика популярна
        - 0.2 * genre_data["Horror"]  # хорроры ниже
        + 0.02 * (start_year - 2000)  # новые фильмы чуть лучше
        + 0.01 * (runtime - 100)  # оптимальная длительность ~100 мин
        + 0.3 * np.log1p(num_votes) / 10  # популярность влияет
        + 0.4 * director_avg  # режиссёр сильно влияет
        + 0.15 * sum(actor_ratings) / 5  # актёры влияют
        + np.random.normal(0, 0.5, n_samples)  # шум
    )

    # Ограничиваем рейтинг от 1 до 10
    rating = np.clip(rating, 1.0, 10.0)

    # Создаём DataFrame
    X_dict = {**genre_data}
    X_dict['startYear'] = start_year_norm
    X_dict['runtimeMinutes'] = runtime_norm
    X_dict['numVotes'] = num_votes_log
    X_dict['director_avg_rating'] = director_avg
    for i in range(5):
        X_dict[f'actor_{i+1}_avg_rating'] = actor_ratings[i]

    X = pd.DataFrame(X_dict)
    y = pd.Series(rating)

    return X, y


def train_lightweight_model():
    """
    Обучает модель на синтетических данных.
    Возвращает модель, scaler и метаданные.
    """
    logger.info("Генерация синтетических данных...")
    X, y = generate_synthetic_data(N_SAMPLES)

    logger.info(f"Данные: {X.shape[0]} примеров, {X.shape[1]} признаков")
    logger.info(f"Рейтинг: min={y.min():.2f}, max={y.max():.2f}, mean={y.mean():.2f}")

    # Инициализация модели
    scaler = StandardScaler()
    model = SGDRegressor(
        loss='huber',
        penalty='l2',
        alpha=0.0001,
        learning_rate='invscaling',
        eta0=0.01,
        power_t=0.25,
        max_iter=100,
        tol=1e-4,
        random_state=42,
        early_stopping=True,
        validation_fraction=0.1
    )

    # Обучение
    logger.info("\nОбучение модели...")
    X_scaled = scaler.fit_transform(X.values)
    model.fit(X_scaled, y.values)

    # Оценка качества
    train_score = model.score(X_scaled, y.values)
    logger.info(f"R² на обучающей выборке: {train_score:.4f}")

    # Интерпретация
    feature_names = list(X.columns)
    coef_dict = dict(zip(feature_names, model.coef_ / scaler.scale_))

    logger.info("\n=== Топ-5 положительных влияний ===")
    sorted_coef = sorted(coef_dict.items(), key=lambda x: x[1], reverse=True)
    for name, val in sorted_coef[:5]:
        logger.info(f"{name}: {val:.4f}")

    logger.info("\n=== Топ-5 отрицательных влияний ===")
    for name, val in sorted_coef[-5:]:
        logger.info(f"{name}: {val:.4f}")

    return model, scaler, feature_names


def save_model(model, scaler, feature_names):
    """Сохраняет модель и метаданные"""
    model_dir = Path("/workspace/models_light")
    model_dir.mkdir(parents=True, exist_ok=True)

    with open(model_dir / 'model.pkl', 'wb') as f:
        pickle.dump(model, f)

    with open(model_dir / 'scaler.pkl', 'wb') as f:
        pickle.dump(scaler, f)

    metadata = {'genres': GENRES, 'feature_names': feature_names}
    with open(model_dir / 'metadata.pkl', 'wb') as f:
        pickle.dump(metadata, f)

    logger.info(f"\nМодель сохранена в {model_dir}")


def load_model():
    """Загружает модель и метаданные"""
    model_dir = Path("/models_light")

    with open(model_dir / 'model.pkl', 'rb') as f:
        model = pickle.load(f)

    with open(model_dir / 'scaler.pkl', 'rb') as f:
        scaler = pickle.load(f)

    with open(model_dir / 'metadata.pkl', 'rb') as f:
        metadata = pickle.load(f)

    return model, scaler, metadata


def predict_rating(title, year, runtime, genres, director, actors):
    """
    Предсказывает рейтинг фильма.

    Args:
        title: название фильма
        year: год выхода
        runtime: длительность в минутах
        genres: список жанров
        director: имя режиссёра (не используется в лайт-версии)
        actors: список актёров (не используется в лайт-версии)

    Returns:
        predicted_rating: предсказанный рейтинг
    """
    model, scaler, metadata = load_model()

    # Подготовка признаков
    X_dict = {}
    for genre in GENRES:
        X_dict[genre] = 1 if genre in genres else 0

    X_dict['startYear'] = (year - 1900) / 100.0
    X_dict['runtimeMinutes'] = runtime / 100.0
    X_dict['numVotes'] = np.log1p(10000)  # среднее значение
    X_dict['director_avg_rating'] = 6.5  # среднее значение
    for i in range(5):
        X_dict[f'actor_{i+1}_avg_rating'] = 6.5  # среднее значение

    X = pd.DataFrame([X_dict])
    X_scaled = scaler.transform(X.values)

    rating = model.predict(X_scaled)[0]
    rating = np.clip(rating, 1.0, 10.0)

    return rating


if __name__ == "__main__":
    logger.info("=" * 60)
    logger.info("ОБЛЕГЧЕННОЕ ОБУЧЕНИЕ МОДЕЛИ КИНОВАНГА")
    logger.info("=" * 60)

    model, scaler, feature_names = train_lightweight_model()
    save_model(model, scaler, feature_names)

    logger.info("\n" + "=" * 60)
    logger.info("ТЕСТИРОВАНИЕ ПРЕДСКАЗАНИЙ")
    logger.info("=" * 60)

    # Тестовые примеры
    test_movies = [
        {
            "title": "Научная фантастика 2021",
            "year": 2021,
            "runtime": 155,
            "genres": ["Sci-Fi", "Adventure"],
            "director": "Unknown",
            "actors": []
        },
        {
            "title": "Комедия 1990",
            "year": 1990,
            "runtime": 95,
            "genres": ["Comedy", "Romance"],
            "director": "Unknown",
            "actors": []
        },
        {
            "title": "Хоррор 2015",
            "year": 2015,
            "runtime": 100,
            "genres": ["Horror", "Thriller"],
            "director": "Unknown",
            "actors": []
        }
    ]

    for movie in test_movies:
        rating = predict_rating(
            movie["title"],
            movie["year"],
            movie["runtime"],
            movie["genres"],
            movie["director"],
            movie["actors"]
        )
        logger.info(f"\n{movie['title']} ({movie['year']})")
        logger.info(f"Жанры: {', '.join(movie['genres'])}")
        logger.info(f"Длительность: {movie['runtime']} мин")
        logger.info(f"Предсказанный рейтинг: {rating:.2f}/10")

    logger.info("\n" + "=" * 60)
    logger.info("ОБУЧЕНИЕ ЗАВЕРШЕНО УСПЕШНО!")
    logger.info("=" * 60)