#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from settings import config
from src.proxy_ablation import TemporalProxyAvailabilityAuditor
from src.proxy_hypotheses import ProxyHypothesisValidationError
from src.proxy_materializer_audited import AuditedProxySourceMaterializer


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "P6: материализовать preregistered pre-release proxies из IMDb/Source/Production stores"
        )
    )
    parser.add_argument("plan", type=Path, help="export-plan из Proxy Hypothesis Registry")
    parser.add_argument("targets", type=Path, help="JSON с targets[] и pre-release cutoff/release")
    parser.add_argument("--source-db", type=Path, default=Path(config.SOURCE_CONTEXT_DB_PATH))
    parser.add_argument(
        "--production-db",
        type=Path,
        default=Path(config.PRODUCTION_CONTEXT_DB_PATH),
    )
    parser.add_argument("--imdb-db", type=Path, default=Path(config.IMDB_DB_PATH))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--audit-output",
        type=Path,
        default=None,
        help="если указан, сразу выполнить TemporalProxyAvailabilityAuditor",
    )
    return parser


def _load(path: Path, *, label: str):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ProxyHypothesisValidationError(f"Не удалось прочитать {label}: {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ProxyHypothesisValidationError(f"{label} должен быть корректным JSON: {exc}") from exc


def _write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        plan = _load(args.plan, label="plan")
        targets = _load(args.targets, label="targets")
        materializer = AuditedProxySourceMaterializer(
            source_db_path=args.source_db,
            production_db_path=args.production_db,
            imdb_db_path=args.imdb_db,
        )
        materialization = materializer.materialize(plan, targets)
        _write(args.output, materialization)
        print(f"Proxy materialization сохранён: {args.output}")

        if args.audit_output is not None:
            audit = TemporalProxyAvailabilityAuditor.audit(plan, materialization)
            _write(args.audit_output, audit)
            print(f"Temporal proxy audit сохранён: {args.audit_output}")
            if not audit.get("passed"):
                print(
                    "Temporal proxy audit НЕ ПРОЙДЕН; candidate training запускать нельзя",
                    file=sys.stderr,
                )
                return 3
        return 0
    except (ValueError, ProxyHypothesisValidationError) as exc:
        print(f"Ошибка proxy materialization: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
