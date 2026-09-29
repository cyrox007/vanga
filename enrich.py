from __future__ import annotations

import argparse
import logging
from pathlib import Path

from settings import config
from src.wikimedia_enrichment import (
    EnrichmentStore,
    WikimediaClient,
    enrich_batch,
    load_candidates,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Постепенное обогащение IMDb данными Wikidata и Wikipedia."
    )
    parser.add_argument(
        "--imdb-db",
        default=str(Path(config.ABSPATH) / "imdb.duckdb"),
        help="Путь к исходной imdb.duckdb.",
    )
    parser.add_argument(
        "--enrichment-db",
        default=config.ENRICHMENT_DB_PATH,
        help="Путь к enrichment.duckdb.",
    )
    parser.add_argument(
        "--since-year",
        type=int,
        default=2020,
        help="Обрабатывать фильмы начиная с указанного года.",
    )
    parser.add_argument(
        "--max-items",
        type=int,
        default=100,
        help="Максимум фильмов за один запуск. 0 = без ограничения.",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=10,
        help="Размер батча IMDb→Wikidata.",
    )
    parser.add_argument(
        "--reset-cursor",
        action="store_true",
        help="Начать проход выбранного диапазона заново.",
    )
    parser.add_argument(
        "--retry-errors",
        action="store_true",
        help="Повторить ранее завершившиеся ошибкой записи.",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-8s | %(message)s",
    )

    if args.since_year < 1888:
        raise SystemExit("--since-year должен быть не меньше 1888")
    if args.max_items < 0:
        raise SystemExit("--max-items не может быть отрицательным")
    if not 1 <= args.batch_size <= 50:
        raise SystemExit("--batch-size должен быть от 1 до 50")

    store = EnrichmentStore(args.enrichment_db)
    client = WikimediaClient()
    try:
        if args.retry_errors:
            retry_limit = args.max_items or 1000
            candidates = store.load_retry_candidates(retry_limit)
            total_ok = 0
            total_failed = 0
            for start in range(0, len(candidates), args.batch_size):
                ok, failed = enrich_batch(
                    candidates[start:start + args.batch_size],
                    client=client,
                    store=store,
                )
                total_ok += ok
                total_failed += failed
            logging.info(
                "Повтор ошибок завершён: успешно=%s, снова с ошибкой=%s.",
                total_ok,
                total_failed,
            )
            return 0 if total_failed == 0 else 2

        state_key = f"wikimedia_cursor_since_{args.since_year}"
        if args.reset_cursor:
            store.reset_state(state_key)
            logging.info("Курсор %s сброшен.", state_key)

        cursor = store.get_state(state_key, "")
        total_processed = 0
        total_ok = 0
        total_failed = 0

        while args.max_items == 0 or total_processed < args.max_items:
            remaining = (
                args.batch_size
                if args.max_items == 0
                else min(args.batch_size, args.max_items - total_processed)
            )
            candidates = load_candidates(
                args.imdb_db,
                after_tconst=cursor,
                since_year=args.since_year,
                limit=remaining,
            )
            if not candidates:
                logging.info("Новых кандидатов в выбранном диапазоне нет.")
                break

            ok, failed = enrich_batch(
                candidates,
                client=client,
                store=store,
            )
            total_ok += ok
            total_failed += failed
            total_processed += len(candidates)

            cursor = candidates[-1].imdb_id
            store.set_state(state_key, cursor)

            logging.info(
                "Прогресс: обработано=%s, Wikidata/Wikipedia=%s, ошибки=%s, курсор=%s.",
                total_processed,
                total_ok,
                total_failed,
                cursor,
            )

        logging.info(
            "Enrichment завершён: обработано=%s, успешно=%s, ошибки=%s.",
            total_processed,
            total_ok,
            total_failed,
        )
        return 0 if total_failed == 0 else 2
    finally:
        store.close()


if __name__ == "__main__":
    raise SystemExit(main())
