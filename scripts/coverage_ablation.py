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
from src.data_filtr import get_all_genres
from src.logger import setup_logger
from src.train_model import train_catboost_model


logger = setup_logger(__name__)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Непубликуемый temporal ablation: baseline v5 без coverage-признаков "
            "против candidate v6 на одной IMDb БД."
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


def database_signature(path: Path) -> dict[str, int]:
    """Фиксирует дешёвую сигнатуру БД и позволяет заметить concurrent ds_update."""
    stat = path.stat()
    return {
        "size": int(stat.st_size),
        "mtime_ns": int(stat.st_mtime_ns),
        "inode": int(getattr(stat, "st_ino", 0)),
    }


def _assert_database_unchanged(path: Path, expected: dict[str, int]) -> None:
    actual = database_signature(path)
    if actual != expected:
        raise RuntimeError(
            "IMDb БД изменилась во время ablation. Сравнение остановлено, "
            "чтобы baseline и candidate не обучались на разных данных."
        )


def _serialize_size(model, root: Path, label: str) -> int:
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{label}.cbm"
    model.save_model(str(path), format="cbm")
    return int(path.stat().st_size)


def _result_from_metadata(
    metadata: dict[str, Any],
    *,
    label: str,
    schema_version: int,
    coverage_features_version: int,
    model_size_bytes: int,
) -> dict[str, Any]:
    return {
        "label": label,
        "schema_version": schema_version,
        "coverage_features_version": coverage_features_version,
        "test_mae": float(metadata["test_mae"]),
        "test_rmse": float(metadata["test_rmse"]),
        "test_r2": float(metadata["test_r2"]),
        "train_year_from": metadata.get("train_year_from"),
        "train_year_to": metadata.get("train_year_to"),
        "test_year_from": metadata.get("test_year_from"),
        "test_year_to": metadata.get("test_year_to"),
        "train_rows": metadata.get("train_rows"),
        "test_rows": metadata.get("test_rows"),
        "total_rows": metadata.get("total_rows"),
        "feature_count": len(metadata.get("feature_names") or []),
        "feature_names": list(metadata.get("feature_names") or []),
        "model_size_bytes": model_size_bytes,
    }


def compare_results(
    baseline: dict[str, Any],
    candidate: dict[str, Any],
    *,
    max_mae_regression: float,
) -> dict[str, Any]:
    """Сравнивает результаты только при идентичном temporal holdout."""
    comparable_keys = (
        "train_year_from",
        "train_year_to",
        "test_year_from",
        "test_year_to",
        "train_rows",
        "test_rows",
        "total_rows",
    )
    mismatches = {
        key: [baseline.get(key), candidate.get(key)]
        for key in comparable_keys
        if baseline.get(key) != candidate.get(key)
    }
    if mismatches:
        raise RuntimeError(
            "Baseline и candidate получили разные temporal datasets: "
            + json.dumps(mismatches, ensure_ascii=False)
        )

    delta_mae = float(candidate["test_mae"]) - float(baseline["test_mae"])
    delta_rmse = float(candidate["test_rmse"]) - float(baseline["test_rmse"])
    delta_r2 = float(candidate["test_r2"]) - float(baseline["test_r2"])
    tolerance = max(0.0, float(max_mae_regression))

    return {
        "delta_mae": delta_mae,
        "delta_rmse": delta_rmse,
        "delta_r2": delta_r2,
        "candidate_improves_mae": delta_mae < 0,
        "non_regression_passed": delta_mae <= tolerance,
        "max_mae_regression": tolerance,
        "note": (
            "Ablation не публикует модель. non_regression_passed означает только "
            "прохождение порога MAE на том же temporal dataset."
        ),
    }


def _train_variant(
    *,
    label: str,
    coverage_enabled: bool,
    genres: list[str],
    iterations: int,
    batch_size: int,
    max_batches: int | None,
    artifact_root: Path,
    db_path: Path,
    db_signature: dict[str, int],
) -> dict[str, Any]:
    os.environ["VANGA_TRAIN_COVERAGE_FEATURES"] = "1" if coverage_enabled else "0"
    logger.info("=" * 60)
    logger.info(
        "ABLATION: старт %s; coverage_features=%s",
        label,
        "on" if coverage_enabled else "off",
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
            schema_version=6 if coverage_enabled else 5,
            coverage_features_version=1 if coverage_enabled else 0,
            model_size_bytes=size_bytes,
        )
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
    work_root = Path(config.ABSPATH) / "temp" / "coverage-ablation" / stamp

    baseline = _train_variant(
        label="baseline-v5",
        coverage_enabled=False,
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
        label="candidate-v6",
        coverage_enabled=True,
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
        "database": {
            "path": str(db_path),
            "signature": signature,
        },
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
            / f"coverage-{stamp}.json"
        )
    output = output.expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    logger.info("=" * 60)
    logger.info("ABLATION ЗАВЕРШЁН")
    logger.info("Baseline MAE: %.6f", baseline["test_mae"])
    logger.info("Candidate MAE: %.6f", candidate["test_mae"])
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
