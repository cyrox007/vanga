from __future__ import annotations

import argparse
import json
from pathlib import Path

from settings import config
from src.future_release_import import FutureReleaseBatchImporter
from src.wikidata_future_enrichment import WDQS_ENDPOINT, WikidataFutureEnricher
from src.wikidata_future_facts import WikidataFutureFactsCollector
from src.wikidata_future_refresh import WikidataFutureRefresh


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Единый P9 Wikidata refresh: команда/source context + temporal runtime/genres"
        )
    )
    parser.add_argument(
        "--registry-db",
        default=config.FUTURE_RELEASE_DB_PATH,
        help="Путь к future_releases.duckdb",
    )
    parser.add_argument(
        "--project-id",
        action="append",
        default=[],
        help="Обновить только указанный project_id; параметр можно повторять",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=100,
        help="Максимум проектов за один WDQS batch (не больше 200)",
    )
    parser.add_argument(
        "--retrieved-at",
        help="Явный ISO-8601 timestamp наблюдения; по умолчанию текущее UTC",
    )
    parser.add_argument(
        "--endpoint",
        default=WDQS_ENDPOINT,
        help=(
            "SPARQL endpoint. Facts collector совместим со SPARQL 1.1; основной "
            "team enrichment пока использует текущий WDQS label service."
        ),
    )
    parser.add_argument("--output", help="Сохранить collector result JSON в файл")
    parser.add_argument(
        "--import",
        dest="do_import",
        action="store_true",
        help="После сбора атомарно импортировать единый batch в registry",
    )
    args = parser.parse_args()

    refresh = WikidataFutureRefresh(
        enricher=WikidataFutureEnricher(endpoint=args.endpoint),
        facts=WikidataFutureFactsCollector(endpoint=args.endpoint),
    )
    result = refresh.collect_from_registry(
        args.registry_db,
        project_ids=args.project_id or None,
        limit=args.limit,
        retrieved_at=args.retrieved_at,
    )

    if args.do_import:
        with FutureReleaseBatchImporter(args.registry_db) as importer:
            result["import_result"] = importer.import_batch(result["batch"])

    rendered = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True)
    if args.output:
        target = Path(args.output)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(rendered + "\n", encoding="utf-8")
        print(f"Результат Wikidata refresh сохранён: {target}")
    else:
        print(rendered)

    if result.get("warning_count"):
        print(f"Предупреждения Wikidata refresh: {result['warning_count']}")
    if args.do_import:
        imported = result["import_result"]
        state = "идемпотентный повтор" if imported.get("idempotent") else "импортирован"
        print(f"Единый P9 batch {state}: {imported['batch_id']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
