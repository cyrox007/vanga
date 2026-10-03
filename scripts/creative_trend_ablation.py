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
train_model_module.get_batches = creative_get_batches


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Непубликуемый temporal ablation: baseline v9 против "
            "Creative Team trend candidate v10 на одной IMDb БД."
        )
    )
    parser.add_argument("--iterations", type=int, default=1500)
    parser.add_argument("--batch-size", type=int, default=10000)
    parser.add_argument("--max-batches", type=int, default=None)
    parser.add_argument("--output", type=Path, default=None)
    return parser


def _train_variant(
    *,
    label: str,
    trend_enabled: bool,
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
    os.environ["VANGA_TRAIN_DIRECTOR_ACTOR_PAIR_FEATURES"] = "1"
    os.environ["VANGA_TRAIN_CREATIVE_TREND_FEATURES"] = "1" if trend_enabled else "0"
    # Schema v11 появилась позже. Старый v9→v10 experiment обязан оставаться
    # воспроизводимым и не включать multi-director block даже при trend=on.
    os.environ["VANGA_TRAIN_DIRECTOR_TEAM_FEATURES"] = "0"

    logger.info("=" * 60)
    logger.info(
        "CREATIVE TREND ABLATION: старт %s; trend=%s",
        label,
        "on" if trend_enabled else "off",
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
            schema_version=10 if trend_enabled else 9,
            coverage_features_version=1,
            model_size_bytes=size_bytes,
        )
        result["creative_team_features_version"] = 1
        result["director_writer_pair_features_version"] = 1
        result["director_actor_pair_features_version"] = 1
        result["creative_trend_features_version"] = 1 if trend_enabled else 0
        result["director_team_features_version"] = 0
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
    work_root = Path(config.ABSPATH) / "temp" / "creative-trend-ablation" / stamp

    baseline = _train_variant(
        label="baseline-v9",
        trend_enabled=False,
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
        label="candidate-v10",
        trend_enabled=True,
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
    output = args.output or (
        Path(config.ABSPATH) / "temp" / "ablation-reports" / f"creative-trend-{stamp}.json"
    )
    output = output.expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    logger.info("=" * 60)
    logger.info("CREATIVE TREND ABLATION ЗАВЕРШЁН")
    logger.info("Baseline v9 MAE: %.6f", baseline["test_mae"])
    logger.info("Candidate v10 MAE: %.6f", candidate["test_mae"])
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
