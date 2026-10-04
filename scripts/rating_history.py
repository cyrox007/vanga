from __future__ import annotations

import argparse
import json

from src.rating_history import RatingHistoryStore


def _ids(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def main() -> int:
    parser = argparse.ArgumentParser(description="P7 append-only IMDb rating history")
    parser.add_argument("--db", help="Путь к rating_history.duckdb")
    sub = parser.add_subparsers(dest="command", required=True)

    watch = sub.add_parser("watch", help="Добавить IMDb id в watchlist")
    watch.add_argument("imdb_ids", help="Список tt... через запятую")
    watch.add_argument("--source", default="manual")
    watch.add_argument("--note")

    unwatch = sub.add_parser("unwatch", help="Отключить дальнейший сбор")
    unwatch.add_argument("imdb_ids")

    capture = sub.add_parser("capture", help="Снять daily snapshot текущего title_ratings")
    capture.add_argument("--imdb-db", required=True)
    capture.add_argument("--observed-at")
    capture.add_argument("--source-fingerprint")

    history = sub.add_parser("history", help="Показать временной ряд фильма")
    history.add_argument("imdb_id")
    history.add_argument("--limit", type=int)

    as_of = sub.add_parser("as-of", help="Рейтинг фильма на заданный cutoff")
    as_of.add_argument("imdb_id")
    as_of.add_argument("cutoff")

    sub.add_parser("watchlist", help="Показать активный watchlist")
    sub.add_parser("status", help="Статус накопленной истории")

    args = parser.parse_args()
    kwargs = {"path": args.db} if args.db else {}
    with RatingHistoryStore(**kwargs) as store:
        if args.command == "watch":
            result = store.watch(_ids(args.imdb_ids), source=args.source, note=args.note)
        elif args.command == "unwatch":
            result = store.unwatch(_ids(args.imdb_ids))
        elif args.command == "capture":
            result = store.capture_daily(
                args.imdb_db,
                observed_at=args.observed_at,
                source_fingerprint_sha256=args.source_fingerprint,
            )
        elif args.command == "history":
            result = store.history(args.imdb_id, limit=args.limit)
        elif args.command == "as-of":
            result = store.rating_as_of(args.imdb_id, args.cutoff)
        elif args.command == "watchlist":
            result = store.watchlist(active_only=True)
        else:
            result = store.status()
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
