from __future__ import annotations

import argparse
import json
from pathlib import Path

from settings import config
from src.future_release_import import FutureReleaseBatchImporter
from src.tmdb_future_releases import TmdbFutureReleaseCollector


def main() -> int:
    parser = argparse.ArgumentParser(
        description="P9 TMDb: независимое обновление региональных theatrical release dates"
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
        help="Обновить только указанный project_id; можно повторять",
    )
    parser.add_argument("--limit", type=int, default=100, help="Максимум проектов за запуск")
    parser.add_argument(
        "--retrieved-at",
        help="Явный ISO-8601 timestamp наблюдения; по умолчанию текущее UTC",
    )
    parser.add_argument("--output", help="Сохранить collector result JSON в файл")
    parser.add_argument(
        "--import",
        dest="do_import",
        action="store_true",
        help="После сбора атомарно импортировать batch в P9 registry",
    )
    args = parser.parse_args()

    collector = TmdbFutureReleaseCollector()
    result = collector.collect_from_registry(
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
        print(f"Результат TMDb collector сохранён: {target}")
    else:
        print(rendered)

    print(
        "TMDb: проектов={projects}, разрешено={resolved}, дат={dates}, предупреждений={warnings}".format(
            projects=result["project_count"],
            resolved=result["resolved_project_count"],
            dates=result["release_observation_count"],
            warnings=result["warning_count"],
        )
    )
    if args.do_import:
        state = "идемпотентный повтор" if result["import_result"].get("idempotent") else "импортирован"
        print(f"P9 batch {state}: {result['import_result']['batch_id']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
