from __future__ import annotations

import argparse
import json
from pathlib import Path

from src.proxy_ablation import TemporalProxyAvailabilityAuditor
from src.proxy_unified_materializer import ProxyUnifiedMaterializer


def _load(path: str):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="P6 unified materializer: IMDb/Source/Production/P8 Audience"
    )
    parser.add_argument("plan_json")
    parser.add_argument("targets_json")
    parser.add_argument("--source-db")
    parser.add_argument("--production-db")
    parser.add_argument("--imdb-db")
    parser.add_argument("--audience-db")
    parser.add_argument("--audit", action="store_true")
    parser.add_argument("--output")
    args = parser.parse_args()

    plan = _load(args.plan_json)
    targets = _load(args.targets_json)
    materializer = ProxyUnifiedMaterializer(
        source_db_path=args.source_db,
        production_db_path=args.production_db,
        imdb_db_path=args.imdb_db,
        audience_db_path=args.audience_db,
    )
    payload = materializer.materialize(plan, targets)
    result = {
        "materialization": payload,
        "audit": TemporalProxyAvailabilityAuditor.audit(plan, payload)
        if args.audit
        else None,
    }
    text = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True)
    if args.output:
        Path(args.output).write_text(text + "\n", encoding="utf-8")
        print(f"Результат записан: {args.output}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
