from __future__ import annotations

import gc
import csv
import json
import os
import pickle
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Tuple
from uuid import uuid4

import numpy as np
import pandas as pd
from catboost import CatBoostRegressor, Pool
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score

from settings import config
from src.data_filtr import get_batches
from src.logger import setup_logger


logger = setup_logger(__name__)

CATEGORICAL_FEATURES = [
    "genres_combined",
    "director_id",
    "actor_1_id",
    "actor_2_id",
    "actor_3_id",
]


@dataclass(frozen=True)
class PreparedDataset:
    root: Path
    train_path: Path
    test_path: Path
    column_description_path: Path
    feature_names: list[str]
    categorical_features: list[str]
    numeric_features: list[str]
    cat_features_idx: list[int]
    min_year: int
    max_year: int
    test_from_year: int
    total_rows: int
    train_rows: int
    test_rows: int
    batches_processed: int

    def cleanup(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)


def _training_root() -> Path:
    root = Path(config.ABSPATH) / "temp" / "training"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _ensure_free_disk(path: Path) -> None:
    reserve_bytes = int(config.TRAIN_MIN_FREE_DISK_GB * 1024 ** 3)
    free_bytes = shutil.disk_usage(path).free
    if free_bytes < reserve_bytes:
        raise RuntimeError(
            "Недостаточно свободного места для disk-first обучения: "
            f"свободно={free_bytes / 1024 ** 3:.2f} ГБ, "
            f"требуемый резерв={config.TRAIN_MIN_FREE_DISK_GB:.2f} ГБ"
        )


def _actual_years(series: pd.Series) -> np.ndarray:
    return np.rint(
        series.to_numpy(dtype=np.float64) * 100.0 + 1900.0
    ).astype(np.int32)


def _write_rows(
    path: Path,
    X: pd.DataFrame,
    y: pd.Series,
    feature_names: list[str],
) -> int:
    if X.empty:
        return 0

    frame = X[feature_names].copy()
    frame.insert(0, "__label__", y.to_numpy(dtype=np.float32, copy=False))
    frame.to_csv(
        path,
        mode="a",
        sep="\t",
        header=False,
        index=False,
        na_rep="",
        quoting=csv.QUOTE_MINIMAL,
        lineterminator="\n",
    )
    return len(frame)


