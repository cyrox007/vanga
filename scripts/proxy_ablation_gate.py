#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from settings import config
from src.proxy_ablation_gate import ProxyAblationResultGate
from src.proxy_hypotheses import ProxyHypothesisStore, ProxyHypothesisValidationError


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Legacy P6 CLI: только чтение history. Для новых ablation используйте "
            "scripts/proxy_ablation_pipeline.py"
        )
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=Path(config.PROXY_HYPOTHESES_DB_PATH),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    history = subparsers.add_parser("history", help="Показать историю P6 result artifacts")
    history.add_argument("hypothesis_id")
    history.add_argument("--output", type=Path, default=None)

    deprecated = subparsers.add_parser(
        "evaluate",
        help="Отключено: используйте canonical proxy_ablation_pipeline.py",
    )
    deprecated.add_argument("result", nargs="?", default=None)
    return parser


def _write(payload: object, output: Path | None) -> None:
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if output is None:
        print(text, end="")
        return
    output.parent.mkdir(parents=True, exist_ok=True)
    temp = output.with_suffix(output.suffix + ".tmp")
    temp.write_text(text, encoding="utf-8")
    temp.replace(output)
    print(f"P6 history сохранена: {output}")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "evaluate":
        print(
            "Legacy evaluate отключён. Используйте scripts/proxy_ablation_pipeline.py: "
            "он всегда выполняет temporal audit + GenericProxyAblationGate.",
            file=sys.stderr,
        )
        return 2
    try:
        with ProxyHypothesisStore(args.db) as store:
            compat = ProxyAblationResultGate(store)
            _write(compat.result_history(args.hypothesis_id), args.output)
            return 0
    except (OSError, ProxyHypothesisValidationError) as exc:
        print(f"Ошибка P6 history: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
