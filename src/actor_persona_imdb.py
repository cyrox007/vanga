from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import duckdb

from settings import config
from src.actor_persona import ActorPersonaStore


class ActorPersonaImdbError(ValueError):
    pass


def _characters(value: Any) -> list[str]:
    if value in (None, "", "\\N"):
        return []
    if isinstance(value, list):
        rows = value
    else:
        try:
            rows = json.loads(str(value))
        except (TypeError, ValueError, json.JSONDecodeError):
            return []
    result = []
    for item in rows if isinstance(rows, list) else []:
        clean = " ".join(str(item or "").strip().split())
        if clean and clean not in result:
            result.append(clean)
    return result


def materialize_imdb_actor_roles(
    target: ActorPersonaStore,
    *,
    imdb_db_path: str | Path | None = None,
    since_year: int | None = None,
    until_year: int | None = None,
    limit: int | None = None,
    source_id: str = "imdb-principals-baseline",
) -> dict[str, int]:
    """Импортирует выпущенные actor/actress credits из локальной IMDb БД.

    ``known_at`` намеренно равен дате выпуска (1 января startYear), а не текущей
    дате скачивания IMDb. Это консервативно: историческая persona не получает
    сведения о роли раньше выхода фильма. Meta-role и archetype здесь не
    угадываются и остаются для подтверждённых аннотаций.
    """
    db_path = Path(imdb_db_path or config.IMDB_DB_PATH)
    if not db_path.is_file():
        raise ActorPersonaImdbError(f"IMDb DuckDB не найден: {db_path}")
    conn = duckdb.connect(str(db_path), read_only=True)
    try:
        tables = {str(row[0]) for row in conn.execute("SHOW TABLES").fetchall()}
        if not {"title_principals", "title_basics"}.issubset(tables):
            raise ActorPersonaImdbError("Нужны title_principals и title_basics")
        retrieved = datetime.fromtimestamp(db_path.stat().st_mtime, tz=timezone.utc)
        target.add_source(
            {
                "source_id": source_id,
                "provider": "IMDb datasets / local DuckDB",
                "retrieved_at": retrieved.isoformat(),
                "usage_basis": "historical role baseline",
            }
        )
        where = ["p.category IN ('actor','actress')", "b.startYear IS NOT NULL"]
        params: list[Any] = []
        if since_year is not None:
            where.append("CAST(b.startYear AS INTEGER) >= ?")
            params.append(int(since_year))
        if until_year is not None:
            where.append("CAST(b.startYear AS INTEGER) <= ?")
            params.append(int(until_year))
        sql = f"""
            SELECT p.tconst, p.nconst, p.characters, CAST(b.startYear AS INTEGER), b.genres
            FROM title_principals p
            JOIN title_basics b ON b.tconst=p.tconst
            WHERE {' AND '.join(where)}
            ORDER BY CAST(b.startYear AS INTEGER), p.tconst, p.nconst
        """
        if limit is not None:
            sql += " LIMIT ?"
            params.append(int(limit))
        rows = conn.execute(sql, params).fetchall()
        appearances = 0
        characters_created = 0
        skipped_without_character = 0
        for work_id, actor_id, raw_characters, year, raw_genres in rows:
            names = _characters(raw_characters)
            if not names:
                skipped_without_character += 1
                continue
            genres = [] if raw_genres in (None, "", "\\N") else [x.strip() for x in str(raw_genres).split(",") if x.strip()]
            release_at = f"{int(year):04d}-01-01T00:00:00Z"
            for index, name in enumerate(names):
                character_id = f"imdb-character:{work_id}:{actor_id}:{index}"
                target.upsert_character(
                    {
                        "character_id": character_id,
                        "canonical_name": name,
                        "external_ids": {"imdb_work": str(work_id), "imdb_actor": str(actor_id)},
                    }
                )
                characters_created += 1
                target.add_role_appearance(
                    {
                        "appearance_id": f"imdb-role:{work_id}:{actor_id}:{index}",
                        "actor_id": str(actor_id),
                        "work_id": str(work_id),
                        "character_id": character_id,
                        "character_name": name,
                        "work_release_at": release_at,
                        "known_at": release_at,
                        "role_function": "unknown",
                        "meta_role_type": "ordinary",
                        "genres": genres,
                        "archetypes": [],
                        "source_id": source_id,
                        "confidence": 0.9,
                    }
                )
                appearances += 1
        return {
            "rows_read": len(rows),
            "characters_created": characters_created,
            "appearances_created": appearances,
            "skipped_without_character": skipped_without_character,
        }
    finally:
        conn.close()