def _write_column_description(
    path: Path,
    feature_names: list[str],
    categorical_features: list[str],
) -> None:
    categorical = set(categorical_features)
    lines = ["0\tLabel"]
    for position, name in enumerate(feature_names, start=1):
        role = "Categ" if name in categorical else "Num"
        lines.append(f"{position}\t{role}\t{name}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def prepare_training_dataset(
    all_genres: list,
    *,
    batch_size: int = 2500,
    max_batches: Optional[int] = None,
) -> PreparedDataset:
    """
    Готовит train/test как TSV на диске, не накапливая общий DataFrame в RAM.

    В каждый момент времени Python держит только один батч признаков. Последние
    два календарных года исходной выборки отправляются в test, более ранние —
    в train.
    """
    root = _training_root() / (
        datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        + "-"
        + uuid4().hex[:8]
    )
    root.mkdir(parents=False, exist_ok=False)
    _ensure_free_disk(root)

    train_path = root / "train.tsv"
    test_path = root / "test.tsv"
    cd_path = root / "columns.cd"

    feature_names: list[str] | None = None
    numeric_features: list[str] | None = None
    min_year: int | None = None
    max_year: int | None = None
    batches_processed = 0
    total_rows = 0

    # Первый проход только определяет диапазон лет и схему. Данные не копятся.
    logger.info("Проход 1/2: определяем диапазон лет и схему признаков")
    for X, _y, _titles, _tconsts in get_batches(
        all_genres,
        batch_size,
        max_batches=max_batches,
    ):
        if X.empty:
            continue

        current_features = X.columns.tolist()
        if feature_names is None:
            feature_names = current_features
            missing = [
                name for name in CATEGORICAL_FEATURES if name not in feature_names
            ]
            if missing:
                raise RuntimeError(
                    "Не найдены категориальные признаки: " + ", ".join(missing)
                )
            if "startYear" not in feature_names:
                raise RuntimeError("В обучающих данных отсутствует startYear")
            numeric_features = [
                name for name in feature_names if name not in CATEGORICAL_FEATURES
            ]
        elif current_features != feature_names:
            raise RuntimeError("Схема признаков изменилась между батчами")

        years = _actual_years(X["startYear"])
        batch_min = int(years.min())
        batch_max = int(years.max())
        min_year = batch_min if min_year is None else min(min_year, batch_min)
        max_year = batch_max if max_year is None else max(max_year, batch_max)
        total_rows += len(X)
        batches_processed += 1

    if not feature_names or min_year is None or max_year is None:
        shutil.rmtree(root, ignore_errors=True)
        raise RuntimeError("Нет данных для обучения")

    test_from_year = max_year - 1
    cat_features_idx = [
        feature_names.index(name) for name in CATEGORICAL_FEATURES
    ]
    _write_column_description(cd_path, feature_names, CATEGORICAL_FEATURES)

    logger.info(
        "Диапазон данных: %s-%s; test начинается с %s",
        min_year,
        max_year,
        test_from_year,
    )

    # Второй проход сразу пишет строки в соответствующий disk-backed dataset.
    logger.info("Проход 2/2: записываем train/test на диск")
    train_rows = 0
    test_rows = 0

    for X, y, _titles, _tconsts in get_batches(
        all_genres,
        batch_size,
        max_batches=max_batches,
    ):
        if X.empty:
            continue
        if X.columns.tolist() != feature_names:
            raise RuntimeError("Схема признаков изменилась между проходами")

        X_filled = X.copy()
        for col in feature_names:
            if col in CATEGORICAL_FEATURES:
                X_filled[col] = X_filled[col].fillna("Unknown").astype(str)
            else:
                X_filled[col] = pd.to_numeric(
                    X_filled[col], errors="coerce"
                ).fillna(0)

        years = _actual_years(X_filled["startYear"])
        train_mask = years < test_from_year
        test_mask = ~train_mask

        _ensure_free_disk(root)

        if train_mask.any():
            train_rows += _write_rows(
                train_path,
                X_filled.loc[train_mask],
                y.loc[train_mask],
                feature_names,
            )
        if test_mask.any():
            test_rows += _write_rows(
                test_path,
                X_filled.loc[test_mask],
                y.loc[test_mask],
                feature_names,
            )

        del X_filled, years, train_mask, test_mask

    if train_rows < 100 or test_rows < 20:
        shutil.rmtree(root, ignore_errors=True)
        raise RuntimeError(
            "Недостаточно данных для временного разделения: "
            f"train={train_rows}, test={test_rows}, "
            f"диапазон={min_year}-{max_year}"
        )

    logger.info(
        "Disk-first dataset готов: train=%s строк, test=%s строк, каталог=%s",
        train_rows,
        test_rows,
        root,
    )

    return PreparedDataset(
        root=root,
        train_path=train_path,
        test_path=test_path,
        column_description_path=cd_path,
        feature_names=feature_names,
        categorical_features=list(CATEGORICAL_FEATURES),
        numeric_features=numeric_features or [],
        cat_features_idx=cat_features_idx,
        min_year=min_year,
        max_year=max_year,
        test_from_year=test_from_year,
        total_rows=total_rows,
        train_rows=train_rows,
        test_rows=test_rows,
        batches_processed=batches_processed,
    )


def _pool_from_file(dataset_path: Path, cd_path: Path) -> Pool:
    return Pool(
        data=f"dsv://{dataset_path}",
        column_description=str(cd_path),
        delimiter="\t",
        has_header=False,
        thread_count=1,
    )


