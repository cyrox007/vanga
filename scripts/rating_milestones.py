from __future__ import annotations

import argparse
import json

from src.rating_history import RatingHistoryStore
from src.rating_milestones import RatingMilestoneExtractor


def main() -> int:
    parser = argparse.ArgumentParser(
        description="P7 milestone targets из point-in-time IMDb rating history"
    )
    parser.add_argument("imdb_id")
    parser.add_argument("release_at")
    parser.add_argument("--as-of")
    parser.add_argument("--db")
    args = parser.parse_args()

    kwargs = {"path": args.db} if args.db else {}
    with RatingHistoryStore(**kwargs) as store:
        result = RatingMilestoneExtractor(store).extract(
            args.imdb_id,
            args.release_at,
            as_of=args.as_of,
        )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
