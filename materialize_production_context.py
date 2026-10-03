from __future__ import annotations

import argparse
import logging
from datetime import datetime, timezone

from settings import config
from src.production_context import ProductionContextStore
from src.production_materializer import ProductionContextMaterializer


REFRESH_STATE_KEY = "wikidata_production_materializer_refresh_cursor"


def _get_state(store: ProductionContextStore, key: str) -> str:
    row = store.conn.execute(
        "SELECT state_value FROM production_materialization_state WHERE state_key = ?",
        [key],
    ).fetchone()
    return str(row[0]) if row else ""


def _set_state(store: ProductionContextStore, key: str, value: str) -> None:
    store.conn.execute(
        """
        INSERT INTO production_materialization_state(state_key, state_value, updated_at)
        VALUES (?, ?, ?)
        ON CONFLICT(state_key) DO UPDATE SET
            state_value = excluded.state_value,
            updated_at = excluded.updated_at
        """,
        [key, value, datetime.now(timezone.utc)],
    )


def _reset_state(store: ProductionContextStore, key: str) -> None:
    store.conn.execute(
        "DELETE FROM production_materialization_state WHERE state_key = ?",
        [key],
    )


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
        help="Сбросить cursor выбранного materialization режима.",
    )
    parser.add_argument(
        "--refresh-known",
        action="store_true",
        help=(
            "Повторно материализовать уже обработанные IMDb IDs после refresh production cache. "
            "Использует отдельный циклический cursor."
        ),
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
    state_key = REFRESH_STATE_KEY if args.refresh_known else materializer.STATE_KEY
    try:
        if args.reset_cursor:
            _reset_state(store, state_key)
            logging.info("Production Context materialization cursor %s сброшен.", state_key)

        cursor = _get_state(store, state_key)
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
                logging.info(
                    "Cached production metadata для materialization нет (режим=%s).",
                    "refresh" if args.refresh_known else "new",
                )
                if args.refresh_known:
                    _reset_state(store, state_key)
                    cursor = ""
                    logging.info(
                        "Полный materialization refresh-cycle завершён; cursor сброшен для следующего запуска."
                    )
                break
            processed += count
            for key, value in batch_totals.items():
                totals[key] += value
            cursor = new_cursor
            _set_state(store, state_key, cursor)
            logging.info(
                "Materialization: режим=%s, films=%s, companies=%s, producers=%s, series=%s, cursor=%s.",
                "refresh" if args.refresh_known else "new",
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
