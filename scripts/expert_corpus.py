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

from src.expert_corpus import ExpertCorpusStore, ExpertCorpusValidationError


def _dump(payload: Any) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))


def _load(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ExpertCorpusValidationError("Корень bundle должен быть JSON-объектом")
    return payload


def _apply_bundle(store: ExpertCorpusStore, payload: dict[str, Any]) -> dict[str, int]:
    counters = {
        "profiles": 0,
        "cases": 0,
        "materials": 0,
        "case_materials": 0,
        "claims": 0,
        "evidence": 0,
    }
    operations = (
        ("profiles", store.upsert_profile),
        ("cases", store.upsert_case),
        ("materials", store.upsert_material),
        ("case_materials", store.link_case_material),
        ("claims", store.upsert_claim),
        ("evidence", store.upsert_evidence),
    )
    for key, handler in operations:
        rows = payload.get(key) or []
        if not isinstance(rows, list):
            raise ExpertCorpusValidationError(f"{key} должен быть массивом")
        for row in rows:
            if not isinstance(row, dict):
                raise ExpertCorpusValidationError(
                    f"Каждый элемент {key} должен быть JSON-объектом"
                )
            handler(row)
            counters[key] += 1
    return counters


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="P5: структурированный Expert Analysis Corpus Vanga"
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=None,
        help="путь к expert_corpus.duckdb (по умолчанию settings.py)",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init", help="создать/проверить schema corpus")

    import_parser = sub.add_parser(
        "import",
        help="импортировать JSON bundle без полных текстов/транскриптов",
    )
    import_parser.add_argument("bundle", type=Path)

    sub.add_parser("stats", help="показать размер корпуса и splits")

    case = sub.add_parser("case", help="показать summary аннотаций одного case")
    case.add_argument("case_id")

    claim = sub.add_parser(
        "claim",
        help="показать полную структурированную claim-chain с evidence",
    )
    claim.add_argument("claim_id")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    store = ExpertCorpusStore(args.db)
    try:
        if args.command == "init":
            _dump({"ok": True, "database": str(store.path)})
            return 0
        if args.command == "import":
            counters = _apply_bundle(store, _load(args.bundle))
            _dump(
                {
                    "ok": True,
                    "database": str(store.path),
                    "imported": counters,
                    "stats": store.corpus_stats(),
                }
            )
            return 0
        if args.command == "stats":
            _dump({"ok": True, "stats": store.corpus_stats()})
            return 0
        if args.command == "case":
            _dump({"ok": True, "case": store.case_summary(args.case_id)})
            return 0
        if args.command == "claim":
            _dump({"ok": True, "claim": store.claim_chain(args.claim_id)})
            return 0
        raise ExpertCorpusValidationError("Неизвестная команда Expert Corpus")
    except (ExpertCorpusValidationError, OSError, json.JSONDecodeError) as exc:
        print(f"Ошибка Expert Analysis Corpus: {exc}", file=sys.stderr)
        return 2
    finally:
        store.close()


if __name__ == "__main__":
    raise SystemExit(main())
