from __future__ import annotations

import argparse
import json
from pathlib import Path

from settings import config
from src.future_territories import FutureTerritoryStore
from src.wikidata_territories import WikidataTerritoryCollector


def main() -> int:
    parser = argparse.ArgumentParser(
        description="P9: канонизация Wikidata territory QID в ISO 3166-1"
    )
    parser.add_argument(
        "--registry-db",
        default=config.FUTURE_RELEASE_DB_PATH,
        help="Путь к future_releases.duckdb",
    )
    parser.add_argument(
        "--qid",
        action="append",
        default=[],
        help="Явный Wikidata territory QID; можно повторять",
    )
    parser.add_argument("--limit", type=int, default=500, help="Максимум territory QID")
    parser.add_argument("--retrieved-at", help="Явный ISO-8601 timestamp наблюдения")
    parser.add_argument("--output", help="Сохранить collector result JSON в файл")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Атомарно применить mappings к локальному registry",
    )
    parser.add_argument(
        "--show-project",
        help="После apply показать canonical release snapshot указанного project_id",
    )
    parser.add_argument(
        "--territory",
        default="US",
        help="Canonical ISO territory для --show-project, например US",
    )
    parser.add_argument(
        "--cutoff",
        help="Cutoff для --show-project; по умолчанию retrieved-at/текущее наблюдение",
    )
    args = parser.parse_args()

    collector = WikidataTerritoryCollector()
    result = collector.collect(
        args.registry_db,
        qids=args.qid or None,
        limit=args.limit,
        retrieved_at=args.retrieved_at,
    )
    if args.apply:
        result["apply_result"] = collector.apply(args.registry_db, result)

    if args.show_project:
        with FutureTerritoryStore(args.registry_db) as store:
            result["release_snapshot"] = store.release_snapshot_as_of(
                args.show_project,
                args.cutoff or result["retrieved_at"],
                canonical_territory=args.territory,
            )

    rendered = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True)
    if args.output:
        target = Path(args.output)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(rendered + "\n", encoding="utf-8")
        print(f"Результат territory mapping сохранён: {target}")
    else:
        print(rendered)

    print(
        f"Territory mapping: QID={result['territory_count']}, "
        f"mapping={result['mapping_count']}, предупреждений={result['warning_count']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
