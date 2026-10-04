from __future__ import annotations

import argparse
import json
from pathlib import Path

from src.rating_dataset import RatingMilestoneDatasetBuilder
from src.rating_history import RatingHistoryStore


def _load(path: str):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description="P7 dataset-level milestone readiness")
    parser.add_argument("--db", help="Путь к rating_history.duckdb")
    sub = parser.add_subparsers(dest="command", required=True)

    report = sub.add_parser("report", help="Построить coverage/readiness report")
    report.add_argument("cases_json")
    report.add_argument("--as-of", required=True)
    report.add_argument("--min-ready-rows", type=int)
    report.add_argument("--min-due-coverage", type=float)
    report.add_argument("--output")

    export = sub.add_parser("export", help="Экспортировать только usable target rows")
    export.add_argument("report_json")
    export.add_argument("milestone")
    export.add_argument("--output")

    args = parser.parse_args()
    kwargs = {"path": args.db} if args.db else {}
    with RatingHistoryStore(**kwargs) as store:
        builder = RatingMilestoneDatasetBuilder(store)
        if args.command == "report":
            payload = _load(args.cases_json)
            cases = payload.get("cases") if isinstance(payload, dict) else payload
            result = builder.build(
                cases,
                as_of=args.as_of,
                min_ready_rows=args.min_ready_rows,
                min_due_coverage=args.min_due_coverage,
            )
        else:
            result = builder.export_target_rows(_load(args.report_json), args.milestone)

    text = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True)
    output = getattr(args, "output", None)
    if output:
        Path(output).write_text(text + "\n", encoding="utf-8")
        print(f"Результат записан: {output}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
