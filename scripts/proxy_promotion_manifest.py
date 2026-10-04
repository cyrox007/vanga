from __future__ import annotations

import argparse
import json
from pathlib import Path

from src.proxy_promotion_manifest import ProxyPromotionManifestRegistry


def _load_json(path: str) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="P6 promotion manifest для validated proxy candidate schema"
    )
    parser.add_argument("--db", required=True, help="Путь к proxy_hypotheses.duckdb")
    sub = parser.add_subparsers(dest="command", required=True)

    freeze = sub.add_parser("freeze", help="Зафиксировать immutable promotion manifest")
    freeze.add_argument("payload", help="JSON с schema_id/target model schema/base feature order")

    show = sub.add_parser("show", help="Показать manifest")
    show.add_argument("promotion_id")

    sub.add_parser("list", help="Показать все promotion manifests")

    args = parser.parse_args()
    with ProxyPromotionManifestRegistry(args.db) as registry:
        if args.command == "freeze":
            result = registry.freeze(_load_json(args.payload))
        elif args.command == "show":
            result = registry.get(args.promotion_id)
        else:
            result = registry.list_manifests()
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
