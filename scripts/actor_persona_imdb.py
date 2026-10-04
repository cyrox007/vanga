#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.actor_persona import ActorPersonaStore
from src.actor_persona_imdb import ActorPersonaImdbError, materialize_imdb_actor_roles


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Материализация Actor Persona graph из локальной IMDb DuckDB")
    parser.add_argument("--db", type=Path, default=None, help="actor_persona.duckdb")
    parser.add_argument("--imdb-db", type=Path, default=None)
    parser.add_argument("--since-year", type=int)
    parser.add_argument("--until-year", type=int)
    parser.add_argument("--limit", type=int)
    args = parser.parse_args(argv)
    try:
        with ActorPersonaStore(args.db) as store:
            result = materialize_imdb_actor_roles(
                store,
                imdb_db_path=args.imdb_db,
                since_year=args.since_year,
                until_year=args.until_year,
                limit=args.limit,
            )
            print(json.dumps({"ok": True, "materialized": result, "stats": store.stats()}, ensure_ascii=False, indent=2))
        return 0
    except ActorPersonaImdbError as exc:
        print(f"Ошибка materializer Actor Persona: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
