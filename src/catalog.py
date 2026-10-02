from __future__ import annotations

from dataclasses import dataclass

import duckdb

from src.input_aliases import RussianInputResolver


@dataclass(frozen=True)
class MovieSearchItem:
    imdb_id: str
    title: str
    original_title: str | None
    year: int | None
    runtime: int | None
    genres: list[str]
    director: str | None
    actors: list[str]

    def to_dict(self) -> dict:
        return {
            "imdb_id": self.imdb_id,
            "title": self.title,
            "original_title": self.original_title,
            "year": self.year,
            "runtime": self.runtime,
            "genres": self.genres,
            "director": self.director,
            "actors": self.actors,
        }


class CatalogSearch:
    """Быстрый локальный поиск по IMDb для публичного интерфейса Vanga."""

    def __init__(
        self,
        conn: duckdb.DuckDBPyConnection,
        resolver: RussianInputResolver,
    ) -> None:
        self.conn = conn
        self.resolver = resolver

    @staticmethod
    def _clean_query(query: str) -> str:
        return " ".join((query or "").strip().split())

    def _movie_details(self, imdb_id: str) -> MovieSearchItem | None:
        row = self.conn.execute(
            """
            SELECT
                tconst,
                primaryTitle,
                NULLIF(originalTitle, ''),
                TRY_CAST(startYear AS INTEGER),
                TRY_CAST(runtimeMinutes AS INTEGER),
                genres
            FROM title_basics
            WHERE tconst = ?
              AND titleType = 'movie'
            LIMIT 1
            """,
            [imdb_id],
        ).fetchone()
        if not row:
            return None

        people = self.conn.execute(
            """
            SELECT p.category, n.primaryName
            FROM title_principals p
            JOIN name_basics n ON n.nconst = p.nconst
            WHERE p.tconst = ?
              AND p.category IN ('director', 'actor', 'actress')
            ORDER BY
                CASE WHEN p.category = 'director' THEN 0 ELSE 1 END,
                TRY_CAST(p.ordering AS INTEGER) NULLS LAST,
                n.primaryName
            """,
            [imdb_id],
        ).fetchall()

        director = next(
            (str(name) for category, name in people if category == "director" and name),
            None,
        )
        actors = [
            str(name)
            for category, name in people
            if category in {"actor", "actress"} and name
        ][:5]

        genres = [
            item.strip()
            for item in str(row[5] or "").split(",")
            if item and item.strip() and item.strip() != "\\N"
        ]

        return MovieSearchItem(
            imdb_id=str(row[0]),
            title=str(row[1] or ""),
            original_title=str(row[2]) if row[2] else None,
            year=int(row[3]) if row[3] is not None else None,
            runtime=int(row[4]) if row[4] is not None else None,
            genres=genres,
            director=director,
            actors=actors,
        )

    def search_movies(
        self,
        query: str,
        *,
        limit: int = 8,
        year: int | None = None,
    ) -> list[dict]:
        clean = self._clean_query(query)
        if len(clean) < 2:
            return []

        limit = max(1, min(int(limit), 12))
        pattern = f"%{clean}%"
        prefix = f"{clean}%"

        rows = self.conn.execute(
            """
            SELECT tconst
            FROM title_basics
            WHERE titleType = 'movie'
              AND (
                    primaryTitle ILIKE ?
                 OR originalTitle ILIKE ?
              )
            ORDER BY
                CASE
                    WHEN LOWER(primaryTitle) = LOWER(?) THEN 0
                    WHEN primaryTitle ILIKE ? THEN 1
                    WHEN originalTitle ILIKE ? THEN 2
                    ELSE 3
                END,
                CASE
                    WHEN ? IS NULL THEN 9999
                    ELSE ABS(COALESCE(TRY_CAST(startYear AS INTEGER), 0) - ?)
                END,
                TRY_CAST(startYear AS INTEGER) DESC NULLS LAST,
                tconst
            LIMIT ?
            """,
            [pattern, pattern, clean, prefix, prefix, year, year, limit],
        ).fetchall()

        ids = [str(row[0]) for row in rows]

        if self.resolver.needs_resolution(clean):
            try:
                resolved = self.resolver.resolve_inputs(
                    title=clean,
                    director=None,
                    actors=[],
                    year=year,
                )
                match = resolved.get("matches", {}).get("title")
                imdb_id = str(match.get("imdb_id") or "") if isinstance(match, dict) else ""
                if imdb_id and imdb_id not in ids:
                    ids.insert(0, imdb_id)
            except Exception:
                # Русский alias — дополнительный слой. Локальный поиск должен
                # оставаться рабочим даже при проблеме внешнего источника.
                pass

        result: list[dict] = []
        for imdb_id in ids[:limit]:
            item = self._movie_details(imdb_id)
            if item is not None:
                result.append(item.to_dict())
        return result

    def search_people(
        self,
        query: str,
        *,
        role: str,
        limit: int = 8,
    ) -> list[dict]:
        clean = self._clean_query(query)
        if len(clean) < 2:
            return []

        role = role if role in {"director", "actor"} else "actor"
        categories = ("director",) if role == "director" else ("actor", "actress")
        placeholders = ",".join("?" for _ in categories)
        limit = max(1, min(int(limit), 12))
        pattern = f"%{clean}%"
        prefix = f"{clean}%"

        rows = self.conn.execute(
            f"""
            SELECT
                n.nconst,
                n.primaryName,
                COUNT(DISTINCT p.tconst) AS known_for_count
            FROM name_basics n
            JOIN title_principals p ON p.nconst = n.nconst
            WHERE p.category IN ({placeholders})
              AND n.primaryName ILIKE ?
            GROUP BY n.nconst, n.primaryName
            ORDER BY
                CASE
                    WHEN LOWER(n.primaryName) = LOWER(?) THEN 0
                    WHEN n.primaryName ILIKE ? THEN 1
                    ELSE 2
                END,
                known_for_count DESC,
                n.primaryName
            LIMIT ?
            """,
            [*categories, pattern, clean, prefix, limit],
        ).fetchall()

        items = [
            {
                "imdb_id": str(imdb_id),
                "name": str(name),
                "known_for_count": int(count or 0),
                "role": role,
            }
            for imdb_id, name, count in rows
        ]

        if self.resolver.needs_resolution(clean):
            try:
                kwargs = {
                    "title": None,
                    "director": clean if role == "director" else None,
                    "actors": [clean] if role == "actor" else [],
                    "year": None,
                }
                resolved = self.resolver.resolve_inputs(**kwargs)
                if role == "director":
                    match = resolved.get("matches", {}).get("director")
                else:
                    actor_matches = resolved.get("matches", {}).get("actors") or []
                    match = actor_matches[0] if actor_matches else None

                if isinstance(match, dict):
                    imdb_id = str(match.get("imdb_id") or "")
                    canonical = str(match.get("canonical") or "")
                    if imdb_id and canonical and not any(
                        item["imdb_id"] == imdb_id for item in items
                    ):
                        items.insert(
                            0,
                            {
                                "imdb_id": imdb_id,
                                "name": canonical,
                                "known_for_count": 0,
                                "role": role,
                                "matched_from": clean,
                            },
                        )
            except Exception:
                pass

        return items[:limit]
