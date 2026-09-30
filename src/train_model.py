from typing import Optional, Tuple
import numpy as np
import json
import os
import pickle
import shutil
from datetime import datetime, timezone
from uuid import uuid4
from pathlib import Path
import pandas as pd
from catboost import CatBoostRegressor, Pool
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from src.data_filtr import get_batches
from src.logger import setup_logger
from settings import config

logger = setup_logger(__name__)


def train_catboost_model(
    all_genres: list,
    batch_size: int = 5000,
    max_batches: Optional[int] = None,
) -> Tuple[CatBoostRegressor, dict]:
    """
    Обучает CatBoost на признаках, доступных до релиза фильма.

    Валидация временная: два последних года датасета используются как test,
    все более ранние фильмы — как train. Это не даёт будущим релизам
    случайно попадать в обучающую часть.
    """
    logger.info("=" * 60)
    logger.info("НАЧАЛО ОБУЧЕНИЯ CATBOOST")
    logger.info(
        f"Параметры: batch_size={batch_size}, max_batches={max_batches}"
    )
    logger.info("=" * 60)

    all_X: list[pd.DataFrame] = []
    all_y: list[np.ndarray] = []
    total_rows = 0
    batches_processed = 0

    try:
        for X, y, _titles, _tconsts in get_batches(
            all_genres,
            batch_size,
            max_batches=max_batches,
        ):
            if X.empty:
                continue

            X_filled = X.copy()
            for col in X_filled.columns:
                if pd.api.types.is_numeric_dtype(X_filled[col]):
                    X_filled[col] = X_filled[col].fillna(0)
                else:
                    X_filled[col] = X_filled[col].fillna("Unknown")

            all_X.append(X_filled)
            all_y.append(y.to_numpy(dtype=np.float32, copy=True))
            total_rows += len(X_filled)
            batches_processed += 1

            logger.info(
                f"Батч {batches_processed}: {len(X_filled)} строк. "
                f"Всего: {total_rows}"
            )

            if batches_processed % 5 == 0:
                import gc
                gc.collect()

    except Exception as exc:
        logger.error(f"Ошибка при сборе данных: {exc}")
        raise

    if total_rows == 0:
        raise RuntimeError("Нет данных для обучения")

    logger.info("Объединение подготовленных батчей...")
    X_full = pd.concat(all_X, ignore_index=True)
    y_full = np.concatenate(all_y)

    categorical_features = [
        "genres_combined",
        "director_id",
        "actor_1_id",
        "actor_2_id",
        "actor_3_id",
    ]
    missing_categorical = [
        name for name in categorical_features if name not in X_full.columns
    ]
    if missing_categorical:
        raise RuntimeError(
            "Не найдены категориальные признаки: "
            + ", ".join(missing_categorical)
        )

    numeric_features = [
        col for col in X_full.columns if col not in categorical_features
    ]
    all_feature_names = numeric_features + categorical_features
    X_full = X_full[all_feature_names]
    cat_features_idx = [
        all_feature_names.index(col) for col in categorical_features
    ]

    del all_X, all_y
    import gc
    gc.collect()

    if "startYear" not in X_full.columns:
        raise RuntimeError("В обучающих данных отсутствует startYear")

    actual_years = np.rint(
        X_full["startYear"].to_numpy(dtype=np.float64) * 100.0 + 1900.0
    ).astype(np.int32)
    min_year = int(actual_years.min())
    max_year = int(actual_years.max())
    test_from_year = max_year - 1

    train_mask = actual_years < test_from_year
    test_mask = actual_years >= test_from_year

    train_count = int(train_mask.sum())
    test_count = int(test_mask.sum())
    if train_count < 100 or test_count < 20:
        raise RuntimeError(
            "Недостаточно данных для временного разделения: "
            f"train={train_count}, test={test_count}, "
            f"диапазон={min_year}-{max_year}"
        )

    X_train = X_full.loc[train_mask].reset_index(drop=True)
    X_test = X_full.loc[test_mask].reset_index(drop=True)
    y_train = y_full[train_mask]
    y_test = y_full[test_mask]

    logger.info(
        "Временное разделение: train=%s-%s (%s строк), test=%s-%s (%s строк)",
        min_year,
        test_from_year - 1,
        len(X_train),
        test_from_year,
        max_year,
        len(X_test),
    )

    del X_full, y_full, train_mask, test_mask, actual_years
    gc.collect()

    model = CatBoostRegressor(
        iterations=1500,
        depth=8,
        learning_rate=0.03,
        loss_function="RMSE",
        random_seed=42,
        verbose=100,
        thread_count=2,
        allow_writing_files=False,
    )

    logger.info("Создание Pool для CatBoost...")
    train_pool = Pool(
        data=X_train,
        label=y_train,
        cat_features=cat_features_idx,
        feature_names=all_feature_names,
    )

    logger.info("Начало обучения CatBoost...")
    model.fit(train_pool)
    logger.info("Обучение завершено")

    logger.info("Оценка на временной тестовой выборке...")
    y_pred = model.predict(X_test)

    mae = float(mean_absolute_error(y_test, y_pred))
    rmse = float(np.sqrt(mean_squared_error(y_test, y_pred)))
    r2 = float(r2_score(y_test, y_pred))

    logger.info("=" * 60)
    logger.info("МЕТРИКИ НА ВРЕМЕННОЙ ТЕСТОВОЙ ВЫБОРКЕ:")
    logger.info(f"MAE  = {mae:.4f}")
    logger.info(f"RMSE = {rmse:.4f}")
    logger.info(f"R²   = {r2:.4f}")
    logger.info("=" * 60)

    importance = model.get_feature_importance()
    sorted_idx = np.argsort(importance)[::-1]
    logger.info("=== Топ-10 важных признаков ===")
    for i in sorted_idx[:10]:
        logger.info(f"{all_feature_names[i]}: {importance[i]:.4f}")

    metadata = {
        "schema_version": 2,
        "feature_names": all_feature_names,
        "cat_features_idx": cat_features_idx,
        "numeric_features": numeric_features,
        "categorical_features": categorical_features,
        "test_mae": mae,
        "test_rmse": rmse,
        "test_r2": r2,
        "train_year_from": min_year,
        "train_year_to": test_from_year - 1,
        "test_year_from": test_from_year,
        "test_year_to": max_year,
        "total_rows": total_rows,
        "train_rows": len(X_train),
        "test_rows": len(X_test),
        "batches_processed": batches_processed,
    }
    return model, metadata


