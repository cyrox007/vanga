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

from src.actor_persona import ActorPersonaStore, ActorPersonaValidationError


def _dump(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, default=str))


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Actor Persona & Character Graph Vanga")
    parser.add_argument("--db", type=Path, default=None)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init", help="создать/проверить registry")
    imp = sub.add_parser("import", help="импортировать структурированный JSON bundle")
    imp.add_argument("bundle", type=Path)
    snap = sub.add_parser("snapshot", help="persona snapshot актёра на cutoff")
    snap.add_argument("actor_id")
    snap.add_argument("--cutoff", required=True)
    feat = sub.add_parser("features", help="candidate features роли на cutoff")
    feat.add_argument("role_json", type=Path)
    feat.add_argument("--cutoff", required=True)
    ensemble = sub.add_parser("ensemble", help="candidate features ансамбля на cutoff")
    ensemble.add_argument("roles_json", type=Path)
    ensemble.add_argument("--cutoff", required=True)
    sub.add_parser("stats", help="статистика registry")
    return parser


def _import_bundle(store: ActorPersonaStore, payload: dict[str, Any]) -> dict[str, int]:
    counters = {"sources": 0, "characters": 0, "appearances": 0}
    for row in payload.get("sources") or []:
        store.add_source(row); counters["sources"] += 1
    for row in payload.get("characters") or []:
        store.upsert_character(row); counters["characters"] += 1
    for row in payload.get("appearances") or []:
        store.add_role_appearance(row); counters["appearances"] += 1
    return counters


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        with ActorPersonaStore(args.db) as store:
            if args.command == "init":
                _dump({"ok": True, "database": str(store.path), "stats": store.stats()})
            elif args.command == "import":
                payload = _load(args.bundle)
                if not isinstance(payload, dict):
                    raise ActorPersonaValidationError("Корень bundle должен быть объектом")
                _dump({"ok": True, "imported": _import_bundle(store, payload), "stats": store.stats()})
            elif args.command == "snapshot":
                _dump({"ok": True, "persona": store.persona_snapshot_as_of(args.actor_id, args.cutoff)})
            elif args.command == "features":
                payload = _load(args.role_json)
                if not isinstance(payload, dict):
                    raise ActorPersonaValidationError("role_json должен быть объектом")
                _dump({"ok": True, "features": store.candidate_features_as_of(payload, args.cutoff)})
            elif args.command == "ensemble":
                payload = _load(args.roles_json)
                if not isinstance(payload, list) or any(not isinstance(row, dict) for row in payload):
                    raise ActorPersonaValidationError("roles_json должен быть массивом объектов")
                _dump({"ok": True, "features": store.ensemble_features_as_of(payload, args.cutoff)})
            elif args.command == "stats":
                _dump({"ok": True, "stats": store.stats()})
            else:
                raise ActorPersonaValidationError("Неизвестная команда")
        return 0
    except (ActorPersonaValidationError, OSError, json.JSONDecodeError) as exc:
        print(f"Ошибка Actor Persona: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
