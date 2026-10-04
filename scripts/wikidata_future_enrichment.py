from __future__ import annotations

import argparse
import json
from pathlib import Path

from settings import config
from src.future_release_import import FutureReleaseBatchImporter
from src.wikidata_future_enrichment import WikidataFutureEnricher


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "P9 Wikidata enrichment: director/writer/cast/source/franchise/production company"
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
        help="Обогатить только указанный project_id; параметр можно повторять",
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
        "--output",
        help="Сохранить collector result JSON в файл",
    )
    parser.add_argument(
        "--import",
        dest="do_import",
        action="store_true",
        help="После сбора атомарно импортировать batch в тот же registry",
    )
    args = parser.parse_args()

    enricher = WikidataFutureEnricher()
    result = enricher.collect_from_registry(
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
        print(f"Результат enrichment сохранён: {target}")
    else:
        print(rendered)

    if result.get("warning_count"):
        print(
            f"Предупреждения enrichment: {result['warning_count']}",
        )
    if args.do_import:
        imported = result["import_result"]
        state = "идемпотентный повтор" if imported.get("idempotent") else "импортирован"
        print(f"P9 batch {state}: {imported['batch_id']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
