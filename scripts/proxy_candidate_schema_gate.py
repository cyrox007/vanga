#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from settings import config
from src.proxy_candidate_schema import ProxyCandidateSchemaRegistry
from src.proxy_candidate_schema_gate import ProxyCandidateSchemaGate
from src.proxy_hypotheses import ProxyHypothesisValidationError


def _load(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ProxyHypothesisValidationError(f"{path}: ожидается JSON-объект")
    return payload


def _write(payload: dict, output: Path | None) -> None:
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if output is None:
        print(text, end="")
        return
    output.parent.mkdir(parents=True, exist_ok=True)
    tmp = output.with_suffix(output.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(output)
    print(f"Candidate schema gate результат сохранён: {output}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="P6 combined ablation gate для frozen candidate schema"
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=Path(config.PROXY_HYPOTHESES_DB_PATH),
        help="Путь к proxy_hypotheses.duckdb",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    evaluate = sub.add_parser("evaluate", help="Проверить и сохранить combined schema ablation")
    evaluate.add_argument("schema_id")
    evaluate.add_argument("plan", type=Path)
    evaluate.add_argument("audit", type=Path)
    evaluate.add_argument("baseline", type=Path)
    evaluate.add_argument("candidate", type=Path)
    evaluate.add_argument("--result-id", default=None)
    evaluate.add_argument("--output", type=Path, default=None)

    result = sub.add_parser("result", help="Показать сохранённый combined result")
    result.add_argument("schema_id")
    result.add_argument("--output", type=Path, default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    registry = None
    try:
        registry = ProxyCandidateSchemaRegistry(args.db)
        gate = ProxyCandidateSchemaGate(registry)
        if args.command == "result":
            payload = gate.result(args.schema_id)
            if payload is None:
                print(f"Для candidate schema {args.schema_id} combined result отсутствует", file=sys.stderr)
                return 4
        else:
            payload = gate.evaluate_and_record(
                schema_id=args.schema_id,
                plan=_load(args.plan),
                audit_report=_load(args.audit),
                baseline=_load(args.baseline),
                candidate=_load(args.candidate),
                result_id=args.result_id,
            )
        _write(payload, args.output)
        if args.command == "evaluate" and not payload["passed"]:
            return 3
        return 0
    except (OSError, json.JSONDecodeError, ProxyHypothesisValidationError, ValueError) as exc:
        print(f"Ошибка candidate schema gate: {exc}", file=sys.stderr)
        return 2
    finally:
        if registry is not None:
            registry.close()


if __name__ == "__main__":
    raise SystemExit(main())
