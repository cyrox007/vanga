from __future__ import annotations

import argparse
import json

from settings import config
from src.future_releases import FutureReleaseStore
from src.future_temporal_facts import FutureTemporalFactStore


def _value(args: argparse.Namespace):
    if args.fact_type == "runtime_minutes":
        return args.runtime
    if args.fact_type == "genres":
        return args.genre
    return args.synopsis


def main() -> int:
    parser = argparse.ArgumentParser(
        description="P9 temporal facts: датированные runtime/genres/synopsis будущего фильма"
    )
    parser.add_argument("--db", default=config.FUTURE_RELEASE_DB_PATH)
    sub = parser.add_subparsers(dest="command", required=True)

    add = sub.add_parser("add", help="Добавить датированный pre-release факт")
    add.add_argument("project_id")
    add.add_argument("fact_type", choices=["runtime_minutes", "genres", "synopsis"])
    add.add_argument("--known-at", required=True)
    add.add_argument("--source-id", required=True)
    add.add_argument("--provider", required=True)
    add.add_argument("--url")
    add.add_argument(
        "--usage-basis",
        default="public_record",
        choices=["public_aggregate", "licensed", "first_party", "manual_reference", "public_record"],
    )
    add.add_argument("--confidence", type=float, default=1.0)
    add.add_argument("--runtime", type=int)
    add.add_argument("--genre", action="append", default=[])
    add.add_argument("--synopsis")
    add.add_argument("--observation-id")

    show = sub.add_parser("show", help="Показать temporal snapshot на cutoff")
    show.add_argument("project_id")
    show.add_argument("--cutoff", required=True)

    args = parser.parse_args()
    if args.command == "show":
        with FutureTemporalFactStore(args.db) as store:
            payload = store.snapshot_as_of(args.project_id, args.cutoff)
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return 0

    value = _value(args)
    if value in {None, []}:
        parser.error(
            "Для runtime_minutes нужен --runtime, для genres хотя бы один --genre, "
            "для synopsis --synopsis"
        )
    with FutureReleaseStore(args.db) as registry:
        registry.upsert_source(
            {
                "source_id": args.source_id,
                "provider": args.provider,
                "url": args.url,
                "usage_basis": args.usage_basis,
                "retrieved_at": args.known_at,
            }
        )
    with FutureTemporalFactStore(args.db) as store:
        observation_id = store.add_fact(
            {
                "observation_id": args.observation_id,
                "project_id": args.project_id,
                "fact_type": args.fact_type,
                "value": value,
                "known_at": args.known_at,
                "source_id": args.source_id,
                "confidence": args.confidence,
            }
        )
    print(f"Temporal fact сохранён: {observation_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())