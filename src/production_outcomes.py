from __future__ import annotations

import statistics
from pathlib import Path
from typing import Any, Iterable

import duckdb

from settings import config
from src.production_context import (
    ProductionContextValidationError,
    _parse_datetime,
)
from src.production_identity import (
    GROUP_KINDS,
    HISTORY_ENTITY_ROLES,
    ProductionIdentityHistory,
)


RATING_FALLBACK = 6.5


class ProductionOutcomeHistory:
    """Research-only outcome history для production identity.

    Temporal-контракт identity строгий: target/prior links должны быть известны к
    ``cutoff``, а prior project должен быть выпущен раньше target/release limit.

    ВАЖНО: IMDb ``title_ratings`` не содержит исторический timestamp рейтинга.
    Поэтому ``averageRating`` здесь является текущим snapshot результата уже
    вышедшего prior project. До появления P7 rating-history этот блок нельзя
    автоматически подключать к production CatBoost как строго point-in-time
    признак. Он предназначен для исследования и будущего ablation.
    """

    def __init__(
        self,
        identity: ProductionIdentityHistory,
        imdb_db_path: str | Path | None = None,
    ) -> None:
        self.identity = identity
        self.store = identity.store
        self.conn = identity.conn
        self.imdb_db_path = Path(imdb_db_path or config.IMDB_DB_PATH)

    def _require_imdb_db(self) -> None:
        if not self.imdb_db_path.is_file():
            raise ProductionContextValidationError(
                f"IMDb БД не найдена: {self.imdb_db_path}"
            )

    def _ratings_for_imdb_ids(self, imdb_ids: Iterable[str]) -> dict[str, float]:
        ids = sorted({str(value).strip() for value in imdb_ids if str(value).strip()})
        if not ids:
            return {}
        self._require_imdb_db()
        conn = duckdb.connect(str(self.imdb_db_path), read_only=True)
        try:
            try:
                rows = conn.execute(
                    """
                    SELECT tconst, TRY_CAST(averageRating AS DOUBLE) AS rating
                    FROM title_ratings
                    WHERE tconst IN (SELECT * FROM UNNEST(?))
                      AND TRY_CAST(averageRating AS DOUBLE) IS NOT NULL
                    """,
                    [ids],
                ).fetchall()
            except duckdb.Error as exc:
                raise ProductionContextValidationError(
                    "IMDb БД не содержит доступную таблицу title_ratings(tconst, averageRating)"
                ) from exc
        finally:
            conn.close()
        return {str(tconst): float(rating) for tconst, rating in rows}

    @staticmethod
    def _rating_summary(
        project_rows: list[tuple[str, str]],
        rating_by_imdb: dict[str, float],
        *,
        prefix: str,
    ) -> dict[str, float]:
        """Суммирует уникальные prior projects без двойного веса одинакового фильма."""
        unique_projects: dict[str, str] = {}
        for project_id, imdb_id in project_rows:
            if project_id and imdb_id:
                unique_projects[str(project_id)] = str(imdb_id)
        ratings = [
            rating_by_imdb[imdb_id]
            for imdb_id in unique_projects.values()
            if imdb_id in rating_by_imdb
        ]
        project_count = len(unique_projects)
        rated_count = len(ratings)
        return {
            f"{prefix}_prior_project_count": float(project_count),
            f"{prefix}_prior_rated_project_count": float(rated_count),
            f"{prefix}_prior_rating_coverage": (
                float(rated_count) / float(project_count) if project_count else 0.0
            ),
            f"{prefix}_prior_rating_avg": (
                float(statistics.fmean(ratings)) if ratings else RATING_FALLBACK
            ),
            f"{prefix}_prior_rating_median": (
                float(statistics.median(ratings)) if ratings else RATING_FALLBACK
            ),
            f"{prefix}_prior_rating_std": (
                float(statistics.pstdev(ratings)) if len(ratings) > 1 else 0.0
            ),
        }

    def _prior_group_projects(
        self,
        *,
        project_id: str,
        group_ids: list[str],
        cutoff_dt,
        release_limit,
        legacy_column: str,
    ) -> list[tuple[str, str]]:
        if not group_ids:
            return []
        rows = self.conn.execute(
            """
            SELECT DISTINCT p.project_id, p.imdb_id
            FROM production_projects p
            JOIN project_group_links l ON l.project_id = p.project_id
            WHERE p.project_id <> ?
              AND l.group_id IN (SELECT * FROM UNNEST(?))
              AND l.known_at <= ?
              AND p.imdb_id IS NOT NULL
              AND p.release_at IS NOT NULL
              AND p.release_at <= ?
              AND p.release_at < ?
            """,
            [project_id, group_ids, cutoff_dt, cutoff_dt, release_limit],
        ).fetchall()
        if rows:
            return [(str(row[0]), str(row[1])) for row in rows]

        if legacy_column not in {"franchise_id", "shared_universe_id"}:
            return []
        legacy = self.conn.execute(
            f"""
            SELECT DISTINCT project_id, imdb_id
            FROM production_projects
            WHERE project_id <> ?
              AND {legacy_column} IN (SELECT * FROM UNNEST(?))
              AND identity_known_at <= ?
              AND imdb_id IS NOT NULL
              AND release_at IS NOT NULL
              AND release_at <= ?
              AND release_at < ?
            """,
            [project_id, group_ids, cutoff_dt, cutoff_dt, release_limit],
        ).fetchall()
        return [(str(row[0]), str(row[1])) for row in legacy]

    def _target_entities_by_role(self, project_id: str, cutoff_dt) -> dict[str, list[str]]:
        rows = self.conn.execute(
            """
            SELECT DISTINCT entity_id, role
            FROM project_entity_links
            WHERE project_id = ?
              AND known_at <= ?
              AND role IN (SELECT * FROM UNNEST(?))
            """,
            [project_id, cutoff_dt, list(HISTORY_ENTITY_ROLES)],
        ).fetchall()
        result = {role: [] for role in HISTORY_ENTITY_ROLES}
        for entity_id, role in rows:
            result[str(role)].append(str(entity_id))
        return result

    def _prior_entity_projects(
        self,
        *,
        project_id: str,
        entity_id: str,
        cutoff_dt,
        release_limit,
    ) -> list[tuple[str, str]]:
        rows = self.conn.execute(
            """
            SELECT DISTINCT p.project_id, p.imdb_id
            FROM production_projects p
            JOIN project_entity_links l ON l.project_id = p.project_id
            WHERE p.project_id <> ?
              AND l.entity_id = ?
              AND l.known_at <= ?
              AND p.imdb_id IS NOT NULL
              AND p.release_at IS NOT NULL
              AND p.release_at <= ?
              AND p.release_at < ?
            """,
            [project_id, entity_id, cutoff_dt, cutoff_dt, release_limit],
        ).fetchall()
        return [(str(row[0]), str(row[1])) for row in rows]

    def features_as_of(self, project_id: str, cutoff) -> dict[str, float]:
        """Возвращает research outcome aggregates для identity, видимой на cutoff."""
        self.store._require_project(project_id)
        cutoff_dt = _parse_datetime(cutoff, field_name="cutoff")
        release_limit = self.identity._target_release_limit(project_id, cutoff_dt)
        groups = self.identity._visible_groups(project_id, cutoff_dt)
        entities_by_role = self._target_entities_by_role(project_id, cutoff_dt)

        group_rows: dict[str, list[tuple[str, str]]] = {
            "franchise": self._prior_group_projects(
                project_id=project_id,
                group_ids=groups["franchise"],
                cutoff_dt=cutoff_dt,
                release_limit=release_limit,
                legacy_column="franchise_id",
            ),
            "shared_universe": self._prior_group_projects(
                project_id=project_id,
                group_ids=groups["shared_universe"],
                cutoff_dt=cutoff_dt,
                release_limit=release_limit,
                legacy_column="shared_universe_id",
            ),
        }

        per_entity_rows: dict[str, dict[str, list[tuple[str, str]]]] = {}
        all_imdb_ids: set[str] = set()
        for rows in group_rows.values():
            all_imdb_ids.update(imdb_id for _, imdb_id in rows)

        for role, entity_ids in entities_by_role.items():
            per_entity_rows[role] = {}
            for entity_id in entity_ids:
                rows = self._prior_entity_projects(
                    project_id=project_id,
                    entity_id=entity_id,
                    cutoff_dt=cutoff_dt,
                    release_limit=release_limit,
                )
                per_entity_rows[role][entity_id] = rows
                all_imdb_ids.update(imdb_id for _, imdb_id in rows)

        rating_by_imdb = self._ratings_for_imdb_ids(all_imdb_ids) if all_imdb_ids else {}
        result: dict[str, float] = {}

        for kind in sorted(GROUP_KINDS):
            result.update(
                self._rating_summary(
                    group_rows[kind],
                    rating_by_imdb,
                    prefix=f"production_{kind}",
                )
            )

        all_current_entity_ids: list[str] = []
        all_role_projects: list[tuple[str, str]] = []
        for role in HISTORY_ENTITY_ROLES:
            entity_ids = entities_by_role[role]
            all_current_entity_ids.extend(entity_ids)
            role_rows: list[tuple[str, str]] = []
            entity_has_rating = 0
            for entity_id in entity_ids:
                rows = per_entity_rows[role][entity_id]
                role_rows.extend(rows)
                if any(imdb_id in rating_by_imdb for _, imdb_id in rows):
                    entity_has_rating += 1
            result.update(
                self._rating_summary(
                    role_rows,
                    rating_by_imdb,
                    prefix=f"production_{role}",
                )
            )
            result[f"production_{role}_rating_history_known_ratio"] = (
                float(entity_has_rating) / float(len(entity_ids)) if entity_ids else 0.0
            )
            all_role_projects.extend(role_rows)

        result.update(
            self._rating_summary(
                all_role_projects,
                rating_by_imdb,
                prefix="production_identity",
            )
        )

        unique_current = sorted(set(all_current_entity_ids))
        repeat_rows: list[tuple[str, str]] = []
        if unique_current:
            rows = self.conn.execute(
                """
                WITH shared AS (
                    SELECT
                        p.project_id,
                        p.imdb_id,
                        COUNT(DISTINCT l.entity_id) AS shared_entity_count
                    FROM production_projects p
                    JOIN project_entity_links l ON l.project_id = p.project_id
                    WHERE p.project_id <> ?
                      AND l.entity_id IN (SELECT * FROM UNNEST(?))
                      AND l.known_at <= ?
                      AND p.imdb_id IS NOT NULL
                      AND p.release_at IS NOT NULL
                      AND p.release_at <= ?
                      AND p.release_at < ?
                    GROUP BY p.project_id, p.imdb_id
                )
                SELECT project_id, imdb_id
                FROM shared
                WHERE shared_entity_count >= 2
                """,
                [project_id, unique_current, cutoff_dt, cutoff_dt, release_limit],
            ).fetchall()
            repeat_rows = [(str(row[0]), str(row[1])) for row in rows]
        result.update(
            self._rating_summary(
                repeat_rows,
                rating_by_imdb,
                prefix="production_key_team_repeat",
            )
        )

        # Явный machine-readable guard: outcome rating пока не point-in-time snapshot.
        result["production_outcome_rating_point_in_time"] = 0.0
        return result
