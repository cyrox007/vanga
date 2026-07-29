from typing import Optional, Tuple

import numpy as np
import pickle
from pathlib import Path
from sklearn.linear_model import SGDRegressor
from sklearn.preprocessing import StandardScaler

from src.data_filtr import get_batches, save_metadata, get_nconst_mapping
from src.logger import setup_logger
from settings import config

logger = setup_logger(__name__)


def train_model(
    all_genres: list,
    batch_size: int = 10000,
    max_batches: Optional[int] = None
) -> Tuple[SGDRegressor, StandardScaler, dict, dict, dict, dict]:
    """
    Обучает модель на батчах данных.
    Возвращает обученную модель, scaler и вспомогательные данные.
    """
    logger.info("=" * 60)
    logger.info("НАЧАЛО ОБУЧЕНИЯ МОДЕЛИ")
    logger.info(f"Параметры: batch_size={batch_size}, max_batches={max_batches}")
    logger.info(f"Количество жанров: {len(all_genres)}")
    logger.info("=" * 60)

    n_features = len(all_genres) + 3 + 5  # жанры + startYear + runtimeMinutes + numVotes + director_avg + 5 актёров
    logger.info(f"Количество признаков: {n_features}")

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
    logger.info("Модель SGDRegressor инициализирована")

    first_batch = True
    total_rows = 0
    batches_processed = 0

    try:
        for X, y, titles, tconsts in get_batches(all_genres, batch_size, max_batches=max_batches):
            if len(X) == 0:
                logger.warning("Пустой батч, пропускаем")
                continue

            logger.info(f"\n--- Обработка батча {batches_processed + 1} ---")
            logger.info(f"Размер батча: {len(X)} записей")

            X_np = X.values.astype(np.float64)
            y_np = y.values.astype(np.float64)

            # --- Стандартизация (один раз) ---
            if first_batch:
                logger.info("Первый батч: инициализация scaler")
                scaler.partial_fit(X_np)
                first_batch = False
            else:
                scaler.partial_fit(X_np)

            X_scaled = scaler.transform(X_np)
            logger.debug(f"Данные масштабированы, shape: {X_scaled.shape}")

            # --- Обучение ---
            model.partial_fit(X_scaled, y_np)
            total_rows += len(y_np)
            batches_processed += 1

            logger.info(f"Обработано строк: {total_rows}")
            logger.info(f"Текущие веса (первые 5): {model.coef_[:5]}")
            logger.info(f"Свободный член: {model.intercept_[0]:.4f}")

            # Освобождаем память
            del X_np, y_np, X_scaled
            gc.collect()

    except Exception as e:
        logger.error(f"Критическая ошибка при обучении: {e}")
        raise
    finally:
        logger.info("=" * 60)
        logger.info("ОБУЧЕНИЕ ЗАВЕРШЕНО")
        logger.info(f"Всего обработано строк: {total_rows}")
        logger.info(f"Всего обработано батчей: {batches_processed}")
        logger.info(f"Финальные веса (первые 5): {model.coef_[:5]}")
        logger.info("=" * 60)

    logger.info("Загрузка дополнительных данных для предсказаний")

    # Получаем статистику по режиссёрам и актёрам
    from src.data_filtr import get_director_actor_stats, get_tconst_to_nconst
    director_avg, actor_avg = get_director_actor_stats()
    tconst_to_people = get_tconst_to_nconst()
    nconst_mapping = get_nconst_mapping()

    logger.info(f"Загружено статистики: {len(director_avg)} режиссёров, {len(actor_avg)} актёров")
    logger.info(f"Загружено связей фильм-персона: {len(tconst_to_people)}")
    logger.info(f"Загружено имён: {len(nconst_mapping)}")

    return model, scaler, director_avg, actor_avg, tconst_to_people, nconst_mapping

