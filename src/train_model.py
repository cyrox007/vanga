import numpy as np
import pickle
from pathlib import Path
from sklearn.linear_model import SGDRegressor
from sklearn.preprocessing import StandardScaler

from src.data_filtr import get_batches, save_metadata, get_nconst_mapping
from src.logger import setup_logger
from settings import config

logger = setup_logger(__name__)


def train_model(all_genres: list, batch_size=10000):
    """
    Обучает модель на батчах данных.
    Возвращает обученную модель, scaler и вспомогательные данные.
    """
    n_features = len(all_genres) + 3 + 5  # жанры + startYear + runtimeMinutes + numVotes + director_avg + 5 актёров
    scaler = StandardScaler()
    model = SGDRegressor(
        loss='huber',
        penalty='l2',
        alpha=0.0001,
        learning_rate='constant',
        eta0=0.0001,
        max_iter=1,
        warm_start=True,
        random_state=42,
        tol=1e-3,
        early_stopping=False
    )
    first_batch = True
    total_rows = 0
    for X, y, titles, tconsts in get_batches(all_genres, batch_size):
        if len(X) == 0:
            continue

        X_np = X.values.astype(np.float64)
        y_np = y.values.astype(np.float64)

        # --- Стандартизация (один раз) ---
        if first_batch:
            scaler.partial_fit(X_np)
            first_batch = False
        else:
            scaler.partial_fit(X_np)

        X_scaled = scaler.transform(X_np)

        # --- Обучение ---
        model.partial_fit(X_scaled, y_np)
        total_rows += len(y_np)

        logger.info(f"Обработано строк: {total_rows}, текущие веса (первые 5): {model.coef_[:5]}")

    logger.info("Обучение завершено")

    # Загружаем дополнительные данные для предсказаний
    director_avg, actor_avg = None, None
    tconst_to_people = None
    nconst_mapping = get_nconst_mapping()

    # Получаем статистику по режиссёрам и актёрам
    from src.data_filtr import get_director_actor_stats, get_tconst_to_nconst
    director_avg, actor_avg = get_director_actor_stats()
    tconst_to_people = get_tconst_to_nconst()

    return model, scaler, director_avg, actor_avg, tconst_to_people, nconst_mapping

def save_trained_model(model, scaler, all_genres, director_avg, actor_avg, tconst_to_people, nconst_mapping):
    """Сохраняет обученную модель и все необходимые метаданные"""
    model_dir = Path(f"{config.ABSPATH}/models")
    model_dir.mkdir(parents=True, exist_ok=True)

    # Сохраняем модель
    with open(model_dir / 'model.pkl', 'wb') as f:
        pickle.dump(model, f)

    # Сохраняем scaler
    with open(model_dir / 'scaler.pkl', 'wb') as f:
        pickle.dump(scaler, f)

    # Сохраняем метаданные
    metadata = {
        'genres': all_genres,
        'feature_names': all_genres + ['startYear', 'runtimeMinutes', 'numVotes', 'director_avg_rating'] +
                         [f'actor_{i+1}_avg_rating' for i in range(5)]
    }
    with open(model_dir / 'metadata.pkl', 'wb') as f:
        pickle.dump(metadata, f)

    # Сохраняем вспомогательные словари
    with open(model_dir / 'director_avg.pkl', 'wb') as f:
        pickle.dump(director_avg, f)

    with open(model_dir / 'actor_avg.pkl', 'wb') as f:
        pickle.dump(actor_avg, f)

    with open(model_dir / 'tconst_to_people.pkl', 'wb') as f:
        pickle.dump(tconst_to_people, f)

    with open(model_dir / 'nconst_mapping.pkl', 'wb') as f:
        pickle.dump(nconst_mapping, f)

    logger.info(f"Модель и метаданные сохранены в {model_dir}")


def load_trained_model():
    """Загружает обученную модель и все необходимые метаданные"""
    model_dir = Path(f"{config.ABSPATH}/models")

    with open(model_dir / 'model.pkl', 'rb') as f:
        model = pickle.load(f)

    with open(model_dir / 'scaler.pkl', 'rb') as f:
        scaler = pickle.load(f)

    with open(model_dir / 'metadata.pkl', 'rb') as f:
        metadata = pickle.load(f)

    with open(model_dir / 'director_avg.pkl', 'rb') as f:
        director_avg = pickle.load(f)

    with open(model_dir / 'actor_avg.pkl', 'rb') as f:
        actor_avg = pickle.load(f)

    with open(model_dir / 'tconst_to_people.pkl', 'rb') as f:
        tconst_to_people = pickle.load(f)

    with open(model_dir / 'nconst_mapping.pkl', 'rb') as f:
        nconst_mapping = pickle.load(f)

    return model, scaler, metadata, director_avg, actor_avg, tconst_to_people, nconst_mapping

def interpret_model(model, scaler, feature_names):
    """
    Выводит коэффициенты модели в исходных единицах.
    Для числовых признаков учтено ручное масштабирование.
    """
    # Коэффициенты после учёта StandardScaler
    coef_scaled = model.coef_ / scaler.scale_
    coef_dict = dict(zip(feature_names, coef_scaled))
    
    # Поправка на ручное масштабирование для года и длительности
    if 'startYear' in coef_dict:
        coef_dict['startYear'] /= 100.0
    if 'runtimeMinutes' in coef_dict:
        coef_dict['runtimeMinutes'] /= 100.0
    # Для numVotes поправка не нужна — он уже в логарифмическом масштабе
    
    # Сортируем
    features_sorted = sorted(coef_dict.items(), key=lambda x: x[1], reverse=True)
    
    logger.info("=== Топ-10 положительных влияний (в исходных единицах) ===")
    for name, val in features_sorted[:10]:
        logger.info(f"{name}: {val:.4f}")
    
    logger.info("\n=== Топ-10 отрицательных влияний ===")
    for name, val in features_sorted[-10:]:
        logger.info(f"{name}: {val:.4f}")
    
    if 'startYear' in coef_dict:
        logger.info(f"\nВлияние года выпуска (на 1 год): {coef_dict['startYear']:.4f}")
    if 'runtimeMinutes' in coef_dict:
        logger.info(f"Влияние длительности (на 1 минуту): {coef_dict['runtimeMinutes']:.4f}")
    if 'numVotes' in coef_dict:
        logger.info(f"Влияние логарифма голосов (на 1 единицу log): {coef_dict['numVotes']:.4f}")
    if 'director_avg_rating' in coef_dict:
        logger.info(f"Влияние среднего рейтинга режиссёра: {coef_dict['director_avg_rating']:.4f}")
    for i in range(5):
        col_name = f'actor_{i+1}_avg_rating'
        if col_name in coef_dict:
            logger.info(f"Влияние рейтинга актёра #{i+1}: {coef_dict[col_name]:.4f}")