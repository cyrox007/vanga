from __future__ import annotations

import statistics
from typing import Any

from src.production_context import (
    CONSULTANCY_SCOPES,
    PRODUCTION_STAGES,
    ProductionContextStore,
    _parse_datetime,
)


class ProductionConsultancyContext:
    """Нейтральный consultancy-контекст с temporal history.

    Название конкретной consultancy не получает положительный/отрицательный
    коэффициент. Признаки описывают только текущий scope/stage-контекст и
    подтверждённую историю тех же canonical consultancy entities на ранее
    выпущенных проектах.
    """

    def __init__(self, store: ProductionContextStore) -> None:
        self.store = store
        self.conn = store.conn

    def _release_limit(self, project_id: str, cutoff_dt):
        row = self.conn.execute(
            "SELECT release_at FROM production_projects WHERE project_id = ?",
            [project_id],
        ).fetchone()
        release_at = row[0] if row else None
        if release_at is not None and release_at < cutoff_dt:
            return release_at
        return cutoff_dt

    def engagements_as_of(self, project_id: str, cutoff) -> list[dict[str, Any]]:
        """Возвращает deduplicated consultancy engagements, известные к cutoff."""
        self.store._require_project(project_id)
        cutoff_dt = _parse_datetime(cutoff, field_name="cutoff")
        rows = self.conn.execute(
            """
            SELECT
                c.entity_id,
                e.name,
                c.scope,
                c.stage,
                MIN(c.known_at) AS first_known_at
            FROM consultancy_engagements c
            JOIN production_entities e USING (entity_id)
            WHERE c.project_id = ?
              AND c.known_at <= ?
            GROUP BY c.entity_id, e.name, c.scope, c.stage
            ORDER BY first_known_at, c.entity_id, c.scope, c.stage
            """,
            [project_id, cutoff_dt],
        ).fetchall()
        return [
            {
                "entity_id": str(entity_id),
                "name": str(name),
                "scope": str(scope),
                "stage": str(stage),
                "known_at": known_at,
            }
            for entity_id, name, scope, stage, known_at in rows
        ]

    def _current_entity_scopes(self, project_id: str, cutoff_dt) -> dict[str, set[str]]:
        rows = self.conn.execute(
            """
            SELECT DISTINCT entity_id, scope
            FROM consultancy_engagements
            WHERE project_id = ? AND known_at <= ?
            """,
            [project_id, cutoff_dt],
        ).fetchall()
        result: dict[str, set[str]] = {}
        for entity_id, scope in rows:
            result.setdefault(str(entity_id), set()).add(str(scope))
        return result

    def _prior_projects_for_entity(
        self,
        *,
        target_project_id: str,
        entity_id: str,
        cutoff_dt,
        release_limit,
    ) -> set[str]:
        rows = self.conn.execute(
            """
            SELECT DISTINCT p.project_id
            FROM production_projects p
            JOIN consultancy_engagements c ON c.project_id = p.project_id
            WHERE p.project_id <> ?
              AND c.entity_id = ?
              AND c.known_at <= ?
              AND p.release_at IS NOT NULL
              AND p.release_at <= ?
              AND p.release_at < ?
            """,
            [target_project_id, entity_id, cutoff_dt, cutoff_dt, release_limit],
        ).fetchall()
        return {str(row[0]) for row in rows}

    def _prior_same_scope_projects(
        self,
        *,
        target_project_id: str,
        entity_id: str,
        scopes: set[str],
        cutoff_dt,
        release_limit,
    ) -> set[str]:
        if not scopes:
            return set()
        rows = self.conn.execute(
            """
            SELECT DISTINCT p.project_id
            FROM production_projects p
            JOIN consultancy_engagements c ON c.project_id = p.project_id
            WHERE p.project_id <> ?
              AND c.entity_id = ?
              AND c.scope IN (SELECT * FROM UNNEST(?))
              AND c.known_at <= ?
              AND p.release_at IS NOT NULL
              AND p.release_at <= ?
              AND p.release_at < ?
            """,
            [
                target_project_id,
                entity_id,
                sorted(scopes),
                cutoff_dt,
                cutoff_dt,
                release_limit,
            ],
        ).fetchall()
        return {str(row[0]) for row in rows}

    def features_as_of(self, project_id: str, cutoff) -> dict[str, float]:
        """Строит factual consultancy coverage/history без quality-sign."""
        self.store._require_project(project_id)
        cutoff_dt = _parse_datetime(cutoff, field_name="cutoff")
        release_limit = self._release_limit(project_id, cutoff_dt)
        engagements = self.engagements_as_of(project_id, cutoff_dt)
        entity_scopes = self._current_entity_scopes(project_id, cutoff_dt)

        entity_ids = sorted(entity_scopes)
        unique_scopes = {item["scope"] for item in engagements}
        unique_stages = {item["stage"] for item in engagements}
        multi_scope_entities = sum(
            1 for scopes in entity_scopes.values() if len(scopes) > 1
        )

        prior_counts: list[float] = []
        same_scope_counts: list[float] = []
        prior_union: set[str] = set()
        same_scope_union: set[str] = set()
        known_entities = 0
        same_scope_known_entities = 0

        for entity_id in entity_ids:
            prior = self._prior_projects_for_entity(
                target_project_id=project_id,
                entity_id=entity_id,
                cutoff_dt=cutoff_dt,
                release_limit=release_limit,
            )
            same_scope = self._prior_same_scope_projects(
                target_project_id=project_id,
                entity_id=entity_id,
                scopes=entity_scopes[entity_id],
                cutoff_dt=cutoff_dt,
                release_limit=release_limit,
            )
            prior_count = float(len(prior))
            same_scope_count = float(len(same_scope))
            prior_counts.append(prior_count)
            same_scope_counts.append(same_scope_count)
            prior_union.update(prior)
            same_scope_union.update(same_scope)
            if prior_count > 0:
                known_entities += 1
            if same_scope_count > 0:
                same_scope_known_entities += 1

        result: dict[str, float] = {
            "production_consultancy_entity_count": float(len(entity_ids)),
            "production_consultancy_engagement_context_count": float(len(engagements)),
            "production_consultancy_scope_diversity": float(len(unique_scopes)),
            "production_consultancy_stage_diversity": float(len(unique_stages)),
            "production_consultancy_multi_scope_entity_count": float(multi_scope_entities),
            "production_consultancy_history_known_ratio": (
                float(known_entities) / float(len(entity_ids)) if entity_ids else 0.0
            ),
            "production_consultancy_prior_project_count_mean": (
                float(statistics.fmean(prior_counts)) if prior_counts else 0.0
            ),
            "production_consultancy_prior_project_count_max": float(
                max(prior_counts, default=0.0)
            ),
            "production_consultancy_prior_shared_project_count": float(len(prior_union)),
            "production_consultancy_same_scope_history_known_ratio": (
                float(same_scope_known_entities) / float(len(entity_ids))
                if entity_ids
                else 0.0
            ),
            "production_consultancy_same_scope_prior_project_count_mean": (
                float(statistics.fmean(same_scope_counts)) if same_scope_counts else 0.0
            ),
            "production_consultancy_same_scope_prior_shared_project_count": float(
                len(same_scope_union)
            ),
        }

        for scope in sorted(CONSULTANCY_SCOPES):
            result[f"production_consultancy_context_scope_{scope}_entity_count"] = float(
                len(
                    {
                        item["entity_id"]
                        for item in engagements
                        if item["scope"] == scope
                    }
                )
            )
        for stage in sorted(PRODUCTION_STAGES):
            result[f"production_consultancy_context_stage_{stage}_entity_count"] = float(
                len(
                    {
                        item["entity_id"]
                        for item in engagements
                        if item["stage"] == stage
                    }
                )
            )

        return result
