from __future__ import annotations

import gc
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Generator, List, Optional, Tuple
from uuid import uuid4

import numpy as np
import pandas as pd
from catboost import CatBoostRegressor

from settings import config
import src.train_model as train_model_module
from src.logger import setup_logger
from src.train_model import (
    CATEGORICAL_FEATURES,
    _actual_years,
    _ensure_free_disk,
    _pool_from_file,
    _training_root,
    _write_column_description,
    _write_rows,
    evaluate_candidate_quality,
)


logger = setup_logger(__name__)

Batch = Tuple[pd.DataFrame, pd.Series, List[str], List[str]]
BatchProvider = Callable[..., Generator[Batch, None, None]]


def make_year_limited_batches(
    base_get_batches: BatchProvider,
    max_target_year: int,
) -> BatchProvider:
    """Ограничивает только target rows, не меняя temporal history semantics.

    Creative/history features уже рассчитаны с правилом ``history_year < target_year``.
    Поэтому наличие более новых фильмов в физической IMDb БД не даёт утечки в
    более старый target; здесь мы только исключаем provisional target years из
    validation/refit dataset.
    """
    limit = int(max_target_year)

    def get_batches(
        genres: list,
        batch_size: int = 5000,
        use_director_stats: bool = True,
        use_actor_stats: bool = True,
        use_writer_stats: bool = True,
        max_batches: Optional[int] = None,
    ) -> Generator[Batch, None, None]:
        for X, y, titles, tconsts in base_get_batches(
            genres,
            batch_size=batch_size,
            use_director_stats=use_director_stats,
            use_actor_stats=use_actor_stats,
            use_writer_stats=use_writer_stats,
            max_batches=max_batches,
        ):
            if X.empty:
                continue
            if "startYear" not in X.columns:
                raise RuntimeError("В обучающих данных отсутствует startYear")
            years = _actual_years(X["startYear"])
            positions = np.flatnonzero(years <= limit)
            if positions.size == 0:
                continue
            yield (
                X.iloc[positions].copy(),
                y.iloc[positions].copy(),
                [titles[int(i)] for i in positions],
                [tconsts[int(i)] for i in positions],
            )

    return get_batches


def evaluate_validation_candidate(metadata: dict) -> dict:
    """Запускает quality gate до дорогого final refit без загрузки active CatBoost."""
    active_metadata = train_model_module._read_active_metadata()
    gate = evaluate_candidate_quality(
        metadata,
        active_metadata,
        max_mae_regression=config.TRAIN_MAX_MAE_REGRESSION,
    )
    logger.info(
        "Pre-refit quality gate: passed=%s comparable=%s reason=%s candidate_mae=%s active_mae=%s",
        gate.get("passed"),
        gate.get("comparable"),
        gate.get("reason"),
        gate.get("candidate_mae"),
        gate.get("active_mae"),
    )
    if not gate.get("passed"):
        raise RuntimeError(
            "Temporal validation не прошла quality gate; final refit не запускается: "
            f"reason={gate.get('reason')}, candidate_mae={gate.get('candidate_mae')}, "
            f"active_mae={gate.get('active_mae')}"
        )
    return gate


def _new_regressor(iterations: int) -> CatBoostRegressor:
    return CatBoostRegressor(
        iterations=iterations,
        depth=8,
        learning_rate=0.03,
        loss_function="RMSE",
        random_seed=42,
        verbose=100,
        thread_count=1,
        used_ram_limit="900mb",
        model_size_reg=5.0,
        ctr_leaf_count_limit=50_000,
        max_ctr_complexity=1,
        allow_writing_files=False,
    )


def train_final_refit_model(
    all_genres: list,
    *,
    base_get_batches: BatchProvider,
    expected_feature_names: list[str],
    max_target_year: int,
    batch_size: int = 2500,
    max_batches: Optional[int] = None,
    iterations: int = 1500,
) -> tuple[CatBoostRegressor, dict]:
    """Переобучает production artifact на всех стабильных target rows.

    В отличие от temporal validation здесь нет test split и не рассчитываются
    quality metrics. Честные MAE/RMSE/R² и uncertainty остаются от отдельной
    holdout-модели, прошедшей quality gate перед refit.
    """
    if iterations < 1:
        raise ValueError("iterations должно быть положительным числом")
    stable_year = int(max_target_year)
    if stable_year < 1900:
        raise ValueError("max_target_year выглядит некорректно")
    if not expected_feature_names:
        raise ValueError("expected_feature_names не может быть пустым")

    root = _training_root() / (
        datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        + "-refit-"
        + uuid4().hex[:8]
    )
    root.mkdir(parents=False, exist_ok=False)
    _ensure_free_disk(root)
    data_path = root / "refit.tsv"
    cd_path = root / "columns.cd"
    _write_column_description(cd_path, expected_feature_names, CATEGORICAL_FEATURES)

    limited_get_batches = make_year_limited_batches(base_get_batches, stable_year)
    rows = 0
    min_year: int | None = None
    max_year: int | None = None
    batches = 0

    try:
        logger.info(
            "FINAL REFIT: собираем все стабильные target rows до %s включительно",
            stable_year,
        )
        for X, y, _titles, _tconsts in limited_get_batches(
            all_genres,
            batch_size=batch_size,
            max_batches=max_batches,
        ):
            if X.columns.tolist() != expected_feature_names:
                raise RuntimeError(
                    "Feature schema final refit отличается от temporal validation"
                )

            X_filled = X.copy()
            for col in expected_feature_names:
                if col in CATEGORICAL_FEATURES:
                    X_filled[col] = X_filled[col].fillna("Unknown").astype(str)
                else:
                    X_filled[col] = pd.to_numeric(
                        X_filled[col], errors="coerce"
                    ).fillna(0)

            years = _actual_years(X_filled["startYear"])
            if years.size:
                batch_min = int(years.min())
                batch_max = int(years.max())
                min_year = batch_min if min_year is None else min(min_year, batch_min)
                max_year = batch_max if max_year is None else max(max_year, batch_max)
            _ensure_free_disk(root)
            rows += _write_rows(
                data_path,
                X_filled,
                y,
                expected_feature_names,
            )
            batches += 1
            del X_filled, years

        if rows < 100:
            raise RuntimeError(
                f"Недостаточно стабильных строк для final refit: rows={rows}"
            )
        if max_year is None or max_year > stable_year:
            raise RuntimeError(
                "Final refit нарушил stable target cutoff: "
                f"max_year={max_year}, cutoff={stable_year}"
            )

        pool = _pool_from_file(data_path, cd_path)
        model = _new_regressor(iterations)
        logger.info(
            "FINAL REFIT: CatBoost fit rows=%s, years=%s-%s, iterations=%s",
            rows,
            min_year,
            max_year,
            iterations,
        )
        model.fit(pool)
        del pool
        gc.collect()

        metadata = {
            "refit_rows": rows,
            "refit_year_from": min_year,
            "refit_year_to": max_year,
            "refit_target_max_year": stable_year,
            "refit_batches_processed": batches,
            "refit_iterations": iterations,
            "refit_storage": "disk-first-dsv",
        }
        return model, metadata
    finally:
        shutil.rmtree(root, ignore_errors=True)
        logger.info("Временный FINAL REFIT dataset удалён")
