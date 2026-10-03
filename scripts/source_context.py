#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.source_context import SourceContextStore, SourceContextValidationError


def _dump(payload: Any) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))


def _load(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise SourceContextValidationError("Корень bundle должен быть JSON-объектом")
    return payload


def _apply_bundle(store: SourceContextStore, payload: dict[str, Any]) -> dict[str, int]:
    counters = {
        "sources": 0,
        "works": 0,
        "creators": 0,
        "creator_links": 0,
        "projects": 0,
        "source_links": 0,
    }
    operations = (
        ("sources", store.upsert_source),
        ("works", store.upsert_work),
        ("creators", store.upsert_creator),
        ("creator_links", store.link_creator),
        ("projects", store.upsert_project),
        ("source_links", store.link_source),
    )
    for key, handler in operations:
        rows = payload.get(key) or []
        if not isinstance(rows, list):
            raise SourceContextValidationError(f"{key} должен быть массивом")
        for row in rows:
            if not isinstance(row, dict):
                raise SourceContextValidationError(
                    f"Каждый элемент {key} должен быть JSON-объектом"
                )
            handler(row)
            counters[key] += 1
    return counters


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Pre-release Source Context registry Vanga"
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=None,
        help="путь к source_context.duckdb (по умолчанию settings.py)",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init", help="создать/проверить schema source context")

    import_parser = sub.add_parser("import", help="импортировать JSON bundle")
    import_parser.add_argument("bundle", type=Path)

    snapshot = sub.add_parser("snapshot", help="показать pre-release features as-of cutoff")
    snapshot.add_argument("project_id")
    snapshot.add_argument("cutoff", help="ISO datetime")

    links = sub.add_parser("links", help="показать source links, известные на cutoff")
    links.add_argument("project_id")
    links.add_argument("cutoff", help="ISO datetime")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    store = SourceContextStore(args.db)
    try:
        if args.command == "init":
            _dump({"ok": True, "database": str(store.path)})
            return 0
        if args.command == "import":
            counters = _apply_bundle(store, _load(args.bundle))
            _dump({"ok": True, "database": str(store.path), "imported": counters})
            return 0
        if args.command == "snapshot":
            _dump(
                {
                    "ok": True,
                    "project_id": args.project_id,
                    "cutoff": args.cutoff,
                    "features": store.features_as_of(args.project_id, args.cutoff),
                }
            )
            return 0
        if args.command == "links":
            _dump(
                {
                    "ok": True,
                    "project_id": args.project_id,
                    "cutoff": args.cutoff,
                    "links": store.links_as_of(args.project_id, args.cutoff),
                }
            )
            return 0
        raise SourceContextValidationError("Неизвестная команда")
    except (SourceContextValidationError, OSError, json.JSONDecodeError) as exc:
        print(f"Ошибка Source Context: {exc}", file=sys.stderr)
        return 2
    finally:
        store.close()


if __name__ == "__main__":
    raise SystemExit(main())
