from __future__ import annotations

import argparse
import json
from pathlib import Path

from src.future_release_import import FutureReleaseBatchImporter
from src.wikidata_future_releases import WDQS_ENDPOINT, WikidataFutureReleaseCollector


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "P9 Wikidata future-release discovery: P577 statement + P291 territory "
            "-> raw cache -> fingerprinted batch"
        )
    )
    parser.add_argument("--from-at", required=True)
    parser.add_argument("--to-at", required=True)
    parser.add_argument("--limit", type=int, default=1000)
    parser.add_argument("--retrieved-at")
    parser.add_argument("--cache-dir")
    parser.add_argument(
        "--endpoint",
        default=WDQS_ENDPOINT,
        help="SPARQL endpoint Wikidata/совместимого WDQS",
    )
    parser.add_argument("--output", help="Сохранить collector result JSON")
    parser.add_argument(
        "--import-db",
        help=(
            "После успешного collect атомарно импортировать batch в указанную "
            "future_releases.duckdb"
        ),
    )
    args = parser.parse_args()

    collector = WikidataFutureReleaseCollector(
        endpoint=args.endpoint,
        cache_dir=args.cache_dir,
    )
    result = collector.collect(
        args.from_at,
        args.to_at,
        limit=args.limit,
        retrieved_at=args.retrieved_at,
    )
    if args.import_db:
        with FutureReleaseBatchImporter(args.import_db) as importer:
            result["import"] = importer.import_batch(result["batch"])
    text = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True)
    if args.output:
        target = Path(args.output)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text + "\n", encoding="utf-8")
        print(f"Результат discovery сохранён: {target}")
    print(text)
    if result.get("warning_count"):
        print(f"Предупреждения release discovery: {result['warning_count']}")
    if args.import_db:
        imported = result["import"]
        state = "идемпотентный повтор" if imported.get("idempotent") else "импортирован"
        print(f"P9 release batch {state}: {imported['batch_id']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
