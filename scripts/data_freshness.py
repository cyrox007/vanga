#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from settings import config
from src.data_freshness import (
    FreshnessPolicy,
    build_freshness_report,
    write_freshness_manifest,
)


def _parse_as_of(value: str | None) -> datetime | None:
    if not value:
        return None
    text = value.strip().replace("Z", "+00:00")
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Проверка свежести локального IMDb snapshot Vanga"
    )
    parser.add_argument("command", choices=("report", "check"))
    parser.add_argument("--db", type=Path, default=Path(config.IMDB_DB_PATH))
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path(config.ABSPATH) / "data" / "imdb",
    )
    parser.add_argument("--as-of", default=None, help="ISO datetime для воспроизводимого отчёта")
    parser.add_argument("--max-source-age-days", type=int, default=14)
    parser.add_argument("--high-vote-threshold", type=int, default=1000)
    parser.add_argument("--recent-year-span", type=int, default=3)
    parser.add_argument(
        "--write-manifest",
        type=Path,
        default=None,
        help="сохранить JSON manifest/fingerprint",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.max_source_age_days < 1:
        raise SystemExit("--max-source-age-days должен быть >= 1")
    if args.high_vote_threshold < 1:
        raise SystemExit("--high-vote-threshold должен быть >= 1")
    if args.recent_year_span < 2:
        raise SystemExit("--recent-year-span должен быть >= 2")

    policy = FreshnessPolicy(
        source_max_age_days=args.max_source_age_days,
        high_vote_threshold=args.high_vote_threshold,
        recent_year_span=args.recent_year_span,
    )
    try:
        report = build_freshness_report(
            args.db,
            data_dir=args.data_dir,
            as_of=_parse_as_of(args.as_of),
            policy=policy,
        )
    except (OSError, ValueError) as exc:
        print(f"Ошибка Data Freshness: {exc}", file=sys.stderr)
        return 2

    if args.write_manifest is not None:
        write_freshness_manifest(report, args.write_manifest)

    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    if args.command == "check" and not report.get("ready_for_full_training"):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
