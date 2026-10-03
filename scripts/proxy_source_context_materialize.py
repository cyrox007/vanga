#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from settings import config
from src.proxy_hypotheses import ProxyHypothesisValidationError
from src.proxy_source_context_materializer import ProxySourceContextMaterializer
from src.source_context import SourceContextStore, SourceContextValidationError


def _load_object(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ProxyHypothesisValidationError(f"{path}: ожидается JSON-объект")
    return payload


def _load_targets(path: Path) -> list[dict]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict):
        payload = payload.get("targets")
    if not isinstance(payload, list) or not payload:
        raise ProxyHypothesisValidationError(
            f"{path}: ожидается непустой массив targets или {{\"targets\": [...]}}"
        )
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
    print(f"Source Context materialization сохранён: {output}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="P6: materialize source_context proxies в temporal-proof контракт"
    )
    parser.add_argument("plan", type=Path, help="Preregistered P6 ablation plan JSON")
    parser.add_argument("targets", type=Path, help="Target manifest JSON")
    parser.add_argument(
        "--source-db",
        type=Path,
        default=Path(config.SOURCE_CONTEXT_DB_PATH),
        help="Путь к source_context.duckdb",
    )
    parser.add_argument(
        "--complexity-method",
        default=None,
        help="Точный method Source Complexity; обязателен для complexity features",
    )
    parser.add_argument(
        "--complexity-version",
        default=None,
        help="Точный method_version Source Complexity; обязателен для complexity features",
    )
    parser.add_argument("--output", type=Path, default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    store = None
    try:
        plan = _load_object(args.plan)
        targets = _load_targets(args.targets)
        store = SourceContextStore(args.source_db)
        payload = ProxySourceContextMaterializer(store).materialize(
            plan,
            targets,
            complexity_method=args.complexity_method,
            complexity_method_version=args.complexity_version,
        )
        _write(payload, args.output)
        return 0
    except (
        OSError,
        json.JSONDecodeError,
        ProxyHypothesisValidationError,
        SourceContextValidationError,
        ValueError,
    ) as exc:
        print(f"Ошибка Source Context proxy materializer: {exc}", file=sys.stderr)
        return 2
    finally:
        if store is not None:
            store.close()


if __name__ == "__main__":
    raise SystemExit(main())
