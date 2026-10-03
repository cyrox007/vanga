from __future__ import annotations

import argparse
import logging

from settings import config
from src.production_context import ProductionContextStore
from src.production_materializer import ProductionContextMaterializer


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Офлайн-материализация cached Wikidata production metadata "
            "из enrichment.duckdb в production_context.duckdb."
        )
    )
    parser.add_argument("--enrichment-db", default=config.ENRICHMENT_DB_PATH)
    parser.add_argument("--production-db", default=config.PRODUCTION_CONTEXT_DB_PATH)
    parser.add_argument(
        "--max-items",
        type=int,
        default=500,
        help="Максимум фильмов за запуск. 0 = без ограничения.",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=100,
        help="Размер локального batch чтения cache.",
    )
    parser.add_argument(
        "--reset-cursor",
        action="store_true",
        help="Сбросить materialization cursor.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-8s | %(message)s",
    )
    if args.max_items < 0:
        raise SystemExit("--max-items не может быть отрицательным")
    if args.batch_size < 1:
        raise SystemExit("--batch-size должен быть положительным")

    store = ProductionContextStore(args.production_db)
    materializer = ProductionContextMaterializer(
        enrichment_db=args.enrichment_db,
        production_store=store,
    )
    try:
        if args.reset_cursor:
            materializer.reset_cursor()
            logging.info("Production Context materialization cursor сброшен.")

        cursor = materializer.get_cursor()
        processed = 0
        totals = {"production_companies": 0, "producers": 0, "series": 0}

        while args.max_items == 0 or processed < args.max_items:
            limit = (
                args.batch_size
                if args.max_items == 0
                else min(args.batch_size, args.max_items - processed)
            )
            count, batch_totals, new_cursor = materializer.materialize_batch(
                after_imdb=cursor,
                limit=limit,
            )
            if count == 0:
                logging.info("Новых cached production metadata для materialization нет.")
                break
            processed += count
            for key, value in batch_totals.items():
                totals[key] += value
            cursor = new_cursor
            materializer.set_cursor(cursor)
            logging.info(
                "Materialization: films=%s, companies=%s, producers=%s, series=%s, cursor=%s.",
                processed,
                totals["production_companies"],
                totals["producers"],
                totals["series"],
                cursor,
            )

        logging.info(
            "Production Context materialization завершена: films=%s, companies=%s, producers=%s, series=%s.",
            processed,
            totals["production_companies"],
            totals["producers"],
            totals["series"],
        )
        return 0
    finally:
        materializer.close()
        store.close()


if __name__ == "__main__":
    raise SystemExit(main())
