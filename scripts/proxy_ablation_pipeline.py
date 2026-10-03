#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from settings import config
from src.proxy_ablation_pipeline import ProxyAblationPipeline
from src.proxy_hypotheses import ProxyHypothesisStore, ProxyHypothesisValidationError


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
    print(f"P6 pipeline результат сохранён: {output}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Канонический P6 pipeline: GenericProxyAblationGate -> result history/status"
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=Path(config.PROXY_HYPOTHESES_DB_PATH),
        help="Путь к proxy_hypotheses.duckdb",
    )
    parser.add_argument("plan", type=Path)
    parser.add_argument("audit", type=Path)
    parser.add_argument("baseline", type=Path)
    parser.add_argument("candidate", type=Path)
    parser.add_argument("--result-id", default=None)
    parser.add_argument("--runner-id", default="generic-proxy-ablation-gate")
    parser.add_argument("--runner-version", default="1")
    parser.add_argument("--output", type=Path, default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    store = None
    try:
        store = ProxyHypothesisStore(args.db)
        result = ProxyAblationPipeline(store).run_and_record(
            plan=_load(args.plan),
            audit_report=_load(args.audit),
            baseline=_load(args.baseline),
            candidate=_load(args.candidate),
            result_id=args.result_id,
            runner_id=args.runner_id,
            runner_version=args.runner_version,
        )
        _write(result, args.output)
        return 0 if result["passed"] else 3
    except (OSError, json.JSONDecodeError, ProxyHypothesisValidationError, ValueError) as exc:
        print(f"Ошибка canonical P6 pipeline: {exc}", file=sys.stderr)
        return 2
    finally:
        if store is not None:
            store.close()


if __name__ == "__main__":
    raise SystemExit(main())
