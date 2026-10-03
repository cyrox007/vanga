#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from settings import config
from src.proxy_ablation_gate import ProxyAblationResultRegistry
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
    print(f"P6 ablation result сохранён: {output}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "P6 persistence: проверить fingerprint отчёта GenericProxyAblationGate "
            "и сохранить immutable history"
        )
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=Path(config.PROXY_HYPOTHESES_DB_PATH),
        help="proxy_hypotheses.duckdb",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    verify = subparsers.add_parser(
        "verify", help="Проверить result envelope без записи в registry"
    )
    verify.add_argument("result", type=Path)
    verify.add_argument("--output", type=Path, default=None)

    record = subparsers.add_parser(
        "record", help="Проверить и сохранить immutable generic-gate result"
    )
    record.add_argument("result", type=Path)
    record.add_argument("--output", type=Path, default=None)

    history = subparsers.add_parser("history", help="Показать историю gate runs hypothesis")
    history.add_argument("hypothesis_id")
    history.add_argument("--output", type=Path, default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        with ProxyHypothesisStore(args.db) as store:
            registry = ProxyAblationResultRegistry(store)
            if args.command in {"verify", "record"}:
                payload = json.loads(args.result.read_text(encoding="utf-8"))
                report = (
                    registry.prepare(payload)
                    if args.command == "verify"
                    else registry.record(payload)
                )
                _write(report, args.output)
                return 0
            if args.command == "history":
                _write(registry.history(args.hypothesis_id), args.output)
                return 0
            raise ProxyHypothesisValidationError("Неизвестная команда")
    except (OSError, json.JSONDecodeError, ProxyHypothesisValidationError) as exc:
        print(f"Ошибка P6 ablation result registry: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
