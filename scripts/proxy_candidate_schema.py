#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from settings import config
from src.proxy_candidate_schema import ProxyCandidateSchemaRegistry
from src.proxy_candidate_schema_plan import ProxyCandidateSchemaPlanBuilder
from src.proxy_hypotheses import ProxyHypothesisValidationError


def _write(payload, output: Path | None = None) -> None:
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if output is None:
        print(text, end="")
        return
    output.parent.mkdir(parents=True, exist_ok=True)
    tmp = output.with_suffix(output.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(output)
    print(f"Candidate schema результат сохранён: {output}")


def _load(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ProxyHypothesisValidationError(f"{path}: ожидается JSON-объект")
    return payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="P6: freeze accepted proxy hypotheses в versioned research candidate schema"
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=Path(config.PROXY_HYPOTHESES_DB_PATH),
        help="Путь к proxy_hypotheses.duckdb",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    freeze = sub.add_parser("freeze", help="Зафиксировать immutable candidate schema")
    freeze.add_argument("payload", type=Path)
    freeze.add_argument("--output", type=Path, default=None)

    show = sub.add_parser("show", help="Показать frozen schema")
    show.add_argument("schema_id")
    show.add_argument("--output", type=Path, default=None)

    listing = sub.add_parser("list", help="Показать candidate schemas")
    listing.add_argument("--output", type=Path, default=None)

    plan = sub.add_parser(
        "export-plan",
        help="Экспортировать combined preregistered plan для #76 materializers/#70 gate",
    )
    plan.add_argument("schema_id")
    plan.add_argument("--allow-regression", type=float, default=None)
    plan.add_argument("--output", type=Path, default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    registry = None
    try:
        registry = ProxyCandidateSchemaRegistry(args.db)
        if args.command == "freeze":
            payload = registry.freeze(_load(args.payload))
        elif args.command == "show":
            payload = registry.get(args.schema_id)
        elif args.command == "list":
            payload = {"schemas": registry.list_schemas()}
        else:
            builder = ProxyCandidateSchemaPlanBuilder(registry)
            if args.allow_regression is None:
                payload = builder.build(args.schema_id)
            else:
                payload = builder.build(
                    args.schema_id,
                    require_improvement=False,
                    max_mae_regression=args.allow_regression,
                )
        _write(payload, args.output)
        return 0
    except (OSError, json.JSONDecodeError, ProxyHypothesisValidationError, ValueError) as exc:
        print(f"Ошибка P6 candidate schema: {exc}", file=sys.stderr)
        return 2
    finally:
        if registry is not None:
            registry.close()


if __name__ == "__main__":
    raise SystemExit(main())
