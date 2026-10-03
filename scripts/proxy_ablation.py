#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from src.proxy_ablation import GenericProxyAblationGate, TemporalProxyAvailabilityAuditor
from src.proxy_hypotheses import ProxyHypothesisValidationError


def _read_json(path: Path) -> dict:
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
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(output)
    print(f"P6 proxy ablation результат сохранён: {output}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "P6: проверка temporal availability materialized proxies и "
            "generic gate baseline/candidate ablation"
        )
    )
    sub = parser.add_subparsers(dest="command", required=True)

    audit = sub.add_parser(
        "audit",
        help="Проверить конкретную materialization на post-release leakage",
    )
    audit.add_argument("plan", type=Path, help="preregistered plan из proxy_hypotheses export-plan")
    audit.add_argument("materialization", type=Path)
    audit.add_argument("--output", type=Path, default=None)

    gate = sub.add_parser(
        "gate",
        help="Сравнить результаты baseline/candidate после успешного temporal audit",
    )
    gate.add_argument("plan", type=Path)
    gate.add_argument("audit_report", type=Path)
    gate.add_argument("baseline_result", type=Path)
    gate.add_argument("candidate_result", type=Path)
    gate.add_argument("--output", type=Path, default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "audit":
            report = TemporalProxyAvailabilityAuditor.audit(
                _read_json(args.plan),
                _read_json(args.materialization),
            )
            _write(report, args.output)
            if not report["passed"]:
                print(
                    f"Temporal proxy audit не пройден: violations={report['violation_count']}",
                    file=sys.stderr,
                )
                return 2
            print("Temporal proxy audit пройден.")
            return 0

        report = GenericProxyAblationGate.compare(
            _read_json(args.plan),
            _read_json(args.audit_report),
            _read_json(args.baseline_result),
            _read_json(args.candidate_result),
        )
        _write(report, args.output)
        if not report["comparison"]["passed"]:
            print(
                "Proxy ablation gate не пройден: "
                + str(report["comparison"]["reason"]),
                file=sys.stderr,
            )
            return 3
        print("Proxy ablation gate пройден.")
        return 0
    except (OSError, json.JSONDecodeError, ValueError, ProxyHypothesisValidationError) as exc:
        print(f"Ошибка P6 proxy ablation: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
