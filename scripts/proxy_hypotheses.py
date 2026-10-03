#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from settings import config
from src.proxy_hypotheses import (
    ProxyHypothesisStore,
    ProxyHypothesisValidationError,
)


def _write(payload: dict, output: Path | None = None) -> None:
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if output is None:
        print(text, end="")
        return
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(output)
    print(f"Proxy hypothesis результат сохранён: {output}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="P6 registry: retrospective evidence -> pre-release proxy -> temporal ablation"
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=Path(config.PROXY_HYPOTHESES_DB_PATH),
        help="Путь к proxy_hypotheses.duckdb",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init", help="Создать/проверить схему registry")
    init.add_argument("--output", type=Path, default=None)

    bundle = sub.add_parser(
        "import-bundle",
        help="Импортировать одну hypothesis вместе с evidence/proxies/ablation spec",
    )
    bundle.add_argument("bundle", type=Path)
    bundle.add_argument("--mark-ready", action="store_true")
    bundle.add_argument("--output", type=Path, default=None)

    validate = sub.add_parser("validate", help="Проверить готовность к temporal ablation")
    validate.add_argument("hypothesis_id")
    validate.add_argument("--output", type=Path, default=None)

    ready = sub.add_parser("mark-ready", help="Перевести валидную hypothesis в ablation_ready")
    ready.add_argument("hypothesis_id")
    ready.add_argument("--output", type=Path, default=None)

    export = sub.add_parser("export-plan", help="Экспортировать preregistered ablation plan")
    export.add_argument("hypothesis_id")
    export.add_argument("--output", type=Path, default=None)
    return parser


def _import_bundle(store: ProxyHypothesisStore, payload: dict, *, mark_ready: bool) -> dict:
    if not isinstance(payload, dict):
        raise ProxyHypothesisValidationError("Bundle должен быть JSON-объектом")
    hypothesis_payload = payload.get("hypothesis")
    if not isinstance(hypothesis_payload, dict):
        raise ProxyHypothesisValidationError("Bundle.hypothesis должен быть JSON-объектом")
    hypothesis_id = store.upsert_hypothesis(hypothesis_payload)

    for evidence in payload.get("evidence") or []:
        if not isinstance(evidence, dict):
            raise ProxyHypothesisValidationError("Bundle.evidence[] должен содержать JSON-объекты")
        item = dict(evidence)
        item.setdefault("hypothesis_id", hypothesis_id)
        store.add_evidence(item)

    for proxy in payload.get("proxy_features") or []:
        if not isinstance(proxy, dict):
            raise ProxyHypothesisValidationError(
                "Bundle.proxy_features[] должен содержать JSON-объекты"
            )
        item = dict(proxy)
        item.setdefault("hypothesis_id", hypothesis_id)
        store.add_proxy_feature(item)

    ablation = payload.get("ablation")
    if ablation is not None:
        if not isinstance(ablation, dict):
            raise ProxyHypothesisValidationError("Bundle.ablation должен быть JSON-объектом")
        item = dict(ablation)
        item.setdefault("hypothesis_id", hypothesis_id)
        store.set_ablation_spec(item)

    if mark_ready:
        report = store.mark_ablation_ready(hypothesis_id)
    else:
        report = store.validation_report(hypothesis_id)
    return {
        "hypothesis_id": hypothesis_id,
        "validation": report,
    }


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    store = None
    try:
        store = ProxyHypothesisStore(args.db)
        if args.command == "init":
            payload = {
                "registry_version": 1,
                "db": str(args.db),
                "status": "ready",
            }
        elif args.command == "import-bundle":
            bundle = json.loads(args.bundle.read_text(encoding="utf-8"))
            payload = _import_bundle(store, bundle, mark_ready=args.mark_ready)
        elif args.command == "validate":
            payload = store.validation_report(args.hypothesis_id)
        elif args.command == "mark-ready":
            payload = store.mark_ablation_ready(args.hypothesis_id)
        else:
            payload = store.export_ablation_plan(args.hypothesis_id)
        _write(payload, args.output)
        return 0
    except (OSError, json.JSONDecodeError, ValueError, ProxyHypothesisValidationError) as exc:
        print(f"Ошибка Proxy Hypothesis Registry: {exc}", file=sys.stderr)
        return 2
    finally:
        if store is not None:
            store.close()


if __name__ == "__main__":
    raise SystemExit(main())
