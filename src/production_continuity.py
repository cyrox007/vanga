from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import uuid4

from src.production_context import (
    ProductionContextStore,
    ProductionContextValidationError,
    _clean_text,
    _parse_datetime,
)


DEPENDENCY_RELATIONS = {
    "sequel_of",
    "prequel_of",
    "spin_off_of",
    "continues_story_from",
    "crossover_with",
    "requires_context_from",
    "other",
}

DEPENDENCY_SCOPES = {
    "story",
    "character",
    "world",
    "continuity",
    "other",
}


class ProductionContinuityContext:
    """Factual cross-project dependency registry без quality-sign.

    Направление link: ``project_id`` — текущий/зависимый проект,
    ``related_project_id`` — проект, с которым документирована связь.
    Для symmetric relation (`crossover_with`) направление техническое и не
    интерпретируется как причинность.
    """

    def __init__(self, store: ProductionContextStore) -> None:
        self.store = store
        self.conn = store.conn
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS production_project_dependencies (
                dependency_id VARCHAR PRIMARY KEY,
                project_id VARCHAR NOT NULL,
                related_project_id VARCHAR NOT NULL,
                relation_type VARCHAR NOT NULL,
                scope VARCHAR NOT NULL,
                known_at TIMESTAMP WITH TIME ZONE NOT NULL,
                source_id VARCHAR NOT NULL,
                note VARCHAR
            )
            """
        )
        self.conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_prod_dependency_project_known
            ON production_project_dependencies(project_id, known_at)
            """
        )
        self.conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_prod_dependency_related_known
            ON production_project_dependencies(related_project_id, known_at)
            """
        )

    def add_dependency(self, payload: dict[str, Any]) -> str:
        project_id = _clean_text(
            payload.get("project_id"),
            field_name="project_id",
            limit=160,
            required=True,
        )
        related_project_id = _clean_text(
            payload.get("related_project_id"),
            field_name="related_project_id",
            limit=160,
            required=True,
        )
        if project_id == related_project_id:
            raise ProductionContextValidationError(
                "project dependency не может ссылаться на тот же project_id"
            )
        self.store._require_project(project_id)
        self.store._require_project(related_project_id)

        relation_type = str(payload.get("relation_type") or "").strip()
        if relation_type not in DEPENDENCY_RELATIONS:
            raise ProductionContextValidationError(
                f"Неизвестный relation_type project dependency: {relation_type!r}"
            )
        scope = str(payload.get("scope") or "").strip()
        if scope not in DEPENDENCY_SCOPES:
            raise ProductionContextValidationError(
                f"Неизвестный scope project dependency: {scope!r}"
            )

        source_id = _clean_text(
            payload.get("source_id"),
            field_name="source_id",
            limit=160,
            required=True,
        )
        self.store._require_source(source_id)
        known_at = _parse_datetime(payload.get("known_at"), field_name="known_at")
        dependency_id = _clean_text(
            payload.get("dependency_id") or uuid4(),
            field_name="dependency_id",
            limit=160,
            required=True,
        )
        note = _clean_text(
            payload.get("note"),
            field_name="dependency.note",
            limit=2000,
        ) or None

        self.conn.execute(
            """
            INSERT INTO production_project_dependencies(
                dependency_id, project_id, related_project_id,
                relation_type, scope, known_at, source_id, note
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(dependency_id) DO UPDATE SET
                project_id = excluded.project_id,
                related_project_id = excluded.related_project_id,
                relation_type = excluded.relation_type,
                scope = excluded.scope,
                known_at = excluded.known_at,
                source_id = excluded.source_id,
                note = excluded.note
            """,
            [
                dependency_id,
                project_id,
                related_project_id,
                relation_type,
                scope,
                known_at,
                source_id,
                note,
            ],
        )
        return dependency_id

    @staticmethod
    def _release_bucket(release_at: datetime | None, cutoff_dt: datetime) -> str:
        if release_at is None:
            return "unknown"
        if release_at <= cutoff_dt:
            return "released"
        return "future"

    def dependencies_as_of(self, project_id: str, cutoff) -> list[dict[str, Any]]:
        """Возвращает deduplicated outgoing dependency facts, известные к cutoff."""
        self.store._require_project(project_id)
        cutoff_dt = _parse_datetime(cutoff, field_name="cutoff")
        rows = self.conn.execute(
            """
            SELECT DISTINCT
                d.related_project_id,
                d.relation_type,
                d.scope,
                rp.title,
                rp.release_at,
                MIN(d.known_at) OVER (
                    PARTITION BY d.related_project_id, d.relation_type, d.scope
                ) AS first_known_at
            FROM production_project_dependencies d
            JOIN production_projects rp
              ON rp.project_id = d.related_project_id
            WHERE d.project_id = ?
              AND d.known_at <= ?
            ORDER BY first_known_at, d.related_project_id, d.relation_type, d.scope
            """,
            [project_id, cutoff_dt],
        ).fetchall()
        result: list[dict[str, Any]] = []
        seen: set[tuple[str, str, str]] = set()
        for related_id, relation_type, scope, title, release_at, known_at in rows:
            key = (str(related_id), str(relation_type), str(scope))
            if key in seen:
                continue
            seen.add(key)
            result.append(
                {
                    "related_project_id": str(related_id),
                    "title": str(title),
                    "relation_type": str(relation_type),
                    "scope": str(scope),
                    "known_at": known_at,
                    "release_at": release_at,
                    "release_state": self._release_bucket(release_at, cutoff_dt),
                }
            )
        return result

    def features_as_of(self, project_id: str, cutoff) -> dict[str, float]:
        """Строит прозрачный continuity-load proxy без знака качества."""
        self.store._require_project(project_id)
        cutoff_dt = _parse_datetime(cutoff, field_name="cutoff")
        outgoing = self.dependencies_as_of(project_id, cutoff_dt)

        incoming_rows = self.conn.execute(
            """
            SELECT DISTINCT
                d.project_id,
                d.relation_type,
                d.scope,
                p.release_at
            FROM production_project_dependencies d
            JOIN production_projects p ON p.project_id = d.project_id
            WHERE d.related_project_id = ?
              AND d.known_at <= ?
            """,
            [project_id, cutoff_dt],
        ).fetchall()
        incoming_keys: set[tuple[str, str, str]] = set()
        incoming: list[tuple[str, str, str, Any]] = []
        for source_project, relation_type, scope, release_at in incoming_rows:
            key = (str(source_project), str(relation_type), str(scope))
            if key in incoming_keys:
                continue
            incoming_keys.add(key)
            incoming.append((key[0], key[1], key[2], release_at))

        result: dict[str, float] = {
            "production_continuity_dependency_count": float(len(outgoing)),
            "production_continuity_prior_released_count": float(
                sum(1 for item in outgoing if item["release_state"] == "released")
            ),
            "production_continuity_future_announced_count": float(
                sum(1 for item in outgoing if item["release_state"] == "future")
            ),
            "production_continuity_unknown_release_count": float(
                sum(1 for item in outgoing if item["release_state"] == "unknown")
            ),
            "production_continuity_downstream_known_count": float(len(incoming)),
            "production_continuity_downstream_future_count": float(
                sum(
                    1
                    for _, _, _, release_at in incoming
                    if self._release_bucket(release_at, cutoff_dt) == "future"
                )
            ),
            "production_continuity_cross_project_count": float(
                len(
                    {item["related_project_id"] for item in outgoing}.union(
                        {item[0] for item in incoming}
                    )
                )
            ),
        }

        for relation in sorted(DEPENDENCY_RELATIONS):
            result[f"production_continuity_relation_{relation}_count"] = float(
                sum(1 for item in outgoing if item["relation_type"] == relation)
            )
        for scope in sorted(DEPENDENCY_SCOPES):
            result[f"production_continuity_scope_{scope}_count"] = float(
                sum(1 for item in outgoing if item["scope"] == scope)
            )

        prior_dates = [
            item["release_at"]
            for item in outgoing
            if item["release_state"] == "released" and item["release_at"] is not None
        ]
        if prior_dates:
            earliest = min(prior_dates)
            days = max(0.0, (cutoff_dt - earliest).total_seconds() / 86400.0)
            result["production_continuity_prior_release_span_years"] = days / 365.25
        else:
            result["production_continuity_prior_release_span_years"] = 0.0

        result["production_continuity_known"] = 1.0 if outgoing or incoming else 0.0
        return result
