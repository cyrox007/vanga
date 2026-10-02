from __future__ import annotations

import argparse
import shutil
from datetime import datetime, timezone
from pathlib import Path

from settings import config
from src.train_model import (
    interpret_model,
    save_trained_model,
    train_catboost_model,
)
from src.data_filtr import get_all_genres
from src.logger import setup_logger


logger = setup_logger(__name__)


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


def _save_smoke_artifact(model) -> tuple[Path, int]:
    """
    Сериализует smoke-модель во временный файл и возвращает её размер.

    Файл нужен только для проверки, что новая конфигурация CatBoost не создаёт
    гигантский артефакт. Он никогда не становится активной моделью.
    """
    smoke_root = Path(config.ABSPATH) / "temp" / "smoke"
    smoke_root.mkdir(parents=True, exist_ok=True)
    path = smoke_root / (
        datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        + "-model.cbm"
    )
    model.save_model(str(path), format="cbm")
    return path, path.stat().st_size


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

    logger.info("=" * 60)
    logger.info("ЗАПУСК ОБУЧЕНИЯ CATBOOST")
    logger.info(
        "Режим: %s; iterations=%s; batch_size=%s; max_batches=%s",
        "SMOKE (без публикации)" if args.smoke else "FULL",
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

    interpret_model(model, metadata)

    if args.smoke:
        smoke_path = None
        try:
            smoke_path, size_bytes = _save_smoke_artifact(model)
            logger.info(
                "SMOKE: модель сериализована, размер=%.1f МБ, путь=%s",
                size_bytes / 1024 / 1024,
                smoke_path,
            )
            logger.info(
                "SMOKE: current.json не изменяется, модель не публикуется"
            )
        finally:
            if smoke_path is not None:
                shutil.rmtree(smoke_path.parent, ignore_errors=True)
        logger.info("SMOKE-ПРОВЕРКА ЗАВЕРШЕНА УСПЕШНО")
        return

    save_trained_model(model, metadata)
    logger.info("ОБУЧЕНИЕ ЗАВЕРШЕНО УСПЕШНО")


if __name__ == "__main__":
    main()
