#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from settings import config
from src.proxy_ablation_gate import ProxyAblationResultGate
from src.proxy_hypotheses import ProxyHypothesisStore, ProxyHypothesisValidationError


def _write(payload: object, output: Path | None) -> None:
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if output is None:
        print(text, end="")
        return
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(output)
    print(f"P6 ablation report сохранён: {output}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="P6 gate: проверить/зафиксировать результат preregistered proxy ablation"
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=Path(config.PROXY_HYPOTHESES_DB_PATH),
        help="proxy_hypotheses.duckdb",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    evaluate = subparsers.add_parser("evaluate", help="Проверить JSON результата ablation")
    evaluate.add_argument("result", type=Path)
    evaluate.add_argument(
        "--record",
        action="store_true",
        help="Зафиксировать verdict в registry и перевести hypothesis в accepted/rejected",
    )
    evaluate.add_argument("--output", type=Path, default=None)

    history = subparsers.add_parser("history", help="Показать историю ablation hypothesis")
    history.add_argument("hypothesis_id")
    history.add_argument("--output", type=Path, default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        with ProxyHypothesisStore(args.db) as store:
            gate = ProxyAblationResultGate(store)
            if args.command == "evaluate":
                payload = json.loads(args.result.read_text(encoding="utf-8"))
                report = gate.evaluate(payload, record=args.record)
                _write(report, args.output)
                return 0 if report["passed"] else 3
            if args.command == "history":
                _write(gate.result_history(args.hypothesis_id), args.output)
                return 0
            raise ProxyHypothesisValidationError("Неизвестная команда")
    except (OSError, json.JSONDecodeError, ProxyHypothesisValidationError) as exc:
        print(f"Ошибка P6 ablation gate: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
