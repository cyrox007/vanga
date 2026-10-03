from __future__ import annotations

import statistics
from pathlib import Path
from typing import Iterable, Sequence

import duckdb

from settings import config
from src.source_context import SourceContextStore, SourceContextValidationError, _parse_datetime


class SourceTeamHistory:
    """История creative team именно на прошлых адаптациях.

    Слой использует только ранее выпущенные проекты, source-link которых уже
    известен к ``cutoff``. Он считает counts/coverage и намеренно не использует
    rating, чтобы не добавлять новую temporal неоднозначность на этом этапе P3.
    """

    def __init__(
        self,
        source_store: SourceContextStore,
        imdb_db_path: str | Path | None = None,
    ) -> None:
        self.store = source_store
        self.conn = source_store.conn
        self.imdb_db_path = Path(imdb_db_path or config.IMDB_DB_PATH)

    def _imdb(self) -> duckdb.DuckDBPyConnection:
        if not self.imdb_db_path.is_file():
            raise SourceContextValidationError(f"IMDb БД не найдена: {self.imdb_db_path}")
        return duckdb.connect(str(self.imdb_db_path), read_only=True)

    def _target_release_limit(self, project_id: str, cutoff_dt):
        row = self.conn.execute(
            "SELECT release_at FROM source_context_projects WHERE project_id = ?",
            [project_id],
        ).fetchone()
        release_at = row[0] if row else None
        if release_at is not None and release_at < cutoff_dt:
            return release_at
        return cutoff_dt

    def _target_imdb_id(self, project_id: str) -> str | None:
        row = self.conn.execute(
            "SELECT imdb_id FROM source_context_projects WHERE project_id = ?",
            [project_id],
        ).fetchone()
        return str(row[0]) if row and row[0] else None

    def _target_source_types(self, project_id: str, cutoff_dt) -> set[str]:
        rows = self.conn.execute(
            """
            SELECT DISTINCT w.source_type
            FROM project_source_links l
            JOIN source_works w USING (work_id)
            WHERE l.project_id = ? AND l.known_at <= ?
            """,
            [project_id, cutoff_dt],
        ).fetchall()
        return {str(row[0]) for row in rows}

    def _prior_adaptations(self, project_id: str, cutoff_dt) -> dict[str, dict]:
        """Возвращает prior adaptation projects и source types, видимые на cutoff."""
        release_limit = self._target_release_limit(project_id, cutoff_dt)
        rows = self.conn.execute(
            """
            SELECT
                p.project_id,
                p.imdb_id,
                p.release_at,
                LIST(DISTINCT w.source_type) AS source_types
            FROM source_context_projects p
            JOIN project_source_links l ON l.project_id = p.project_id
            JOIN source_works w USING (work_id)
            WHERE p.project_id <> ?
              AND p.imdb_id IS NOT NULL
              AND p.release_at IS NOT NULL
              AND p.release_at <= ?
              AND p.release_at < ?
              AND l.known_at <= ?
            GROUP BY p.project_id, p.imdb_id, p.release_at
            """,
            [project_id, cutoff_dt, release_limit, cutoff_dt],
        ).fetchall()
        return {
            str(project_id_value): {
                "imdb_id": str(imdb_id),
                "release_at": release_at,
                "source_types": {str(value) for value in (source_types or [])},
            }
            for project_id_value, imdb_id, release_at, source_types in rows
        }

    @staticmethod
    def _clean_people(values: Sequence[str | None] | None) -> tuple[int, list[str]]:
        raw = [str(value or "").strip() for value in (values or [])]
        slots = len(raw)
        valid: list[str] = []
        for value in raw:
            if value and value != "Unknown" and value not in valid:
                valid.append(value)
        return slots, valid

    def _target_people_and_genres(
        self,
        project_id: str,
        *,
        director_nconsts: Sequence[str | None] | None,
        writer_nconst: str | None,
    ) -> tuple[int, list[str], str | None, set[str]]:
        target_imdb = self._target_imdb_id(project_id)
        director_slots, directors = self._clean_people(director_nconsts)
        writer = str(writer_nconst or "").strip() or None
        if writer == "Unknown":
            writer = None

        genres: set[str] = set()
        if not target_imdb:
            return director_slots, directors, writer, genres

        conn = self._imdb()
        try:
            if not directors and director_nconsts is None:
                try:
                    rows = conn.execute(
                        """
                        SELECT DISTINCT nconst
                        FROM title_principals
                        WHERE tconst = ? AND category = 'director'
                        ORDER BY nconst
                        """,
                        [target_imdb],
                    ).fetchall()
                except duckdb.Error as exc:
                    raise SourceContextValidationError(
                        "IMDb БД не содержит title_principals для adaptation-team history"
                    ) from exc
                directors = [str(row[0]) for row in rows]
                director_slots = len(directors)

            if writer is None and writer_nconst is None:
                try:
                    row = conn.execute(
                        """
                        SELECT nconst
                        FROM title_writers
                        WHERE tconst = ?
                        ORDER BY nconst
                        LIMIT 1
                        """,
                        [target_imdb],
                    ).fetchone()
                except duckdb.Error as exc:
                    raise SourceContextValidationError(
                        "IMDb БД не содержит title_writers для adaptation-team history"
                    ) from exc
                writer = str(row[0]) if row else None

            try:
                row = conn.execute(
                    "SELECT genres FROM title_basics WHERE tconst = ?",
                    [target_imdb],
                ).fetchone()
            except duckdb.Error as exc:
                raise SourceContextValidationError(
                    "IMDb БД не содержит title_basics для genre overlap"
                ) from exc
            if row and row[0] and str(row[0]) != "\\N":
                genres = {
                    value.strip()
                    for value in str(row[0]).split(",")
                    if value.strip()
                }
        finally:
            conn.close()
        return director_slots, directors, writer, genres

    def _prior_imdb_context(self, prior: dict[str, dict]) -> dict[str, dict]:
        if not prior:
            return {}
        imdb_ids = sorted({item["imdb_id"] for item in prior.values()})
        conn = self._imdb()
        try:
            try:
                director_rows = conn.execute(
                    """
                    SELECT DISTINCT tconst, nconst
                    FROM title_principals
                    WHERE tconst IN (SELECT * FROM UNNEST(?))
                      AND category = 'director'
                    """,
                    [imdb_ids],
                ).fetchall()
                writer_rows = conn.execute(
                    """
                    SELECT DISTINCT tconst, nconst
                    FROM title_writers
                    WHERE tconst IN (SELECT * FROM UNNEST(?))
                    """,
                    [imdb_ids],
                ).fetchall()
                basics_rows = conn.execute(
                    """
                    SELECT tconst, genres
                    FROM title_basics
                    WHERE tconst IN (SELECT * FROM UNNEST(?))
                    """,
                    [imdb_ids],
                ).fetchall()
            except duckdb.Error as exc:
                raise SourceContextValidationError(
                    "IMDb БД не содержит таблицы title_principals/title_writers/title_basics"
                ) from exc
        finally:
            conn.close()

        result = {
            imdb_id: {"directors": set(), "writers": set(), "genres": set()}
            for imdb_id in imdb_ids
        }
        for tconst, nconst in director_rows:
            result[str(tconst)]["directors"].add(str(nconst))
        for tconst, nconst in writer_rows:
            result[str(tconst)]["writers"].add(str(nconst))
        for tconst, genres in basics_rows:
            if genres and str(genres) != "\\N":
                result[str(tconst)]["genres"] = {
                    value.strip() for value in str(genres).split(",") if value.strip()
                }
        return result

    @staticmethod
    def _aggregate_counts(counts: list[float], *, prefix: str) -> dict[str, float]:
        return {
            f"{prefix}_known_ratio": (
                float(sum(1 for value in counts if value > 0)) / float(len(counts))
                if counts
                else 0.0
            ),
            f"{prefix}_count_mean": (
                float(statistics.fmean(counts)) if counts else 0.0
            ),
            f"{prefix}_count_max": float(max(counts, default=0.0)),
        }

    def features_as_of(
        self,
        project_id: str,
        cutoff,
        *,
        director_nconsts: Sequence[str | None] | None = None,
        writer_nconst: str | None = None,
    ) -> dict[str, float]:
        """Считает adaptation-specific history текущей creative team."""
        self.store._require_project(project_id)
        cutoff_dt = _parse_datetime(cutoff, field_name="cutoff")
        target_types = self._target_source_types(project_id, cutoff_dt)
        director_slots, directors, writer, target_genres = self._target_people_and_genres(
            project_id,
            director_nconsts=director_nconsts,
            writer_nconst=writer_nconst,
        )
        prior = self._prior_adaptations(project_id, cutoff_dt)
        prior_imdb = self._prior_imdb_context(prior)

        # Сохраняем unresolved director slots в denominator как нули.
        director_counts: list[float] = []
        director_type_counts: list[float] = []
        director_genre_counts: list[float] = []
        for director_id in directors:
            adaptation_count = 0
            same_type_count = 0
            same_genre_count = 0
            for item in prior.values():
                imdb = prior_imdb.get(item["imdb_id"], {})
                if director_id not in imdb.get("directors", set()):
                    continue
                adaptation_count += 1
                if target_types and target_types.intersection(item["source_types"]):
                    same_type_count += 1
                if target_genres and target_genres.intersection(imdb.get("genres", set())):
                    same_genre_count += 1
            director_counts.append(float(adaptation_count))
            director_type_counts.append(float(same_type_count))
            director_genre_counts.append(float(same_genre_count))
        missing_directors = max(0, director_slots - len(directors))
        director_counts.extend([0.0] * missing_directors)
        director_type_counts.extend([0.0] * missing_directors)
        director_genre_counts.extend([0.0] * missing_directors)

        writer_count = 0.0
        writer_type_count = 0.0
        writer_genre_count = 0.0
        if writer:
            for item in prior.values():
                imdb = prior_imdb.get(item["imdb_id"], {})
                if writer not in imdb.get("writers", set()):
                    continue
                writer_count += 1.0
                if target_types and target_types.intersection(item["source_types"]):
                    writer_type_count += 1.0
                if target_genres and target_genres.intersection(imdb.get("genres", set())):
                    writer_genre_count += 1.0

        pair_counts: list[float] = []
        pair_type_counts: list[float] = []
        pair_genre_counts: list[float] = []
        if writer:
            for director_id in directors:
                count = type_count = genre_count = 0
                for item in prior.values():
                    imdb = prior_imdb.get(item["imdb_id"], {})
                    if (
                        director_id not in imdb.get("directors", set())
                        or writer not in imdb.get("writers", set())
                    ):
                        continue
                    count += 1
                    if target_types and target_types.intersection(item["source_types"]):
                        type_count += 1
                    if target_genres and target_genres.intersection(imdb.get("genres", set())):
                        genre_count += 1
                pair_counts.append(float(count))
                pair_type_counts.append(float(type_count))
                pair_genre_counts.append(float(genre_count))
            pair_counts.extend([0.0] * missing_directors)
            pair_type_counts.extend([0.0] * missing_directors)
            pair_genre_counts.extend([0.0] * missing_directors)
        elif director_slots:
            pair_counts = [0.0] * director_slots
            pair_type_counts = [0.0] * director_slots
            pair_genre_counts = [0.0] * director_slots

        result: dict[str, float] = {
            "source_team_prior_adaptation_project_count": float(len(prior)),
            "source_team_target_type_known": 1.0 if target_types else 0.0,
            "source_team_target_genre_known": 1.0 if target_genres else 0.0,
            "source_writer_adaptation_count": writer_count,
            "source_writer_adaptation_known": 1.0 if writer_count > 0 else 0.0,
            "source_writer_same_type_adaptation_count": writer_type_count,
            "source_writer_same_genre_adaptation_count": writer_genre_count,
        }
        result.update(
            self._aggregate_counts(
                director_counts,
                prefix="source_director_adaptation",
            )
        )
        result.update(
            self._aggregate_counts(
                director_type_counts,
                prefix="source_director_same_type_adaptation",
            )
        )
        result.update(
            self._aggregate_counts(
                director_genre_counts,
                prefix="source_director_same_genre_adaptation",
            )
        )
        result.update(
            self._aggregate_counts(
                pair_counts,
                prefix="source_director_writer_adaptation_pair",
            )
        )
        result.update(
            self._aggregate_counts(
                pair_type_counts,
                prefix="source_director_writer_same_type_pair",
            )
        )
        result.update(
            self._aggregate_counts(
                pair_genre_counts,
                prefix="source_director_writer_same_genre_pair",
            )
        )
        return result
