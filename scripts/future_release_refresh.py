from __future__ import annotations

import argparse
import json
from pathlib import Path

from settings import config
from src.future_release_refresh import (
    FutureReleaseRefreshError,
    FutureReleaseRefreshPipeline,
)


def _render(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Единый P9 refresh: Wikidata discovery -> atomic import -> "
            "enrichment -> atomic import"
        )
    )
    parser.add_argument(
        "--db",
        default=config.FUTURE_RELEASE_DB_PATH,
        help="Путь к future_releases.duckdb",
    )
    parser.add_argument("--from-at", required=True, help="Начало окна будущих релизов")
    parser.add_argument("--to-at", required=True, help="Конец окна будущих релизов")
    parser.add_argument(
        "--discovery-limit",
        type=int,
        default=1000,
        help="Максимум строк discovery WDQS за один проход (1..5000)",
    )
    parser.add_argument(
        "--enrichment-chunk-size",
        type=int,
        default=100,
        help="Число проектов на один enrichment request (1..200)",
    )
    parser.add_argument(
        "--retrieved-at",
        help="Явный ISO-8601 timestamp всего refresh; по умолчанию текущее UTC",
    )
    parser.add_argument(
        "--output",
        help="Сохранить итоговый/частичный отчёт в JSON",
    )
    args = parser.parse_args()

    pipeline = FutureReleaseRefreshPipeline(args.db)
    exit_code = 0
    try:
        report = pipeline.run(
            args.from_at,
            args.to_at,
            discovery_limit=args.discovery_limit,
            enrichment_chunk_size=args.enrichment_chunk_size,
            retrieved_at=args.retrieved_at,
        )
        print(
            "P9 refresh завершён: "
            f"projects={len(report.get('project_ids') or [])}; "
            f"enrichment_batches={len(report.get('enrichment_batches') or [])}; "
            f"warnings={report.get('warning_count', 0)}"
        )
    except FutureReleaseRefreshError as exc:
        report = exc.report
        exit_code = 2
        print(f"P9 refresh завершён частично/с ошибкой: {exc}")
        print(
            "Стадия сбоя: "
            f"{report.get('failed_stage') or 'неизвестно'}"
        )

    rendered = _render(report)
    if args.output:
        target = Path(args.output)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(rendered + "\n", encoding="utf-8")
        print(f"Отчёт P9 refresh сохранён: {target}")
    else:
        print(rendered)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
