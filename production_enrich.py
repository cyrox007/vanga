from __future__ import annotations

import argparse
import logging

from settings import config
from src.production_wikidata import (
    ProductionWikidataCache,
    ProductionWikidataClient,
    enrich_production_batch,
)
from src.wikimedia_enrichment import WikimediaClient


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Постепенное обогащение cached Wikidata-фильмов production metadata: "
            "компании, продюсеры и series/franchise candidates."
        )
    )
    parser.add_argument(
        "--enrichment-db",
        default=config.ENRICHMENT_DB_PATH,
        help="Путь к enrichment.duckdb.",
    )
    parser.add_argument(
        "--max-items",
        type=int,
        default=100,
        help="Максимум фильмов за запуск. 0 = без ограничения.",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=10,
        help="Размер Wikidata batch.",
    )
    parser.add_argument(
        "--reset-cursor",
        action="store_true",
        help="Сбросить production enrichment cursor.",
    )
    parser.add_argument(
        "--retry-errors",
        action="store_true",
        help="Повторить production metadata records со status=error.",
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
    if not 1 <= args.batch_size <= 50:
        raise SystemExit("--batch-size должен быть от 1 до 50")

    cache = ProductionWikidataCache(args.enrichment_db)
    client = ProductionWikidataClient(WikimediaClient())
    state_key = "production_wikidata_cursor"
    try:
        if args.reset_cursor:
            cache.reset_state(state_key)
            logging.info("Production Wikidata cursor сброшен.")

        total_processed = 0
        total_ok = 0
        total_failed = 0
        cursor = cache.get_state(state_key, "")

        while args.max_items == 0 or total_processed < args.max_items:
            remaining = (
                args.batch_size
                if args.max_items == 0
                else min(args.batch_size, args.max_items - total_processed)
            )
            candidates = cache.load_candidates(
                after_imdb=cursor,
                limit=remaining,
                retry_errors=args.retry_errors,
            )
            if not candidates:
                logging.info("Новых production metadata кандидатов нет.")
                break

            ok, failed = enrich_production_batch(candidates, client=client, cache=cache)
            total_ok += ok
            total_failed += failed
            total_processed += len(candidates)

            if not args.retry_errors:
                cursor = candidates[-1].imdb_id
                cache.set_state(state_key, cursor)

            logging.info(
                "Production enrichment: обработано=%s, успешно=%s, ошибки=%s, cursor=%s.",
                total_processed,
                total_ok,
                total_failed,
                cursor or "-",
            )

            if args.retry_errors:
                # Один проход по текущему retry-набору; иначе записи error будут
                # немедленно выбираться повторно в том же запуске.
                break

        logging.info(
            "Production enrichment завершён: обработано=%s, успешно=%s, ошибки=%s.",
            total_processed,
            total_ok,
            total_failed,
        )
        return 0 if total_failed == 0 else 2
    finally:
        cache.close()


if __name__ == "__main__":
    raise SystemExit(main())