def _model_root() -> Path:
    root = Path(config.ABSPATH) / "models"
    root.mkdir(parents=True, exist_ok=True)
    return root


def resolve_current_model_path() -> Path:
    """Возвращает путь к активной модели.

    Новые обучения публикуются как неизменяемые каталоги releases/<generation>.
    Переключение происходит атомарной заменой файла current.json. Для старых
    установок оставлен fallback на models/model.cbm.
    """
    root = _model_root()
    pointer = root / "current.json"
    if pointer.exists():
        try:
            payload = json.loads(pointer.read_text(encoding="utf-8"))
            generation = str(payload["generation"]).strip()
            candidate = root / "releases" / generation / "model.cbm"
            metadata = candidate.parent / "metadata.pkl"
            if candidate.is_file() and metadata.is_file():
                return candidate
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
            logger.warning(f"Не удалось прочитать models/current.json: {exc}")

    legacy = root / "model.cbm"
    if legacy.is_file() and (root / "metadata.pkl").is_file():
        return legacy
    raise FileNotFoundError("Активная модель Vanga не найдена")


def save_trained_model(model: CatBoostRegressor, metadata: dict) -> None:
    """Публикует новую модель атомарно, не затрагивая рабочую версию при сбое."""
    root = _model_root()
    releases_dir = root / "releases"
    releases_dir.mkdir(parents=True, exist_ok=True)

    generation = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:8]
    release_dir = releases_dir / generation
    release_dir.mkdir(parents=False, exist_ok=False)

    model_path = release_dir / "model.cbm"
    metadata_path = release_dir / "metadata.pkl"
    try:
        model.save_model(str(model_path), format="cbm")
        with metadata_path.open("wb") as handle:
            pickle.dump(metadata, handle)

        # Проверяем, что обе части bundle реально читаются до публикации.
        check_model = CatBoostRegressor()
        check_model.load_model(str(model_path))
        with metadata_path.open("rb") as handle:
            check_metadata = pickle.load(handle)
        if not isinstance(check_metadata, dict) or not check_metadata.get("feature_names"):
            raise RuntimeError("Метаданные обученной модели повреждены")

        pointer_tmp = root / ".current.json.tmp"
        pointer_tmp.write_text(
            json.dumps(
                {
                    "generation": generation,
                    "published_at": datetime.now(timezone.utc).isoformat(),
                },
                ensure_ascii=False,
                indent=2,
            ) + "\n",
            encoding="utf-8",
        )
        os.replace(pointer_tmp, root / "current.json")
        logger.info(f"Опубликована новая модель: generation={generation}")

        # Храним три последних поколения, чтобы был простой ручной rollback.
        generations = sorted(
            [item for item in releases_dir.iterdir() if item.is_dir()],
            key=lambda item: item.stat().st_mtime,
            reverse=True,
        )
        for stale in generations[3:]:
            try:
                shutil.rmtree(stale)
            except OSError as exc:
                logger.warning(f"Не удалось удалить старую модель {stale}: {exc}")
    except Exception:
        # До замены current.json рабочая модель остаётся прежней.
        try:
            shutil.rmtree(release_dir)
        except OSError:
            pass
        raise


def load_trained_model() -> Tuple[CatBoostRegressor, dict]:
    """Загружает текущую опубликованную модель и её метаданные."""
    model_path = resolve_current_model_path()
    model = CatBoostRegressor()
    model.load_model(str(model_path))
    with (model_path.parent / "metadata.pkl").open("rb") as handle:
        metadata = pickle.load(handle)
    return model, metadata


def interpret_model(model: CatBoostRegressor, metadata: dict) -> None:
    """Выводит важность признаков."""
    importance = model.get_feature_importance()
    feature_names = metadata['feature_names']
    sorted_features = sorted(zip(feature_names, importance), key=lambda x: x[1], reverse=True)
    logger.info("=== Важность признаков ===")
    for name, val in sorted_features:
        logger.info(f"{name}: {val:.4f}")