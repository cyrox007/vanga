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
    parser.add_argument("--batch-size", type=int, default=5000, help="Размер пакета чтения IMDb, по умолчанию 5000")
    args = parser.parse_args(argv)

    print("Actor Persona: начинаю материализацию IMDb credits...", flush=True)
    if args.imdb_db:
        print(f"IMDb DB: {args.imdb_db}", flush=True)
    if args.limit is not None:
        print(f"Лимит строк: {args.limit}", flush=True)

    def report_progress(stats: dict[str, int]) -> None:
        print(
            "Actor Persona: "
            f"прочитано={stats['rows_read']}, "
            f"создано_ролей={stats['appearances_created']}, "
            f"уже_были={stats['skipped_existing']}, "
            f"без_персонажа={stats['skipped_without_character']}",
            flush=True,
        )

    try:
        with ActorPersonaStore(args.db) as store:
            result = materialize_imdb_actor_roles(
                store,
                imdb_db_path=args.imdb_db,
                since_year=args.since_year,
                until_year=args.until_year,
                limit=args.limit,
                batch_size=args.batch_size,
                progress_callback=report_progress,
            )
            print("Actor Persona: материализация завершена.", flush=True)
            print(json.dumps({"ok": True, "materialized": result, "stats": store.stats()}, ensure_ascii=False, indent=2))
        return 0
    except KeyboardInterrupt:
        print("Actor Persona: остановлено пользователем; уже записанные данные сохранены и будут пропущены при повторном запуске.", file=sys.stderr, flush=True)
        return 130
    except ActorPersonaImdbError as exc:
        print(f"Ошибка materializer Actor Persona: {exc}", file=sys.stderr, flush=True)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
