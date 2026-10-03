from __future__ import annotations

import argparse
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path

from settings import config
import src.train_model as train_model_module
from src.creative_training import get_batches as creative_get_batches
from src.train_model import (
    interpret_model,
    save_trained_model,
    train_catboost_model,
)
from src.data_filtr import get_all_genres
from src.logger import setup_logger


logger = setup_logger(__name__)

# Production training использует расширенный P2 generator. Сам train_model остаётся
# общим disk-first engine, поэтому baseline v5/v6 и candidate v7 проходят один и
# тот же temporal split, CatBoost-конфигурацию и quality gate.
train_model_module.get_batches = creative_get_batches

# Schema v6 вводит явные *_known и *_prior_count для исторических person-сигналов.
# Schema v7 добавляет только pre-release Creative Team context: жанровую историю,
# recent form и director_is_writer. Публикация всё равно проходит temporal holdout
# и существующий quality gate.
BASELINE_SCHEMA_VERSION = 5
COVERAGE_SCHEMA_VERSION = 6
CREATIVE_TEAM_SCHEMA_VERSION = 7


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Обучение CatBoost-модели Vanga",
    )
    parser.add_argument(
        "--smoke",
        action="store_true",
        help=(
            "короткий полный проход по данным с 50 итерациями без публикации "
            "models/current.json"
        ),
    )
    parser.add_argument(
        "--evaluation-only",
        action="store_true",
        help=(
            "выполнить полноценное обучение и оценку, но не публиковать "
            "новое поколение в models/current.json"
        ),
    )
    parser.add_argument(
        "--without-coverage-features",
        action="store_true",
        help=(
            "отключить *_known и *_prior_count и воспроизвести baseline feature "
            "set schema v5 на том же актуальном IMDb dataset"
        ),
    )
    parser.add_argument(
        "--without-creative-team-features",
        action="store_true",
        help=(
            "отключить P2 Creative Team block и воспроизвести schema v6: "
            "coverage включён, genre/recent/director_is_writer отсутствуют"
        ),
    )
    parser.add_argument(
        "--iterations",
        type=int,
        default=None,
        help="число итераций CatBoost; по умолчанию 1500, в smoke-режиме 50",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=10000,
        help="размер батча подготовки признаков",
    )
    parser.add_argument(
        "--max-batches",
        type=int,
        default=None,
        help="ограничить число батчей только для отладки",
    )
    return parser


def _save_temporary_artifact(model, kind: str) -> tuple[Path, int]:
    """Сериализует непубликуемую модель и возвращает путь и размер."""
    root = Path(config.ABSPATH) / "temp" / kind
    root.mkdir(parents=True, exist_ok=True)
    path = root / (
        datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        + "-model.cbm"
    )
    model.save_model(str(path), format="cbm")
    return path, path.stat().st_size


def _metric_text(metadata: dict, name: str) -> str:
    value = metadata.get(name)
    try:
        return f"{float(value):.6f}"
    except (TypeError, ValueError):
        return "n/a"