def train_catboost_model(
    all_genres: list,
    batch_size: int = 2500,
    max_batches: Optional[int] = None,
) -> Tuple[CatBoostRegressor, dict]:
    """
    Обучает CatBoost из файлового Pool.

    Python не объединяет все батчи в один DataFrame. Системный MemoryMax
    дополнительно ограничивает retrain-процесс; при превышении лимита рабочий
    inference остаётся отдельным сервисом и продолжает использовать старую
    опубликованную модель.
    """
    logger.info("=" * 60)
    logger.info("НАЧАЛО DISK-FIRST ОБУЧЕНИЯ CATBOOST")
    logger.info(
        "Параметры: batch_size=%s, max_batches=%s",
        batch_size,
        max_batches,
    )
    logger.info("=" * 60)

    prepared = prepare_training_dataset(
        all_genres,
        batch_size=batch_size,
        max_batches=max_batches,
    )

    try:
        logger.info("Создание тренировочного файлового Pool без общего pandas DataFrame")
        train_pool = _pool_from_file(
            prepared.train_path,
            prepared.column_description_path,
        )

        model = CatBoostRegressor(
            iterations=1500,
            depth=8,
            learning_rate=0.03,
            loss_function="RMSE",
            random_seed=42,
            verbose=100,
            thread_count=1,
            used_ram_limit="900mb",
            # Сдерживаем рост CTR-таблиц для высококардинальных персональных ID.
            # Это критично для VPS с 2 ГБ RAM: модель должна оставаться пригодной
            # для последующей загрузки inference-процессом.
            model_size_reg=5.0,
            ctr_leaf_count_limit=50_000,
            max_ctr_complexity=1,
            allow_writing_files=False,
        )

        logger.info("Начало обучения CatBoost")
        model.fit(train_pool)
        logger.info("Обучение завершено")

        # Большой train Pool больше не нужен. На малом сервере удержание его
        # одновременно с test Pool вызывало активный swap и многоминутный I/O stall.
        del train_pool
        gc.collect()
        logger.info("Тренировочный Pool освобождён перед оценкой")

        logger.info("Создание тестового файлового Pool")
        test_pool = _pool_from_file(
            prepared.test_path,
            prepared.column_description_path,
        )

        logger.info("Оценка на временной тестовой выборке")
        y_test = np.asarray(test_pool.get_label(), dtype=np.float32)
        y_pred = np.asarray(model.predict(test_pool), dtype=np.float32)

        mae = float(mean_absolute_error(y_test, y_pred))
        rmse = float(np.sqrt(mean_squared_error(y_test, y_pred)))
        r2 = float(r2_score(y_test, y_pred))

        logger.info("=" * 60)
        logger.info("МЕТРИКИ НА ВРЕМЕННОЙ ТЕСТОВОЙ ВЫБОРКЕ:")
        logger.info(f"MAE  = {mae:.4f}")
        logger.info(f"RMSE = {rmse:.4f}")
        logger.info(f"R²   = {r2:.4f}")
        logger.info("=" * 60)

        # После расчёта метрик test Pool и массивы прогнозов больше не нужны.
        # Освобождаем их до интерпретации и публикации модели.
        del test_pool, y_test, y_pred
        gc.collect()
        logger.info("Тестовый Pool освобождён после оценки")

        importance = model.get_feature_importance()
        sorted_idx = np.argsort(importance)[::-1]
        logger.info("=== Топ-10 важных признаков ===")
        for i in sorted_idx[:10]:
            logger.info(
                f"{prepared.feature_names[i]}: {importance[i]:.4f}"
            )

        metadata = {
            "schema_version": 3,
            "training_storage": "disk-first-dsv",
            "feature_names": prepared.feature_names,
            "cat_features_idx": prepared.cat_features_idx,
            "numeric_features": prepared.numeric_features,
            "categorical_features": prepared.categorical_features,
            "test_mae": mae,
            "test_rmse": rmse,
            "test_r2": r2,
            "train_year_from": prepared.min_year,
            "train_year_to": prepared.test_from_year - 1,
            "test_year_from": prepared.test_from_year,
            "test_year_to": prepared.max_year,
            "total_rows": prepared.total_rows,
            "train_rows": prepared.train_rows,
            "test_rows": prepared.test_rows,
            "batches_processed": prepared.batches_processed,
        }
        return model, metadata
    finally:
        prepared.cleanup()
        logger.info("Временный disk-first dataset удалён")


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
        model_size_bytes = model_path.stat().st_size
        max_model_size_bytes = int(config.TRAIN_MAX_MODEL_SIZE_MB * 1024 * 1024)
        if model_size_bytes <= 0:
            raise RuntimeError("CatBoost сохранил пустой файл модели")
        if model_size_bytes > max_model_size_bytes:
            raise RuntimeError(
                "Модель получилась слишком большой для безопасного inference: "
                f"{model_size_bytes / 1024 / 1024:.1f} МБ при лимите "
                f"{config.TRAIN_MAX_MODEL_SIZE_MB} МБ"
            )

        persisted_metadata = dict(metadata)
        persisted_metadata["model_size_bytes"] = model_size_bytes
        with metadata_path.open("wb") as handle:
            pickle.dump(persisted_metadata, handle)

        # Не загружаем model.cbm второй раз в процессе обучения: на малом VPS
        # это временно удваивает память CatBoost и приводит к swap-thrashing.
        # Успешный save_model + проверка размера + повторное чтение metadata
        # дают дешёвую проверку артефактов перед атомарным переключением.
        with metadata_path.open("rb") as handle:
            check_metadata = pickle.load(handle)
        if (
            not isinstance(check_metadata, dict)
            or not check_metadata.get("feature_names")
            or check_metadata.get("model_size_bytes") != model_size_bytes
        ):
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
    feature_names = metadata["feature_names"]
    sorted_features = sorted(
        zip(feature_names, importance),
        key=lambda item: item[1],
        reverse=True,
    )
    logger.info("=== Важность признаков ===")
    for name, val in sorted_features:
        logger.info(f"{name}: {val:.4f}")
