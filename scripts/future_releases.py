from __future__ import annotations

import argparse
import json
from pathlib import Path

from src.future_releases import FutureReleaseStore


def _load(path: str):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="P9 локальный registry будущих релизов с provenance/confidence"
    )
    parser.add_argument("--db", help="Путь к future_releases.duckdb")
    sub = parser.add_subparsers(dest="command", required=True)

    imp = sub.add_parser("import-bundle", help="Импортировать нормализованный JSON bundle")
    imp.add_argument("bundle_json")

    snap = sub.add_parser("snapshot", help="Показать project snapshot as-of cutoff")
    snap.add_argument("project_id")
    snap.add_argument("--cutoff", required=True)
    snap.add_argument("--territory", default="worldwide")

    catalog = sub.add_parser("catalog", help="Собрать локальный каталог будущих релизов")
    catalog.add_argument("--cutoff", required=True)
    catalog.add_argument("--territory", default="worldwide")
    catalog.add_argument("--from-at")
    catalog.add_argument("--to-at")
    catalog.add_argument("--exclude-conflicts", action="store_true")

    resolve = sub.add_parser("resolve-title", help="Exact-normalized title alias resolution")
    resolve.add_argument("title")
    resolve.add_argument("--cutoff", required=True)

    args = parser.parse_args()
    kwargs = {"path": args.db} if args.db else {}
    with FutureReleaseStore(**kwargs) as store:
        if args.command == "import-bundle":
            payload = _load(args.bundle_json)
            if not isinstance(payload, dict):
                raise ValueError("Bundle должен быть JSON-объектом")
            result = {
                "sources": [store.upsert_source(item) for item in payload.get("sources", [])],
                "projects": [store.upsert_project(item) for item in payload.get("projects", [])],
                "people": [store.upsert_person(item) for item in payload.get("people", [])],
                "entities": [store.upsert_entity(item) for item in payload.get("entities", [])],
                "aliases": [store.add_alias(item) for item in payload.get("aliases", [])],
                "release_windows": [
                    store.add_release_window(item)
                    for item in payload.get("release_windows", [])
                ],
                "statuses": [store.add_status(item) for item in payload.get("statuses", [])],
                "project_people": [
                    store.link_person(item) for item in payload.get("project_people", [])
                ],
                "project_entities": [
                    store.link_entity(item) for item in payload.get("project_entities", [])
                ],
            }
        elif args.command == "snapshot":
            result = store.snapshot_as_of(
                args.project_id,
                args.cutoff,
                territory=args.territory,
            )
        elif args.command == "catalog":
            result = store.catalog_as_of(
                args.cutoff,
                territory=args.territory,
                from_at=args.from_at,
                to_at=args.to_at,
                include_conflicts=not args.exclude_conflicts,
            )
        else:
            result = store.resolve_title_as_of(args.title, args.cutoff)

    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
