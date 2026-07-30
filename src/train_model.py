from typing import Optional, Tuple
import numpy as np
import pickle
from pathlib import Path
import pandas as pd
from catboost import CatBoostRegressor, Pool
from sklearn.model_selection import train_test_split
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from src.data_filtr import get_batches
from src.logger import setup_logger
from settings import config

logger = setup_logger(__name__)


def train_catboost_model(
    all_genres: list,
    batch_size: int = 10000,
    max_batches: Optional[int] = None,
    test_size: float = 0.2,
    random_state: int = 42
) -> Tuple[CatBoostRegressor, dict]:
    """
    Обучает CatBoost модель на батчах данных.
    Разделяет данные на train/test и вычисляет метрики.
    """
    logger.info("=" * 60)
    logger.info("НАЧАЛО ОБУЧЕНИЯ CATBOOST")
    logger.info(f"Параметры: batch_size={batch_size}, max_batches={max_batches}, test_size={test_size}")
    logger.info("=" * 60)

    # Определяем признаки
    numeric_features = [
        'startYear', 'runtimeMinutes', 'numVotes_log',
        'director_avg_rating', 'actor_1_avg_rating',
        'actor_2_avg_rating', 'actor_3_avg_rating'
    ]
    categorical_features = [
        'genres_combined', 'director_id', 'actor_ids_combined'
    ]
    all_feature_names = numeric_features + categorical_features
    cat_features_idx = [all_feature_names.index(x) for x in categorical_features]

    logger.info(f"Числовые признаки: {numeric_features}")
    logger.info(f"Категориальные признаки: {categorical_features}")

    # Списки для сбора данных
    all_X = []
    all_y = []
    total_rows = 0
    batches_processed = 0

    try:
        for X, y, titles, tconsts in get_batches(all_genres, batch_size, max_batches=max_batches):
            if len(X) == 0:
                continue

            # Заполняем пропуски
            X_filled = X.copy()
            for col in numeric_features:
                if col in X_filled.columns:
                    X_filled[col] = X_filled[col].fillna(0)
            for col in categorical_features:
                if col in X_filled.columns:
                    X_filled[col] = X_filled[col].fillna('Unknown')
                else:
                    X_filled[col] = 'Unknown'

            X_ready = X_filled[all_feature_names]
            y_ready = y.values.astype(np.float32)

            all_X.append(X_ready)
            all_y.append(y_ready)

            total_rows += len(X_ready)
            batches_processed += 1
            logger.info(f"Батч {batches_processed}: {len(X_ready)} строк. Всего: {total_rows}")

            if batches_processed % 5 == 0:
                import gc
                gc.collect()

    except Exception as e:
        logger.error(f"Ошибка при сборе данных: {e}")
        raise

    logger.info("=" * 60)
    logger.info("СБОР ДАННЫХ ЗАВЕРШЁН")
    logger.info(f"Всего строк: {total_rows}, батчей: {batches_processed}")
    logger.info("=" * 60)

    if total_rows == 0:
        logger.error("Нет данных для обучения")
        return None, {}

    # Объединяем все батчи
    logger.info("Объединение батчей...")
    X_full = pd.concat(all_X, ignore_index=True)
    y_full = np.concatenate(all_y)

    del all_X, all_y
    import gc
    gc.collect()

    logger.info(f"Итоговый размер выборки: {len(X_full)} записей")

    # Разделение на train/test
    logger.info(f"Разделение данных: test_size={test_size}, random_state={random_state}")
    X_train, X_test, y_train, y_test = train_test_split(
        X_full, y_full, test_size=test_size, random_state=random_state
    )
    logger.info(f"Обучающая выборка: {len(X_train)} записей")
    logger.info(f"Тестовая выборка: {len(X_test)} записей")

    # Освобождаем X_full, y_full (они больше не нужны)
    del X_full, y_full
    gc.collect()

    # Инициализация модели CatBoost
    model = CatBoostRegressor(
        iterations=1500,
        depth=8,
        learning_rate=0.03,
        loss_function='RMSE',
        random_seed=42,
        verbose=100,
        text_processing={
            "tokenizers": [{"tokenizer_id": "Space", "separator_type": "ByDelimiter", "delimiter": " "}],
            "dictionaries": [{"dictionary_id": "BiGram", "dictionary_type": "BiGram", "max_dictionary_size": "50000"}]
        }
    )

    # Создаём Pool для обучения
    logger.info("Создание Pool для CatBoost (обучение)...")
    train_pool = Pool(
        data=X_train,
        label=y_train,
        cat_features=cat_features_idx,
        feature_names=all_feature_names
    )

    # Обучение
    logger.info("Начало обучения CatBoost...")
    model.fit(train_pool)
    logger.info("Обучение завершено")

    # Предсказание на тестовой выборке
    logger.info("Оценка на тестовой выборке...")
    y_pred = model.predict(X_test)

    # Метрики
    mae = mean_absolute_error(y_test, y_pred)
    rmse = np.sqrt(mean_squared_error(y_test, y_pred))
    r2 = r2_score(y_test, y_pred)

    logger.info("=" * 60)
    logger.info("МЕТРИКИ НА ТЕСТОВОЙ ВЫБОРКЕ:")
    logger.info(f"MAE  = {mae:.4f}")
    logger.info(f"RMSE = {rmse:.4f}")
    logger.info(f"R²   = {r2:.4f}")
    logger.info("=" * 60)

    # Важность признаков
    importance = model.get_feature_importance()
    sorted_idx = np.argsort(importance)[::-1]
    logger.info("\n=== Топ-10 важных признаков ===")
    for i in sorted_idx[:10]:
        logger.info(f"{all_feature_names[i]}: {importance[i]:.4f}")

    # Метаданные включают метрики
    metadata = {
        'feature_names': all_feature_names,
        'cat_features_idx': cat_features_idx,
        'numeric_features': numeric_features,
        'categorical_features': categorical_features,
        'test_size': test_size,
        'test_mae': mae,
        'test_rmse': rmse,
        'test_r2': r2,
        'total_rows': total_rows,
        'batches_processed': batches_processed,
    }
    return model, metadata


def save_trained_model(model: CatBoostRegressor, metadata: dict) -> None:
    """Сохраняет модель и метаданные."""
    model_dir = Path(f"{config.ABSPATH}/models")
    model_dir.mkdir(parents=True, exist_ok=True)
    model.save_model(model_dir / 'model.cbm')
    with open(model_dir / 'metadata.pkl', 'wb') as f:
        pickle.dump(metadata, f)
    logger.info(f"Модель и метаданные сохранены в {model_dir}")


def load_trained_model() -> Tuple[CatBoostRegressor, dict]:
    """Загружает модель и метаданные."""
    model_dir = Path(f"{config.ABSPATH}/models")
    model = CatBoostRegressor()
    model.load_model(model_dir / 'model.cbm')
    with open(model_dir / 'metadata.pkl', 'rb') as f:
        metadata = pickle.load(f)
    return model, metadata


def interpret_model(model: CatBoostRegressor, metadata: dict) -> None:
    """Выводит важность признаков."""
    importance = model.get_feature_importance()
    feature_names = metadata['feature_names']
    sorted_features = sorted(zip(feature_names, importance), key=lambda x: x[1], reverse=True)
    logger.info("=== Важность признаков ===")
    for name, val in sorted_features:
        logger.info(f"{name}: {val:.4f}")