def save_trained_model(
    model: SGDRegressor,
    scaler: StandardScaler,
    all_genres: list,
    director_avg: dict,
    actor_avg: dict,
    tconst_to_people: dict,
    nconst_mapping: dict
) -> None:
    """
    Сохраняет обученную модель и все необходимые метаданные.

    Args:
        model: обученная модель
        scaler: объект стандартизации
        all_genres: список жанров
        director_avg: словарь средних рейтингов режиссёров
        actor_avg: словарь средних рейтингов актёров
        tconst_to_people: словарь связей фильм-персоны
        nconst_mapping: словарь имён персон
    """
    logger.info("Начало сохранения модели и метаданных")

    model_dir = Path(f"{config.ABSPATH}/models")
    model_dir.mkdir(parents=True, exist_ok=True)

    # Сохраняем модель
    logger.info("Сохранение модели...")
    with open(model_dir / 'model.pkl', 'wb') as f:
        pickle.dump(model, f)
    logger.info(f"Модель сохранена в {model_dir / 'model.pkl'}")

    # Сохраняем scaler
    logger.info("Сохранение scaler...")
    with open(model_dir / 'scaler.pkl', 'wb') as f:
        pickle.dump(scaler, f)
    logger.info(f"Scaler сохранён в {model_dir / 'scaler.pkl'}")

    # Сохраняем метаданные
    logger.info("Сохранение метаданных...")
    metadata = {
        'genres': all_genres,
        'feature_names': all_genres + ['startYear', 'runtimeMinutes', 'numVotes', 'director_avg_rating'] +
                         [f'actor_{i+1}_avg_rating' for i in range(5)]
    }
    with open(model_dir / 'metadata.pkl', 'wb') as f:
        pickle.dump(metadata, f)
    logger.info(f"Метаданные сохранены в {model_dir / 'metadata.pkl'}")

    # Сохраняем вспомогательные словари
    logger.info("Сохранение вспомогательных словарей...")
    with open(model_dir / 'director_avg.pkl', 'wb') as f:
        pickle.dump(director_avg, f)
    logger.info(f"director_avg сохранён ({len(director_avg)} записей)")

    with open(model_dir / 'actor_avg.pkl', 'wb') as f:
        pickle.dump(actor_avg, f)
    logger.info(f"actor_avg сохранён ({len(actor_avg)} записей)")

    with open(model_dir / 'tconst_to_people.pkl', 'wb') as f:
        pickle.dump(tconst_to_people, f)
    logger.info(f"tconst_to_people сохранён ({len(tconst_to_people)} записей)")

    with open(model_dir / 'nconst_mapping.pkl', 'wb') as f:
        pickle.dump(nconst_mapping, f)
    logger.info(f"nconst_mapping сохранён ({len(nconst_mapping)} записей)")

    logger.info("=" * 60)
    logger.info("ВСЕ ДАННЫЕ УСПЕШНО СОХРАНЕНЫ")
    logger.info(f"Путь к моделям: {model_dir}")
    logger.info("=" * 60)


def load_trained_model():
    """
    Загружает обученную модель и все необходимые метаданные.

    Returns:
        model, scaler, metadata, director_avg, actor_avg, tconst_to_people, nconst_mapping
    """
    logger.info("Загрузка обученной модели и метаданных")

    model_dir = Path(f"{config.ABSPATH}/models")

    logger.info(f"Путь к моделям: {model_dir}")

    with open(model_dir / 'model.pkl', 'rb') as f:
        model = pickle.load(f)
    logger.info("Модель загружена")

    with open(model_dir / 'scaler.pkl', 'rb') as f:
        scaler = pickle.load(f)
    logger.info("Scaler загружен")

    with open(model_dir / 'metadata.pkl', 'rb') as f:
        metadata = pickle.load(f)
    logger.info(f"Метаданные загружены ({len(metadata['genres'])} жанров)")

    with open(model_dir / 'director_avg.pkl', 'rb') as f:
        director_avg = pickle.load(f)
    logger.info(f"director_avg загружен ({len(director_avg)} записей)")

    with open(model_dir / 'actor_avg.pkl', 'rb') as f:
        actor_avg = pickle.load(f)
    logger.info(f"actor_avg загружен ({len(actor_avg)} записей)")

    with open(model_dir / 'tconst_to_people.pkl', 'rb') as f:
        tconst_to_people = pickle.load(f)
    logger.info(f"tconst_to_people загружен ({len(tconst_to_people)} записей)")

    with open(model_dir / 'nconst_mapping.pkl', 'rb') as f:
        nconst_mapping = pickle.load(f)
    logger.info(f"nconst_mapping загружен ({len(nconst_mapping)} записей)")

    logger.info("=" * 60)
    logger.info("ВСЕ ДАННЫЕ УСПЕШНО ЗАГРУЖЕНЫ")
    logger.info("=" * 60)

    return model, scaler, metadata, director_avg, actor_avg, tconst_to_people, nconst_mapping

def interpret_model(model: SGDRegressor, scaler: StandardScaler, feature_names: list) -> None:
    """
    Выводит коэффициенты модели в исходных единицах.
    Для числовых признаков учтено ручное масштабирование.
    """
    logger.info("=" * 60)
    logger.info("ИНТЕРПРЕТАЦИЯ МОДЕЛИ")
    logger.info("=" * 60)
    
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