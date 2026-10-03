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
train_model_module.get_batches = creative_get_batches

BASELINE_SCHEMA_VERSION = 5
COVERAGE_SCHEMA_VERSION = 6
CREATIVE_TEAM_SCHEMA_VERSION = 7
DIRECTOR_WRITER_PAIR_SCHEMA_VERSION = 8
DIRECTOR_ACTOR_PAIR_SCHEMA_VERSION = 9
CREATIVE_TREND_SCHEMA_VERSION = 10
DIRECTOR_TEAM_SCHEMA_VERSION = 11
FULL_CAST_SCHEMA_VERSION = 12
CAST_PAIR_SCHEMA_VERSION = 13
DUAL_ROLE_SCHEMA_VERSION = 14
TEAM_COLLABORATION_SCHEMA_VERSION = 15


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Обучение CatBoost-модели Vanga")
    parser.add_argument("--smoke", action="store_true")
    parser.add_argument("--evaluation-only", action="store_true")
    parser.add_argument("--without-coverage-features", action="store_true")
    parser.add_argument("--without-creative-team-features", action="store_true")
    parser.add_argument("--without-director-writer-pair-features", action="store_true")
    parser.add_argument("--without-director-actor-pair-features", action="store_true")
    parser.add_argument("--without-creative-trend-features", action="store_true")
    parser.add_argument("--without-director-team-features", action="store_true")
    parser.add_argument("--without-full-cast-features", action="store_true")
    parser.add_argument("--without-cast-pair-features", action="store_true")
    parser.add_argument("--without-dual-role-features", action="store_true")
    parser.add_argument(
        "--without-team-collaboration-features",
        action="store_true",
        help="отключить team-wide director↔writer/director↔actor и воспроизвести schema v14",
    )
    parser.add_argument("--iterations", type=int, default=None)
    parser.add_argument("--batch-size", type=int, default=10000)
    parser.add_argument("--max-batches", type=int, default=None)
    return parser


