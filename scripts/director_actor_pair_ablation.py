#!/usr/bin/env python3
from __future__ import annotations

import argparse
import gc
import json
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from settings import config
import src.train_model as train_model_module
from scripts.coverage_ablation import (
    _assert_database_unchanged,
    _result_from_metadata,
    _serialize_size,
    compare_results,
    database_signature,
)
from src.creative_training import get_batches as creative_get_batches
from src.data_filtr import get_all_genres
from src.logger import setup_logger
from src.train_model import train_catboost_model


logger = setup_logger(__name__)

# Оба варианта используют coverage + Creative Team + director↔writer schema v8.
# Единственное отличие — история режиссёра с первыми тремя актёрами target-фильма.
# Creative trend schema v10 здесь принудительно выключена, чтобы v8→v9 ablation
# оставался воспроизводимым после добавления следующих блоков.
train_model_module.get_batches = creative_get_batches


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Непубликуемый temporal ablation: director-writer baseline v8 "
            "против director-actor pair candidate v9 на одной IMDb БД."
        )
    )
    parser.add_argument("--iterations", type=int, default=1500)
    parser.add_argument("--batch-size", type=int, default=10000)
    parser.add_argument("--max-batches", type=int, default=None)
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="путь к JSON-отчёту; по умолчанию temp/ablation-reports/...json",
    )
    return parser


def _train_variant(
    *,
    label: str,
    actor_pair_enabled: bool,
    genres: list[str],
    iterations: int,
    batch_size: int,
    max_batches: int | None,
    artifact_root: Path,
    db_path: Path,
    db_signature: dict[str, int],
) -> dict[str, Any]:
    os.environ["VANGA_TRAIN_COVERAGE_FEATURES"] = "1"
    os.environ["VANGA_TRAIN_CREATIVE_TEAM_FEATURES"] = "1"
    os.environ["VANGA_TRAIN_DIRECTOR_WRITER_PAIR_FEATURES"] = "1"
    os.environ["VANGA_TRAIN_DIRECTOR_ACTOR_PAIR_FEATURES"] = (
        "1" if actor_pair_enabled else "0"
    )
    os.environ["VANGA_TRAIN_CREATIVE_TREND_FEATURES"] = "0"
    logger.info("=" * 60)
    logger.info(
        "DIRECTOR-ACTOR PAIR ABLATION: старт %s; director_actor=%s; trend=off",
        label,
        "on" if actor_pair_enabled else "off",
    )

    model, metadata = train_catboost_model(
        genres,
        batch_size=batch_size,
        max_batches=max_batches,
        iterations=iterations,
    )
    _assert_database_unchanged(db_path, db_signature)

    try:
        size_bytes = _serialize_size(model, artifact_root, label)
        result = _result_from_metadata(
            metadata,
            label=label,
            schema_version=9 if actor_pair_enabled else 8,
            coverage_features_version=1,
            model_size_bytes=size_bytes,
        )
        result["creative_team_features_version"] = 1
        result["director_writer_pair_features_version"] = 1
        result["director_actor_pair_features_version"] = (
            1 if actor_pair_enabled else 0
        )
        result["creative_trend_features_version"] = 0
        logger.info(
            "ABLATION %s: MAE=%.6f RMSE=%.6f R²=%.6f features=%s size=%.2f МБ",
            label,
            result["test_mae"],
            result["test_rmse"],
            result["test_r2"],
            result["feature_count"],
            size_bytes / 1024 / 1024,
        )
        return result
    finally:
        del model
        gc.collect()
        shutil.rmtree(artifact_root, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.iterations < 1:
        raise SystemExit("--iterations должно быть положительным числом")
    if args.batch_size < 1:
        raise SystemExit("--batch-size должно быть положительным числом")
    if args.max_batches is not None and args.max_batches < 1:
        raise SystemExit("--max-batches должно быть положительным числом")

    db_path = Path(config.IMDB_DB_PATH)
    if not db_path.is_file():
        raise SystemExit(f"IMDb БД не найдена: {db_path}")

    signature = database_signature(db_path)
    genres = get_all_genres()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    work_root = Path(config.ABSPATH) / "temp" / "director-actor-pair-ablation" / stamp

    baseline = _train_variant(
        label="baseline-v8",
        actor_pair_enabled=False,
        genres=genres,
        iterations=args.iterations,
        batch_size=args.batch_size,
        max_batches=args.max_batches,
        artifact_root=work_root / "baseline",
        db_path=db_path,
        db_signature=signature,
    )
    _assert_database_unchanged(db_path, signature)

    candidate = _train_variant(
        label="candidate-v9",
        actor_pair_enabled=True,
        genres=genres,
        iterations=args.iterations,
        batch_size=args.batch_size,
        max_batches=args.max_batches,
        artifact_root=work_root / "candidate",
        db_path=db_path,
        db_signature=signature,
    )
    _assert_database_unchanged(db_path, signature)

    comparison = compare_results(
        baseline,
        candidate,
        max_mae_regression=config.TRAIN_MAX_MAE_REGRESSION,
    )
    report = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "database": {"path": str(db_path), "signature": signature},
        "iterations": args.iterations,
        "batch_size": args.batch_size,
        "max_batches": args.max_batches,
        "baseline": baseline,
        "candidate": candidate,
        "comparison": comparison,
        "published": False,
    }

    output = args.output
    if output is None:
        output = (
            Path(config.ABSPATH)
            / "temp"
            / "ablation-reports"
            / f"director-actor-pair-{stamp}.json"
        )
    output = output.expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    logger.info("=" * 60)
    logger.info("DIRECTOR-ACTOR PAIR ABLATION ЗАВЕРШЁН")
    logger.info("Baseline v8 MAE: %.6f", baseline["test_mae"])
    logger.info("Candidate v9 MAE: %.6f", candidate["test_mae"])
    logger.info("Δ MAE: %+.6f", comparison["delta_mae"])
    logger.info(
        "Non-regression: %s",
        "ПРОЙДЕН" if comparison["non_regression_passed"] else "НЕ ПРОЙДЕН",
    )
    logger.info("Отчёт: %s", output)
    logger.info("Активная модель НЕ ИЗМЕНЕНА")
    logger.info("=" * 60)
    return 0 if comparison["non_regression_passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
