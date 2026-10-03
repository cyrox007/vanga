#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from settings import config
from src.proxy_ablation_results import ProxyAblationResultStore
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
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(output)
    print(f"Результат proxy ablation сохранён: {output}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="P6: сохранить проверенный proxy ablation result и обновить status hypothesis"
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=Path(config.PROXY_HYPOTHESES_DB_PATH),
        help="Путь к proxy_hypotheses.duckdb",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    record = sub.add_parser(
        "record",
        help="Сохранить GenericProxyAblationGate result и атомарно принять/отклонить hypothesis",
    )
    record.add_argument("plan", type=Path)
    record.add_argument("gate", type=Path)
    record.add_argument("--output", type=Path, default=None)

    latest = sub.add_parser("latest", help="Показать последний сохранённый result hypothesis")
    latest.add_argument("hypothesis_id")
    latest.add_argument("--output", type=Path, default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        with ProxyAblationResultStore(args.db) as store:
            if args.command == "record":
                payload = store.record(_load(args.plan), _load(args.gate))
            else:
                payload = store.latest(args.hypothesis_id)
                if payload is None:
                    print(f"Для hypothesis {args.hypothesis_id} нет сохранённых ablation results", file=sys.stderr)
                    return 3
        _write(payload, args.output)
        return 0
    except (OSError, json.JSONDecodeError, ProxyHypothesisValidationError, ValueError) as exc:
        print(f"Ошибка P6 proxy ablation result store: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