def _log_evaluation_summary(metadata: dict, size_bytes: int) -> None:
    logger.info("=" * 60)
    logger.info("РЕЗУЛЬТАТ НЕПУБЛИКУЕМОЙ ОЦЕНКИ")
    logger.info("schema_version=%s", metadata.get("schema_version"))
    logger.info(
        "coverage_features_version=%s",
        metadata.get("coverage_features_version"),
    )
    logger.info(
        "creative_team_features_version=%s",
        metadata.get("creative_team_features_version"),
    )
    logger.info("MAE=%s", _metric_text(metadata, "test_mae"))
    logger.info("RMSE=%s", _metric_text(metadata, "test_rmse"))
    logger.info("R²=%s", _metric_text(metadata, "test_r2"))
    logger.info(
        "holdout=%s-%s; test_rows=%s",
        metadata.get("test_year_from"),
        metadata.get("test_year_to"),
        metadata.get("test_rows"),
    )
    logger.info("model_size_mb=%.2f", size_bytes / 1024 / 1024)
    logger.info("models/current.json НЕ ИЗМЕНЁН")
    logger.info("=" * 60)


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)

    iterations = args.iterations
    if iterations is None:
        iterations = 50 if args.smoke else 1500
    if iterations < 1:
        raise SystemExit("--iterations должно быть положительным числом")
    if args.batch_size < 1:
        raise SystemExit("--batch-size должно быть положительным числом")
    if args.max_batches is not None and args.max_batches < 1:
        raise SystemExit("--max-batches должно быть положительным числом")

    coverage_enabled = not args.without_coverage_features
    # Creative Team schema строится поверх coverage schema. Если пользователь
    # просит baseline v5, P2 автоматически отключается и не создаёт гибридную
    # схему, которую нельзя честно сопоставить с v5/v6.
    creative_enabled = coverage_enabled and not args.without_creative_team_features

    non_publishable_baseline = not coverage_enabled or not creative_enabled
    if non_publishable_baseline and not args.smoke and not args.evaluation_only:
        if not coverage_enabled:
            baseline_name = "Baseline schema v5"
        else:
            baseline_name = "Baseline schema v6 без Creative Team"
        raise SystemExit(
            f"{baseline_name} нельзя публиковать через этот entrypoint. "
            "Используйте --evaluation-only или --smoke."
        )

    os.environ["VANGA_TRAIN_COVERAGE_FEATURES"] = "1" if coverage_enabled else "0"
    os.environ["VANGA_TRAIN_CREATIVE_TEAM_FEATURES"] = (
        "1" if creative_enabled else "0"
    )

    if args.smoke:
        mode = "SMOKE (без публикации)"
    elif args.evaluation_only:
        mode = "FULL EVALUATION (без публикации)"
    else:
        mode = "FULL"

    if creative_enabled:
        schema_label = "candidate v7"
    elif coverage_enabled:
        schema_label = "baseline v6"
    else:
        schema_label = "baseline v5"

    logger.info("=" * 60)
    logger.info("ЗАПУСК ОБУЧЕНИЯ CATBOOST")
    logger.info(
        "Режим: %s; schema=%s; coverage=%s; creative_team=%s; "
        "iterations=%s; batch_size=%s; max_batches=%s",
        mode,
        schema_label,
        "on" if coverage_enabled else "off",
        "on" if creative_enabled else "off",
        iterations,
        args.batch_size,
        args.max_batches,
    )
    logger.info("=" * 60)

    genres = get_all_genres()
    logger.info(f"Найдено жанров: {len(genres)}")

    model, metadata = train_catboost_model(
        genres,
        batch_size=args.batch_size,
        max_batches=args.max_batches,
        iterations=iterations,
    )
    if creative_enabled:
        metadata["schema_version"] = CREATIVE_TEAM_SCHEMA_VERSION
    elif coverage_enabled:
        metadata["schema_version"] = COVERAGE_SCHEMA_VERSION
    else:
        metadata["schema_version"] = BASELINE_SCHEMA_VERSION
    metadata["coverage_features_version"] = 1 if coverage_enabled else 0
    metadata["creative_team_features_version"] = 1 if creative_enabled else 0

    interpret_model(model, metadata)

    if args.smoke or args.evaluation_only:
        artifact_path = None
        kind = "smoke" if args.smoke else "evaluation"
        try:
            artifact_path, size_bytes = _save_temporary_artifact(model, kind)
            logger.info(
                "%s: модель сериализована, размер=%.1f МБ, путь=%s",
                kind.upper(),
                size_bytes / 1024 / 1024,
                artifact_path,
            )
            _log_evaluation_summary(metadata, size_bytes)
        finally:
            if artifact_path is not None:
                shutil.rmtree(artifact_path.parent, ignore_errors=True)
        if args.smoke:
            logger.info("SMOKE-ПРОВЕРКА ЗАВЕРШЕНА УСПЕШНО")
        else:
            logger.info("НЕПУБЛИКУЕМАЯ ОЦЕНКА ЗАВЕРШЕНА УСПЕШНО")
        return

    # Defense in depth: ни v5, ни v6-baseline не должны попасть в публикацию,
    # даже если раннюю валидацию аргументов в будущем случайно изменят.
    if not coverage_enabled or not creative_enabled:
        raise SystemExit(
            "Неполную baseline-схему нельзя публиковать через этот entrypoint."
        )

    save_trained_model(model, metadata)
    logger.info("ОБУЧЕНИЕ ЗАВЕРШЕНО УСПЕШНО")


if __name__ == "__main__":
    main()
