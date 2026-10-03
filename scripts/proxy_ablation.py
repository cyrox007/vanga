#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from src.proxy_ablation import (
    ProxyAblationEvaluator,
    ProxyAblationValidationError,
    TemporalAvailabilityAuditor,
)


def _load(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ProxyAblationValidationError(f"{path}: ожидается JSON-объект")
    return payload


def _write(payload: dict, output: Path | None) -> None:
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if output is None:
        print(text, end="")
        return
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(output)
    print(f"Результат P6 сохранён: {output}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="P6 temporal availability audit и deterministic ablation evaluation"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    audit = sub.add_parser("audit", help="Проверить temporal proof materialized candidate features")
    audit.add_argument("plan", type=Path)
    audit.add_argument("materialized", type=Path)
    audit.add_argument("--output", type=Path, default=None)

    evaluate = sub.add_parser(
        "evaluate",
        help="Сравнить baseline/candidate на одном dataset fingerprint и holdout",
    )
    evaluate.add_argument("plan", type=Path)
    evaluate.add_argument("audit_report", type=Path)
    evaluate.add_argument("baseline", type=Path)
    evaluate.add_argument("candidate", type=Path)
    evaluate.add_argument("--output", type=Path, default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        plan = _load(args.plan)
        if args.command == "audit":
            payload = TemporalAvailabilityAuditor().audit(plan, _load(args.materialized))
            _write(payload, args.output)
            return 0 if payload["passed"] else 2

        payload = ProxyAblationEvaluator().evaluate(
            plan,
            _load(args.audit_report),
            _load(args.baseline),
            _load(args.candidate),
        )
        _write(payload, args.output)
        return 0 if payload["passed"] else 3
    except (OSError, json.JSONDecodeError, ProxyAblationValidationError, ValueError) as exc:
        print(f"Ошибка P6 proxy ablation: {exc}", file=sys.stderr)
        return 4


if __name__ == "__main__":
    raise SystemExit(main())
