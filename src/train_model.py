from typing import Optional, Tuple, List
import gc
import numpy as np
import pickle
from pathlib import Path
import pandas as pd
from catboost import CatBoostRegressor, Pool

from src.data_filtr import get_batches, save_metadata
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
    CatBoost умеет работать с категориальными признаками без one-hot кодирования.
    
    Returns:
        model, metadata
    """
    logger.info("=" * 60)
    logger.info("НАЧАЛО ОБУЧЕНИЯ CATBOOST")
    logger.info(f"Параметры: batch_size={batch_size}, max_batches={max_batches}")
    logger.info(f"Количество жанров: {len(all_genres)}")
    logger.info("=" * 60)

    # Определяем признаки
    # Числовые: startYear, runtimeMinutes, numVotes_log, director_avg_rating, actor_*_avg_rating
    # Категориальные: genres (как строка), director_id, actor_ids (как строки)
    
    numeric_features = [
        'startYear',
        'runtimeMinutes',
        'numVotes_log',
        'director_avg_rating',
        'actor_1_avg_rating',
        'actor_2_avg_rating',
        'actor_3_avg_rating'
    ]

    categorical_features = [
        'genres_combined',
        'director_id',
        'actor_ids_combined'
    ]

    all_feature_names = (
        numeric_features
        + categorical_features
    )
    
    # Индексы категориальных признаков для CatBoost (0-based)
    cat_features_idx = [
        all_feature_names.index(x)
        for x in categorical_features
    ]
    
    logger.info(f"Числовые признаки ({len(numeric_features)}): {numeric_features}")
    logger.info(f"Категориальные признаки ({len(categorical_features)}): {categorical_features}")
    logger.info(f"Индексы категориальных признаков: {cat_features_idx}")

    # Инициализация модели CatBoost
    model = CatBoostRegressor(
        iterations=1500,
        depth=8,
        learning_rate=0.03,
        loss_function='RMSE',
        random_seed=42,
        verbose=100,
        text_processing={
            "tokenizers": [
                {
                    "tokenizer_id": "Space",
                    "separator_type": "ByDelimiter",
                    "delimiter": " "
                }
            ],
            "dictionaries": [
                {
                    "dictionary_id": "BiGram",
                    "dictionary_type": "BiGram",
                    "max_dictionary_size": "50000"
                }
            ]
        }
    )
    logger.info("Модель CatBoostRegressor инициализирована")

    # Собираем все данные для обучения (CatBoost требует все данные сразу для построения деревьев)
    # Для очень больших датасетов можно использовать catboost.Pool с параметром chunk_size
    all_X_list = []
    all_y_list = []
    
    total_rows = 0
    batches_processed = 0

    try:
        for X, y, titles, tconsts in get_batches(all_genres, batch_size, max_batches=max_batches):
            if len(X) == 0:
                logger.warning("Пустой батч, пропускаем")
                continue

            logger.info(f"\n--- Обработка батча {batches_processed + 1} ---")
            logger.info(f"Размер батча: {len(X)} записей")

            # Проверяем наличие всех необходимых колонок
            missing_cols = [col for col in all_feature_names if col not in X.columns]
            if missing_cols:
                logger.warning(f"Отсутствуют колонки: {missing_cols}. Пропускаем батч.")
                continue

            # Заполняем пропуски
            X_processed = X.copy()
            
            # Числовые признаки -> 0
            for col in numeric_features:
                if col in X_processed.columns:
                    X_processed[col] = X_processed[col].fillna(0)
            
            # Категориальные признаки -> 'Unknown'
            for col in categorical_features:
                if col in X_processed.columns:
                    X_processed[col] = X_processed[col].fillna('Unknown')
                else:
                    # Если колонки нет, создаем
                    X_processed[col] = 'Unknown'

            all_X_list.append(X_processed[all_feature_names])
            all_y_list.append(y.values)
            
            total_rows += len(y)
            batches_processed += 1

            logger.info(f"Обработано строк: {total_rows}")

            # Освобождаем память
            del X_processed
            gc.collect()

    except Exception as e:
        logger.error(f"Критическая ошибка при сборе данных: {e}")
        raise
    finally:
        logger.info("=" * 60)
        logger.info("СБОР ДАННЫХ ЗАВЕРШЕН")
        logger.info(f"Всего обработано строк: {total_rows}")
        logger.info(f"Всего обработано батчей: {batches_processed}")
        logger.info("=" * 60)

    if total_rows == 0:
        logger.error("Не удалось получить данные для обучения")
        return None, {}

    # Объединяем все батчи
    logger.info("Объединение батчей...")
    X_full = pd.concat(all_X_list, ignore_index=True)
    y_full = np.concatenate(all_y_list)
    
    logger.info(f"Итоговый размер выборки: {len(X_full)} записей")
    
    # Освобождаем память от списков
    del all_X_list, all_y_list
    gc.collect()

    # Обучение модели
    logger.info("Начало обучения CatBoost...")
    
    # Создаем Pool для CatBoost
    train_pool = Pool(
        data=X_full,
        label=y_full,
        cat_features=cat_features_idx,
        feature_names=all_feature_names
    )
    
    model.fit(train_pool)
    
    logger.info("Обучение завершено")
    
    # Вывод важности признаков
    importance = model.get_feature_importance()
    sorted_idx = np.argsort(importance)[::-1]
    
    logger.info("\n=== Топ-10 важных признаков ===")
    for i in sorted_idx[:10]:
        logger.info(f"{all_feature_names[i]}: {importance[i]:.4f}")
    
    # Метаданные
    metadata = {
        'feature_names': all_feature_names,
        'cat_features_idx': cat_features_idx,
        'numeric_features': numeric_features,
        'categorical_features': categorical_features,
        'total_rows_trained': total_rows,
        'batches_processed': batches_processed
    }
    
    return model, metadata


def save_trained_model(
    model: CatBoostRegressor,
    metadata: dict
) -> None:
    """
    Сохраняет обученную CatBoost модель и метаданные.
    """
    logger.info("Начало сохранения модели и метаданных")

    model_dir = Path(f"{config.ABSPATH}/models")
    model_dir.mkdir(parents=True, exist_ok=True)

    # Сохраняем модель в формате CatBoost
    logger.info("Сохранение модели...")
    model_path = model_dir / 'model.cbm'
    model.save_model(model_path)
    logger.info(f"Модель сохранена в {model_path}")

    # Сохраняем метаданные
    logger.info("Сохранение метаданных...")
    with open(model_dir / 'metadata.pkl', 'wb') as f:
        pickle.dump(metadata, f)
    logger.info(f"Метаданные сохранены в {model_dir / 'metadata.pkl'}")

    logger.info("=" * 60)
    logger.info("ВСЕ ДАННЫЕ УСПЕШНО СОХРАНЕНЫ")
    logger.info(f"Путь к моделям: {model_dir}")
    logger.info("=" * 60)


def load_trained_model():
    """
    Загружает обученную CatBoost модель и метаданные.
    """
    logger.info("Загрузка обученной модели и метаданных")

    model_dir = Path(f"{config.ABSPATH}/models")
    logger.info(f"Путь к моделям: {model_dir}")

    # Загрузка модели CatBoost
    model = CatBoostRegressor()
    model.load_model(model_dir / 'model.cbm')
    logger.info("Модель загружена")

    # Загрузка метаданных
    with open(model_dir / 'metadata.pkl', 'rb') as f:
        metadata = pickle.load(f)
    logger.info(f"Метаданные загружены ({len(metadata['feature_names'])} признаков)")

    logger.info("=" * 60)
    logger.info("ВСЕ ДАННЫЕ УСПЕШНО ЗАГРУЖЕНЫ")
    logger.info("=" * 60)

    return model, metadata


def interpret_model(model: CatBoostRegressor, metadata: dict) -> None:
    """
    Выводит важность признаков CatBoost модели.
    """
    logger.info("=" * 60)
    logger.info("ИНТЕРПРЕТАЦИЯ МОДЕЛИ CATBOOST")
    logger.info("=" * 60)
    
    importance = model.get_feature_importance()
    feature_names = metadata['feature_names']
    
    # Сортируем по важности
    features_sorted = sorted(
        zip(feature_names, importance),
        key=lambda x: x[1],
        reverse=True
    )
    
    logger.info("=== Все признаки по важности ===")
    for name, val in features_sorted:
        logger.info(f"{name}: {val:.4f}")
    
    logger.info("\n=== Топ-5 важных признаков ===")
    for name, val in features_sorted[:5]:
        logger.info(f"{name}: {val:.4f}")