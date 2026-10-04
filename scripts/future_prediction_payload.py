from __future__ import annotations

import argparse
import json

from src.future_prediction_payload import FuturePredictionPayloadBuilder


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Собрать cache-only `/predict` payload для P9 future release"
    )
    parser.add_argument("project_id")
    parser.add_argument("--cutoff", required=True)
    parser.add_argument("--territory", default="worldwide")
    parser.add_argument("--runtime", type=int)
    parser.add_argument("--genre", action="append", dest="genres")
    parser.add_argument("--synopsis")
    parser.add_argument("--future-db")
    parser.add_argument("--imdb-db")
    parser.add_argument("--source-db")
    parser.add_argument("--production-db")
    args = parser.parse_args()

    kwargs = {}
    for key, value in (
        ("future_db_path", args.future_db),
        ("imdb_db_path", args.imdb_db),
        ("source_db_path", args.source_db),
        ("production_db_path", args.production_db),
    ):
        if value:
            kwargs[key] = value

    builder = FuturePredictionPayloadBuilder(**kwargs)
    result = builder.build(
        args.project_id,
        args.cutoff,
        territory=args.territory,
        runtime_override=args.runtime,
        genres_override=args.genres,
        synopsis=args.synopsis,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result["prediction_ready"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
