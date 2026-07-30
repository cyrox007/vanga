from typing import Optional, Tuple
import numpy as np
import pickle
from pathlib import Path
import pandas as pd
from catboost import CatBoostRegressor, Pool
from src.data_filtr import get_batches
from src.logger import setup_logger
from settings import config

logger = setup_logger(__name__)


def train_catboost_model(
    all_genres: list,
    batch_size: int = 10000,
    max_batches: Optional[int] = None
) -> Tuple[CatBoostRegressor, dict]:
    """
    Обучает CatBoost модель на батчах данных.
    Все батчи собираются в память (для 1 млн строк это ~200 МБ).
    """
    logger.info("=" * 60)
    logger.info("НАЧАЛО ОБУЧЕНИЯ CATBOOST")
    logger.info(f"Параметры: batch_size={batch_size}, max_batches={max_batches}")
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

            # Приводим к правильному порядку колонок
            X_ready = X_filled[all_feature_names]
            y_ready = y.values.astype(np.float32)

            all_X.append(X_ready)
            all_y.append(y_ready)

            total_rows += len(X_ready)
            batches_processed += 1
            logger.info(f"Батч {batches_processed}: {len(X_ready)} строк. Всего: {total_rows}")

            # Периодически освобождаем память (не каждый батч)
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

    # Очищаем списки
    del all_X, all_y
    import gc
    gc.collect()

    logger.info(f"Итоговый размер выборки: {len(X_full)} записей")

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

    # Создаём Pool
    logger.info("Создание Pool для CatBoost...")
    train_pool = Pool(
        data=X_full,
        label=y_full,
        cat_features=cat_features_idx,
        feature_names=all_feature_names
    )

    # Обучение
    logger.info("Начало обучения CatBoost...")
    model.fit(train_pool)
    logger.info("Обучение завершено")

    # Важность признаков
    importance = model.get_feature_importance()
    sorted_idx = np.argsort(importance)[::-1]
    logger.info("\n=== Топ-10 важных признаков ===")
    for i in sorted_idx[:10]:
        logger.info(f"{all_feature_names[i]}: {importance[i]:.4f}")

    metadata = {
        'feature_names': all_feature_names,
        'cat_features_idx': cat_features_idx,
        'numeric_features': numeric_features,
        'categorical_features': categorical_features,
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