def _save_temporary_artifact(model, kind: str) -> tuple[Path, int]:
    root = Path(config.ABSPATH) / "temp" / kind
    root.mkdir(parents=True, exist_ok=True)
    path = root / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-model.cbm")
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
    for name in (
        "coverage_features_version",
        "creative_team_features_version",
        "director_writer_pair_features_version",
        "director_actor_pair_features_version",
        "creative_trend_features_version",
        "director_team_features_version",
        "full_cast_features_version",
        "cast_pair_features_version",
        "dual_role_features_version",
        "team_collaboration_features_version",
    ):
        logger.info("%s=%s", name, metadata.get(name))
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
    iterations = args.iterations if args.iterations is not None else (50 if args.smoke else 1500)
    if iterations < 1:
        raise SystemExit("--iterations должно быть положительным числом")
    if args.batch_size < 1:
        raise SystemExit("--batch-size должно быть положительным числом")
    if args.max_batches is not None and args.max_batches < 1:
        raise SystemExit("--max-batches должно быть положительным числом")

    coverage_enabled = not args.without_coverage_features
    creative_enabled = coverage_enabled and not args.without_creative_team_features
    writer_pair_enabled = creative_enabled and not args.without_director_writer_pair_features
    actor_pair_enabled = writer_pair_enabled and not args.without_director_actor_pair_features
    trend_enabled = actor_pair_enabled and not args.without_creative_trend_features
    director_team_enabled = trend_enabled and not args.without_director_team_features
    full_cast_enabled = director_team_enabled and not args.without_full_cast_features
    cast_pair_enabled = full_cast_enabled and not args.without_cast_pair_features
    dual_role_enabled = cast_pair_enabled and not args.without_dual_role_features
    team_collaboration_enabled = dual_role_enabled and not args.without_team_collaboration_features

    if not team_collaboration_enabled and not args.smoke and not args.evaluation_only:
        if not coverage_enabled:
            baseline_name = "Baseline schema v5"
        elif not creative_enabled:
            baseline_name = "Baseline schema v6"
        elif not writer_pair_enabled:
            baseline_name = "Baseline schema v7"
        elif not actor_pair_enabled:
            baseline_name = "Baseline schema v8"
        elif not trend_enabled:
            baseline_name = "Baseline schema v9"
        elif not director_team_enabled:
            baseline_name = "Baseline schema v10"
        elif not full_cast_enabled:
            baseline_name = "Baseline schema v11"
        elif not cast_pair_enabled:
            baseline_name = "Baseline schema v12"
        elif not dual_role_enabled:
            baseline_name = "Baseline schema v13"
        else:
            baseline_name = "Baseline schema v14 без team-wide collaboration"
        raise SystemExit(
            f"{baseline_name} нельзя публиковать через этот entrypoint. "
            "Используйте --evaluation-only или --smoke."
        )

    env = {
        "VANGA_TRAIN_COVERAGE_FEATURES": coverage_enabled,
        "VANGA_TRAIN_CREATIVE_TEAM_FEATURES": creative_enabled,
        "VANGA_TRAIN_DIRECTOR_WRITER_PAIR_FEATURES": writer_pair_enabled,
        "VANGA_TRAIN_DIRECTOR_ACTOR_PAIR_FEATURES": actor_pair_enabled,
        "VANGA_TRAIN_CREATIVE_TREND_FEATURES": trend_enabled,
        "VANGA_TRAIN_DIRECTOR_TEAM_FEATURES": director_team_enabled,
        "VANGA_TRAIN_FULL_CAST_FEATURES": full_cast_enabled,
        "VANGA_TRAIN_CAST_PAIR_FEATURES": cast_pair_enabled,
        "VANGA_TRAIN_DUAL_ROLE_FEATURES": dual_role_enabled,
        "VANGA_TRAIN_TEAM_COLLABORATION_FEATURES": team_collaboration_enabled,
    }
    for name, enabled in env.items():
        os.environ[name] = "1" if enabled else "0"

    mode = "SMOKE (без публикации)" if args.smoke else (
        "FULL EVALUATION (без публикации)" if args.evaluation_only else "FULL"
    )
    if team_collaboration_enabled:
        schema_label = "candidate v15"
    elif dual_role_enabled:
        schema_label = "baseline v14"
    elif cast_pair_enabled:
        schema_label = "baseline v13"
    elif full_cast_enabled:
        schema_label = "baseline v12"
    elif director_team_enabled:
        schema_label = "baseline v11"
    elif trend_enabled:
        schema_label = "baseline v10"
    elif actor_pair_enabled:
        schema_label = "baseline v9"
    elif writer_pair_enabled:
        schema_label = "baseline v8"
    elif creative_enabled:
        schema_label = "baseline v7"
    elif coverage_enabled:
        schema_label = "baseline v6"
    else:
        schema_label = "baseline v5"

    logger.info("=" * 60)
    logger.info("ЗАПУСК ОБУЧЕНИЯ CATBOOST")
    logger.info(
        "Режим: %s; schema=%s; full_cast=%s; cast_pair=%s; dual_role=%s; team_collaboration=%s; iterations=%s; batch_size=%s; max_batches=%s",
        mode,
        schema_label,
        "on" if full_cast_enabled else "off",
        "on" if cast_pair_enabled else "off",
        "on" if dual_role_enabled else "off",
        "on" if team_collaboration_enabled else "off",
        iterations,
        args.batch_size,
        args.max_batches,
    )
    logger.info("=" * 60)

    genres = get_all_genres()
    model, metadata = train_catboost_model(
        genres,
        batch_size=args.batch_size,
        max_batches=args.max_batches,
        iterations=iterations,
    )

    if team_collaboration_enabled:
        schema_version = TEAM_COLLABORATION_SCHEMA_VERSION
    elif dual_role_enabled:
        schema_version = DUAL_ROLE_SCHEMA_VERSION
    elif cast_pair_enabled:
        schema_version = CAST_PAIR_SCHEMA_VERSION
    elif full_cast_enabled:
        schema_version = FULL_CAST_SCHEMA_VERSION
    elif director_team_enabled:
        schema_version = DIRECTOR_TEAM_SCHEMA_VERSION
    elif trend_enabled:
        schema_version = CREATIVE_TREND_SCHEMA_VERSION
    elif actor_pair_enabled:
        schema_version = DIRECTOR_ACTOR_PAIR_SCHEMA_VERSION
    elif writer_pair_enabled:
        schema_version = DIRECTOR_WRITER_PAIR_SCHEMA_VERSION
    elif creative_enabled:
        schema_version = CREATIVE_TEAM_SCHEMA_VERSION
    elif coverage_enabled:
        schema_version = COVERAGE_SCHEMA_VERSION
    else:
        schema_version = BASELINE_SCHEMA_VERSION

    metadata["schema_version"] = schema_version
    metadata["coverage_features_version"] = 1 if coverage_enabled else 0
    metadata["creative_team_features_version"] = 1 if creative_enabled else 0
    metadata["director_writer_pair_features_version"] = 1 if writer_pair_enabled else 0
    metadata["director_actor_pair_features_version"] = 1 if actor_pair_enabled else 0
    metadata["creative_trend_features_version"] = 1 if trend_enabled else 0
    metadata["director_team_features_version"] = 1 if director_team_enabled else 0
    metadata["full_cast_features_version"] = 1 if full_cast_enabled else 0
    metadata["cast_pair_features_version"] = 1 if cast_pair_enabled else 0
    metadata["dual_role_features_version"] = 1 if dual_role_enabled else 0
    metadata["team_collaboration_features_version"] = 1 if team_collaboration_enabled else 0

    interpret_model(model, metadata)

    if args.smoke or args.evaluation_only:
        artifact_path = None
        kind = "smoke" if args.smoke else "evaluation"
        try:
            artifact_path, size_bytes = _save_temporary_artifact(model, kind)
            _log_evaluation_summary(metadata, size_bytes)
        finally:
            if artifact_path is not None:
                shutil.rmtree(artifact_path.parent, ignore_errors=True)
        return

    if not all((
        coverage_enabled,
        creative_enabled,
        writer_pair_enabled,
        actor_pair_enabled,
        trend_enabled,
        director_team_enabled,
        full_cast_enabled,
        cast_pair_enabled,
        dual_role_enabled,
        team_collaboration_enabled,
    )):
        raise SystemExit("Неполную baseline-схему нельзя публиковать через этот entrypoint.")

    save_trained_model(model, metadata)
    logger.info("ОБУЧЕНИЕ ЗАВЕРШЕНО УСПЕШНО")


if __name__ == "__main__":
    main()
