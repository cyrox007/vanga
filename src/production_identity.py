from __future__ import annotations

import re
import statistics
import unicodedata
from datetime import datetime
from typing import Any
from uuid import uuid4

from src.production_context import (
    PROJECT_ROLES,
    ProductionContextStore,
    ProductionContextValidationError,
    _clean_text,
    _now,
    _parse_datetime,
)


GROUP_KINDS = {"franchise", "shared_universe"}
HISTORY_ENTITY_ROLES = (
    "studio",
    "production_company",
    "production_label",
    "producer",
    "creative_lead",
)


def normalize_identity_alias(value: Any) -> str:
    """Строит только технический exact-key, но не выполняет fuzzy-слияние.

    Разные сущности никогда не объединяются автоматически по похожести строки.
    Варианты названия должны быть явно зарегистрированы как alias с provenance.
    """
    text = unicodedata.normalize("NFKC", str(value or "")).casefold().strip()
    text = re.sub(r"[^\w]+", " ", text, flags=re.UNICODE)
    return " ".join(text.split())


class ProductionIdentityHistory:
    """Нормализация production entities/groups и нейтральная история as-of.

    Этот слой не присваивает студии, продюсеру или франшизе оценку качества.
    Он отвечает только на воспроизводимые вопросы: какая canonical-сущность
    стояла за alias, что было известно на cutoff и сколько более ранних уже
    выпущенных проектов связано с текущими production identity.
    """

    def __init__(self, store: ProductionContextStore) -> None:
        self.store = store
        self.conn = store.conn
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS production_groups (
                group_id VARCHAR PRIMARY KEY,
                kind VARCHAR NOT NULL,
                name VARCHAR NOT NULL,
                external_id VARCHAR,
                created_at TIMESTAMP WITH TIME ZONE NOT NULL
            )
            """
        )
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS production_entity_aliases (
                alias_key VARCHAR NOT NULL,
                alias VARCHAR NOT NULL,
                entity_id VARCHAR NOT NULL,
                known_at TIMESTAMP WITH TIME ZONE NOT NULL,
                source_id VARCHAR NOT NULL,
                PRIMARY KEY(alias_key, entity_id)
            )
            """
        )
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS production_group_aliases (
                alias_key VARCHAR NOT NULL,
                alias VARCHAR NOT NULL,
                group_id VARCHAR NOT NULL,
                known_at TIMESTAMP WITH TIME ZONE NOT NULL,
                source_id VARCHAR NOT NULL,
                PRIMARY KEY(alias_key, group_id)
            )
            """
        )
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS project_group_links (
                link_id VARCHAR PRIMARY KEY,
                project_id VARCHAR NOT NULL,
                group_id VARCHAR NOT NULL,
                known_at TIMESTAMP WITH TIME ZONE NOT NULL,
                source_id VARCHAR NOT NULL,
                installment_index INTEGER,
                note VARCHAR
            )
            """
        )
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_entity_alias_key ON production_entity_aliases(alias_key, known_at)"
        )
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_group_alias_key ON production_group_aliases(alias_key, known_at)"
        )
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_project_group_project_known ON project_group_links(project_id, known_at)"
        )
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_project_group_group_known ON project_group_links(group_id, known_at)"
        )

    def _require_group(self, group_id: str) -> tuple[str, str]:
        row = self.conn.execute(
            "SELECT kind, name FROM production_groups WHERE group_id = ?",
            [group_id],
        ).fetchone()
        if row is None:
            raise ProductionContextValidationError(f"Неизвестный group_id: {group_id!r}")
        return str(row[0]), str(row[1])

    def upsert_group(self, payload: dict[str, Any]) -> str:
        kind = str(payload.get("kind") or "").strip()
        if kind not in GROUP_KINDS:
            raise ProductionContextValidationError(f"Неизвестный kind production group: {kind!r}")
        group_id = _clean_text(
            payload.get("group_id") or uuid4(),
            field_name="group_id",
            limit=160,
            required=True,
        )
        name = _clean_text(
            payload.get("name"),
            field_name="group.name",
            limit=500,
            required=True,
        )
        external_id = _clean_text(
            payload.get("external_id"),
            field_name="group.external_id",
            limit=300,
        ) or None
        self.conn.execute(
            """
            INSERT INTO production_groups(group_id, kind, name, external_id, created_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(group_id) DO UPDATE SET
                kind = excluded.kind,
                name = excluded.name,
                external_id = excluded.external_id
            """,
            [group_id, kind, name, external_id, _now()],
        )
        return group_id

    def add_entity_alias(self, payload: dict[str, Any]) -> str:
        entity_id = _clean_text(
            payload.get("entity_id"),
            field_name="entity_id",
            limit=160,
            required=True,
        )
        source_id = _clean_text(
            payload.get("source_id"),
            field_name="source_id",
            limit=160,
            required=True,
        )
        self.store._require_entity(entity_id)
        self.store._require_source(source_id)
        alias = _clean_text(
            payload.get("alias"),
            field_name="alias",
            limit=500,
            required=True,
        )
        alias_key = normalize_identity_alias(alias)
        if not alias_key:
            raise ProductionContextValidationError("alias не должен нормализоваться в пустое значение")
        known_at = _parse_datetime(payload.get("known_at"), field_name="known_at")
        self.conn.execute(
            """
            INSERT INTO production_entity_aliases(alias_key, alias, entity_id, known_at, source_id)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(alias_key, entity_id) DO UPDATE SET
                alias = excluded.alias,
                known_at = excluded.known_at,
                source_id = excluded.source_id
            """,
            [alias_key, alias, entity_id, known_at, source_id],
        )
        return alias_key

    def add_group_alias(self, payload: dict[str, Any]) -> str:
        group_id = _clean_text(
            payload.get("group_id"),
            field_name="group_id",
            limit=160,
            required=True,
        )
        source_id = _clean_text(
            payload.get("source_id"),
            field_name="source_id",
            limit=160,
            required=True,
        )
        self._require_group(group_id)
        self.store._require_source(source_id)
        alias = _clean_text(
            payload.get("alias"),
            field_name="alias",
            limit=500,
            required=True,
        )
        alias_key = normalize_identity_alias(alias)
        if not alias_key:
            raise ProductionContextValidationError("alias не должен нормализоваться в пустое значение")
        known_at = _parse_datetime(payload.get("known_at"), field_name="known_at")
        self.conn.execute(
            """
            INSERT INTO production_group_aliases(alias_key, alias, group_id, known_at, source_id)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(alias_key, group_id) DO UPDATE SET
                alias = excluded.alias,
                known_at = excluded.known_at,
                source_id = excluded.source_id
            """,
            [alias_key, alias, group_id, known_at, source_id],
        )
        return alias_key

    def link_group(self, payload: dict[str, Any]) -> str:
        project_id = _clean_text(
            payload.get("project_id"),
            field_name="project_id",
            limit=160,
            required=True,
        )
        group_id = _clean_text(
            payload.get("group_id"),
            field_name="group_id",
            limit=160,
            required=True,
        )
        source_id = _clean_text(
            payload.get("source_id"),
            field_name="source_id",
            limit=160,
            required=True,
        )
        self.store._require_project(project_id)
        self._require_group(group_id)
        self.store._require_source(source_id)
        known_at = _parse_datetime(payload.get("known_at"), field_name="known_at")
        installment_index = payload.get("installment_index")
        if installment_index not in {None, ""}:
            try:
                installment_index = int(installment_index)
            except (TypeError, ValueError) as exc:
                raise ProductionContextValidationError(
                    "group installment_index должен быть целым числом"
                ) from exc
            if installment_index < 1:
                raise ProductionContextValidationError("group installment_index должен быть >= 1")
        else:
            installment_index = None
        link_id = _clean_text(
            payload.get("link_id") or uuid4(),
            field_name="group_link_id",
            limit=160,
            required=True,
        )
        note = _clean_text(
            payload.get("note"),
            field_name="group_link.note",
            limit=2000,
        ) or None
        self.conn.execute(
            """
            INSERT INTO project_group_links(
                link_id, project_id, group_id, known_at, source_id,
                installment_index, note
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(link_id) DO UPDATE SET
                project_id = excluded.project_id,
                group_id = excluded.group_id,
                known_at = excluded.known_at,
                source_id = excluded.source_id,
                installment_index = excluded.installment_index,
                note = excluded.note
            """,
            [
                link_id,
                project_id,
                group_id,
                known_at,
                source_id,
                installment_index,
                note,
            ],
        )
        return link_id

    @staticmethod
    def _single_candidate(
        rows: list[tuple[Any, ...]],
        *,
        label: str,
        query: str,
    ) -> dict[str, Any] | None:
        unique: dict[str, tuple[Any, ...]] = {str(row[0]): row for row in rows}
        if not unique:
            return None
        if len(unique) > 1:
            ids = ", ".join(sorted(unique))
            raise ProductionContextValidationError(
                f"{label} {query!r} неоднозначен; кандидаты: {ids}. Уточните kind/canonical ID."
            )
        row = next(iter(unique.values()))
        return {
            "id": str(row[0]),
            "kind": str(row[1]),
            "name": str(row[2]),
        }

    def resolve_entity(
        self,
        value: str,
        *,
        kind: str | None = None,
        cutoff: datetime | str | None = None,
    ) -> dict[str, Any] | None:
        """Разрешает canonical ID или явно зарегистрированный alias без fuzzy matching."""
        clean = _clean_text(value, field_name="entity query", limit=500, required=True)
        if kind is not None and kind not in PROJECT_ROLES:
            raise ProductionContextValidationError(f"Неизвестный entity kind: {kind!r}")
        cutoff_dt = _parse_datetime(cutoff, field_name="cutoff") if cutoff is not None else None
        alias_key = normalize_identity_alias(clean)
        params: list[Any] = [clean, clean, alias_key]
        cutoff_clause = ""
        if cutoff_dt is not None:
            cutoff_clause = " AND a.known_at <= ?"
            params.append(cutoff_dt)
        kind_clause = ""
        if kind is not None:
            kind_clause = " AND e.kind = ?"
            params.append(kind)
        rows = self.conn.execute(
            f"""
            SELECT DISTINCT e.entity_id, e.kind, e.name
            FROM production_entities e
            LEFT JOIN production_entity_aliases a ON a.entity_id = e.entity_id
            WHERE (
                e.entity_id = ?
                OR lower(trim(e.name)) = lower(trim(?))
                OR (a.alias_key = ? {cutoff_clause})
            )
            {kind_clause}
            """,
            params,
        ).fetchall()
        return self._single_candidate(rows, label="Production entity", query=clean)

    def resolve_group(
        self,
        value: str,
        *,
        kind: str | None = None,
        cutoff: datetime | str | None = None,
    ) -> dict[str, Any] | None:
        clean = _clean_text(value, field_name="group query", limit=500, required=True)
        if kind is not None and kind not in GROUP_KINDS:
            raise ProductionContextValidationError(f"Неизвестный group kind: {kind!r}")
        cutoff_dt = _parse_datetime(cutoff, field_name="cutoff") if cutoff is not None else None
        alias_key = normalize_identity_alias(clean)
        params: list[Any] = [clean, clean, alias_key]
        cutoff_clause = ""
        if cutoff_dt is not None:
            cutoff_clause = " AND a.known_at <= ?"
            params.append(cutoff_dt)
        kind_clause = ""
        if kind is not None:
            kind_clause = " AND g.kind = ?"
            params.append(kind)
        rows = self.conn.execute(
            f"""
            SELECT DISTINCT g.group_id, g.kind, g.name
            FROM production_groups g
            LEFT JOIN production_group_aliases a ON a.group_id = g.group_id
            WHERE (
                g.group_id = ?
                OR lower(trim(g.name)) = lower(trim(?))
                OR (a.alias_key = ? {cutoff_clause})
            )
            {kind_clause}
            """,
            params,
        ).fetchall()
        return self._single_candidate(rows, label="Production group", query=clean)

    def _target_release_limit(self, project_id: str, cutoff_dt: datetime) -> datetime:
        row = self.conn.execute(
            "SELECT release_at FROM production_projects WHERE project_id = ?",
            [project_id],
        ).fetchone()
        release_at = row[0] if row else None
        if release_at is not None and release_at < cutoff_dt:
            return release_at
        return cutoff_dt

    def _visible_groups(self, project_id: str, cutoff_dt: datetime) -> dict[str, list[str]]:
        rows = self.conn.execute(
            """
            SELECT DISTINCT g.kind, g.group_id
            FROM project_group_links l
            JOIN production_groups g USING (group_id)
            WHERE l.project_id = ? AND l.known_at <= ?
            """,
            [project_id, cutoff_dt],
        ).fetchall()
        result = {kind: [] for kind in GROUP_KINDS}
        for kind, group_id in rows:
            result[str(kind)].append(str(group_id))

        # Backward-compatible fallback для bundle старого registry-контракта.
        if not any(result.values()):
            legacy = self.conn.execute(
                """
                SELECT franchise_id, shared_universe_id, identity_known_at
                FROM production_projects WHERE project_id = ?
                """,
                [project_id],
            ).fetchone()
            if legacy and legacy[2] <= cutoff_dt:
                if legacy[0]:
                    result["franchise"].append(str(legacy[0]))
                if legacy[1]:
                    result["shared_universe"].append(str(legacy[1]))
        return result

    def _prior_group_project_count(
        self,
        *,
        project_id: str,
        group_ids: list[str],
        cutoff_dt: datetime,
        release_limit: datetime,
        legacy_column: str,
    ) -> float:
        if not group_ids:
            return 0.0
        canonical = self.conn.execute(
            """
            SELECT COUNT(DISTINCT p.project_id)
            FROM production_projects p
            JOIN project_group_links l ON l.project_id = p.project_id
            WHERE p.project_id <> ?
              AND l.group_id IN (SELECT * FROM UNNEST(?))
              AND l.known_at <= ?
              AND p.release_at IS NOT NULL
              AND p.release_at <= ?
              AND p.release_at < ?
            """,
            [project_id, group_ids, cutoff_dt, cutoff_dt, release_limit],
        ).fetchone()[0]
        if canonical:
            return float(canonical)

        if legacy_column not in {"franchise_id", "shared_universe_id"}:
            return 0.0
        legacy = self.conn.execute(
            f"""
            SELECT COUNT(DISTINCT project_id)
            FROM production_projects
            WHERE project_id <> ?
              AND {legacy_column} IN (SELECT * FROM UNNEST(?))
              AND identity_known_at <= ?
              AND release_at IS NOT NULL
              AND release_at <= ?
              AND release_at < ?
            """,
            [project_id, group_ids, cutoff_dt, cutoff_dt, release_limit],
        ).fetchone()[0]
        return float(legacy or 0.0)

    def history_features_as_of(
        self,
        project_id: str,
        cutoff: datetime | str,
    ) -> dict[str, float]:
        """Считает только историю проектов, уже выпущенных и известных к cutoff."""
        self.store._require_project(project_id)
        cutoff_dt = _parse_datetime(cutoff, field_name="cutoff")
        release_limit = self._target_release_limit(project_id, cutoff_dt)
        groups = self._visible_groups(project_id, cutoff_dt)

        result: dict[str, float] = {
            "production_franchise_prior_project_count": self._prior_group_project_count(
                project_id=project_id,
                group_ids=groups["franchise"],
                cutoff_dt=cutoff_dt,
                release_limit=release_limit,
                legacy_column="franchise_id",
            ),
            "production_shared_universe_prior_project_count": self._prior_group_project_count(
                project_id=project_id,
                group_ids=groups["shared_universe"],
                cutoff_dt=cutoff_dt,
                release_limit=release_limit,
                legacy_column="shared_universe_id",
            ),
        }

        target_rows = self.conn.execute(
            """
            SELECT DISTINCT entity_id, role
            FROM project_entity_links
            WHERE project_id = ?
              AND known_at <= ?
              AND role IN (SELECT * FROM UNNEST(?))
            """,
            [project_id, cutoff_dt, list(HISTORY_ENTITY_ROLES)],
        ).fetchall()
        target_by_role: dict[str, list[str]] = {role: [] for role in HISTORY_ENTITY_ROLES}
        for entity_id, role in target_rows:
            target_by_role[str(role)].append(str(entity_id))

        all_current_entities: list[str] = []
        all_prior_counts: list[float] = []
        for role in HISTORY_ENTITY_ROLES:
            entity_ids = target_by_role[role]
            all_current_entities.extend(entity_ids)
            counts: list[float] = []
            for entity_id in entity_ids:
                count = self.conn.execute(
                    """
                    SELECT COUNT(DISTINCT p.project_id)
                    FROM production_projects p
                    JOIN project_entity_links l ON l.project_id = p.project_id
                    WHERE p.project_id <> ?
                      AND l.entity_id = ?
                      AND l.known_at <= ?
                      AND p.release_at IS NOT NULL
                      AND p.release_at <= ?
                      AND p.release_at < ?
                    """,
                    [project_id, entity_id, cutoff_dt, cutoff_dt, release_limit],
                ).fetchone()[0]
                counts.append(float(count or 0.0))
            all_prior_counts.extend(counts)
            result[f"production_{role}_history_known_ratio"] = (
                float(sum(1 for value in counts if value > 0)) / float(len(counts))
                if counts
                else 0.0
            )
            result[f"production_{role}_prior_project_count_mean"] = (
                float(statistics.fmean(counts)) if counts else 0.0
            )
            result[f"production_{role}_prior_project_count_max"] = float(max(counts, default=0.0))

        unique_current = sorted(set(all_current_entities))
        result["production_entity_history_known_ratio"] = (
            float(sum(1 for value in all_prior_counts if value > 0)) / float(len(all_prior_counts))
            if all_prior_counts
            else 0.0
        )
        if unique_current:
            overlap_rows = self.conn.execute(
                """
                WITH shared AS (
                    SELECT
                        p.project_id,
                        COUNT(DISTINCT l.entity_id) AS shared_entity_count
                    FROM production_projects p
                    JOIN project_entity_links l ON l.project_id = p.project_id
                    WHERE p.project_id <> ?
                      AND l.entity_id IN (SELECT * FROM UNNEST(?))
                      AND l.known_at <= ?
                      AND p.release_at IS NOT NULL
                      AND p.release_at <= ?
                      AND p.release_at < ?
                    GROUP BY p.project_id
                )
                SELECT
                    COALESCE(SUM(CASE WHEN shared_entity_count >= 1 THEN 1 ELSE 0 END), 0),
                    COALESCE(SUM(CASE WHEN shared_entity_count >= 2 THEN 1 ELSE 0 END), 0),
                    COALESCE(MAX(shared_entity_count), 0)
                FROM shared
                """,
                [project_id, unique_current, cutoff_dt, cutoff_dt, release_limit],
            ).fetchone()
            result["production_prior_shared_entity_project_count"] = float(overlap_rows[0] or 0.0)
            result["production_key_team_repeat_project_count"] = float(overlap_rows[1] or 0.0)
            result["production_prior_shared_entity_max"] = float(overlap_rows[2] or 0.0)
        else:
            result["production_prior_shared_entity_project_count"] = 0.0
            result["production_key_team_repeat_project_count"] = 0.0
            result["production_prior_shared_entity_max"] = 0.0

        return result
