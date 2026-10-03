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

from src.production_context import (
    ProductionContextStore,
    ProductionContextValidationError,
)
from src.production_identity import (
    GROUP_KINDS,
    ProductionIdentityHistory,
)


def _json_dump(payload: Any) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))


def _load_bundle(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ProductionContextValidationError("Корень bundle должен быть JSON-объектом")
    return payload


def _apply_bundle(
    store: ProductionContextStore,
    identity: ProductionIdentityHistory,
    payload: dict[str, Any],
) -> dict[str, int]:
    counters = {
        "sources": 0,
        "groups": 0,
        "projects": 0,
        "entities": 0,
        "entity_aliases": 0,
        "group_aliases": 0,
        "group_links": 0,
        "links": 0,
        "events": 0,
        "consultancies": 0,
    }
    # Порядок важен: provenance и canonical identity должны существовать до links.
    operations = (
        ("sources", store.upsert_source),
        ("groups", identity.upsert_group),
        ("projects", store.upsert_project),
        ("entities", store.upsert_entity),
        ("entity_aliases", identity.add_entity_alias),
        ("group_aliases", identity.add_group_alias),
        ("group_links", identity.link_group),
        ("links", store.link_entity),
        ("events", store.add_event),
        ("consultancies", store.add_consultancy),
    )
    for key, handler in operations:
        rows = payload.get(key) or []
        if not isinstance(rows, list):
            raise ProductionContextValidationError(f"{key} должен быть массивом")
        for row in rows:
            if not isinstance(row, dict):
                raise ProductionContextValidationError(
                    f"Каждый элемент {key} должен быть JSON-объектом"
                )
            handler(row)
            counters[key] += 1
    return counters


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Production Context registry Vanga: factual temporal/provenance слой"
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=None,
        help="путь к production_context.duckdb (по умолчанию settings.py)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init", help="создать/проверить схему registry и identity")

    import_parser = sub.add_parser("import", help="импортировать воспроизводимый JSON bundle")
    import_parser.add_argument("bundle", type=Path)

    snapshot = sub.add_parser("snapshot", help="показать factual + historical features as-of cutoff")
    snapshot.add_argument("project_id")
    snapshot.add_argument("cutoff", help="ISO datetime, например 2026-01-15T00:00:00Z")

    history = sub.add_parser("history", help="показать только neutral historical aggregates as-of")
    history.add_argument("project_id")
    history.add_argument("cutoff", help="ISO datetime")

    timeline = sub.add_parser("timeline", help="показать известную к cutoff timeline событий")
    timeline.add_argument("project_id")
    timeline.add_argument("cutoff", help="ISO datetime")

    entity = sub.add_parser("resolve-entity", help="разрешить canonical production entity без fuzzy matching")
    entity.add_argument("query")
    entity.add_argument("--kind", default=None)
    entity.add_argument("--cutoff", default=None, help="не использовать aliases, ставшие известными позже cutoff")

    group = sub.add_parser("resolve-group", help="разрешить canonical franchise/shared-universe group")
    group.add_argument("query")
    group.add_argument("--kind", choices=sorted(GROUP_KINDS), default=None)
    group.add_argument("--cutoff", default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    store = ProductionContextStore(args.db)
    identity = ProductionIdentityHistory(store)
    try:
        if args.command == "init":
            _json_dump({"ok": True, "database": str(store.path)})
            return 0
        if args.command == "import":
            counters = _apply_bundle(store, identity, _load_bundle(args.bundle))
            _json_dump({"ok": True, "database": str(store.path), "imported": counters})
            return 0
        if args.command == "snapshot":
            features = store.features_as_of(args.project_id, args.cutoff)
            features.update(identity.history_features_as_of(args.project_id, args.cutoff))
            _json_dump(
                {
                    "ok": True,
                    "project_id": args.project_id,
                    "cutoff": args.cutoff,
                    "features": features,
                }
            )
            return 0
        if args.command == "history":
            _json_dump(
                {
                    "ok": True,
                    "project_id": args.project_id,
                    "cutoff": args.cutoff,
                    "features": identity.history_features_as_of(args.project_id, args.cutoff),
                }
            )
            return 0
        if args.command == "timeline":
            _json_dump(
                {
                    "ok": True,
                    "project_id": args.project_id,
                    "cutoff": args.cutoff,
                    "events": store.timeline_as_of(args.project_id, args.cutoff),
                }
            )
            return 0
        if args.command == "resolve-entity":
            _json_dump(
                {
                    "ok": True,
                    "query": args.query,
                    "resolved": identity.resolve_entity(
                        args.query,
                        kind=args.kind,
                        cutoff=args.cutoff,
                    ),
                }
            )
            return 0
        if args.command == "resolve-group":
            _json_dump(
                {
                    "ok": True,
                    "query": args.query,
                    "resolved": identity.resolve_group(
                        args.query,
                        kind=args.kind,
                        cutoff=args.cutoff,
                    ),
                }
            )
            return 0
        raise ProductionContextValidationError("Неизвестная команда")
    except (ProductionContextValidationError, OSError, json.JSONDecodeError) as exc:
        print(f"Ошибка Production Context: {exc}", file=sys.stderr)
        return 2
    finally:
        store.close()


if __name__ == "__main__":
    raise SystemExit(main())
