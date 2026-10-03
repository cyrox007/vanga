from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

import duckdb

from settings import config


ENTITY_KINDS = {
    "studio",
    "production_company",
    "production_label",
    "producer",
    "creative_lead",
    "consultancy",
    "other",
}

PROJECT_ROLES = {
    "studio",
    "production_company",
    "production_label",
    "producer",
    "creative_lead",
    "consultancy",
    "other",
}

PRODUCTION_STAGES = {
    "development",
    "writing",
    "pre_production",
    "production",
    "post_production",
    "release",
    "unknown",
}

EVENT_TYPES = {
    "director_change",
    "writer_change",
    "creative_lead_change",
    "release_date_change",
    "rewrite",
    "reshoot",
    "recut",
    "format_change",
    "scope_change",
    "production_label_change",
    "other",
}

CONSULTANCY_SCOPES = {
    "story",
    "script",
    "character",
    "worldbuilding",
    "authenticity",
    "sensitivity",
    "other",
}


class ProductionContextValidationError(ValueError):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _clean_text(
    value: Any,
    *,
    field_name: str,
    limit: int,
    required: bool = False,
) -> str:
    text = " ".join(str(value or "").strip().split())
    if required and not text:
        raise ProductionContextValidationError(f"{field_name} не должен быть пустым")
    if len(text) > limit:
        raise ProductionContextValidationError(
            f"{field_name} длиннее допустимых {limit} символов"
        )
    return text


def _parse_datetime(value: Any, *, field_name: str, required: bool = True) -> datetime | None:
    if value in {None, ""}:
        if required:
            raise ProductionContextValidationError(f"{field_name} обязателен")
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value).strip().replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError as exc:
            raise ProductionContextValidationError(
                f"{field_name} должен быть ISO-датой/временем"
            ) from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _bounded_confidence(value: Any) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ProductionContextValidationError("confidence должен быть числом") from exc
    if not 0.0 <= parsed <= 1.0:
        raise ProductionContextValidationError("confidence должен быть в диапазоне 0..1")
    return parsed


class ProductionContextStore:
    """Фактический registry производства с temporal/provenance контрактом.

    Хранилище не является частью IMDb и не подключается к CatBoost напрямую.
    Любой будущий ML-признак должен вычисляться через ``features_as_of`` или
    эквивалентный temporal snapshot, после чего отдельно пройти ablation.
    """

    def __init__(self, path: str | Path | None = None) -> None:
        default_path = getattr(
            config,
            "PRODUCTION_CONTEXT_DB_PATH",
            str(Path(config.ABSPATH) / "production_context.duckdb"),
        )
        self.path = Path(path or default_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = duckdb.connect(str(self.path))
        self._ensure_schema()

    def close(self) -> None:
        self.conn.close()

    def _ensure_schema(self) -> None:
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS production_sources (
                source_id VARCHAR PRIMARY KEY,
                url VARCHAR NOT NULL,
                title VARCHAR,
                publisher VARCHAR,
                published_at TIMESTAMP WITH TIME ZONE,
                retrieved_at TIMESTAMP WITH TIME ZONE NOT NULL,
                confidence DOUBLE NOT NULL,
                note VARCHAR
            )
            """
        )
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS production_projects (
                project_id VARCHAR PRIMARY KEY,
                imdb_id VARCHAR,
                title VARCHAR NOT NULL,
                release_at TIMESTAMP WITH TIME ZONE,
                franchise_id VARCHAR,
                shared_universe_id VARCHAR,
                installment_index INTEGER,
                identity_known_at TIMESTAMP WITH TIME ZONE NOT NULL,
                updated_at TIMESTAMP WITH TIME ZONE NOT NULL
            )
            """
        )
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS production_entities (
                entity_id VARCHAR PRIMARY KEY,
                kind VARCHAR NOT NULL,
                name VARCHAR NOT NULL,
                external_id VARCHAR,
                created_at TIMESTAMP WITH TIME ZONE NOT NULL
            )
            """
        )
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS project_entity_links (
                link_id VARCHAR PRIMARY KEY,
                project_id VARCHAR NOT NULL,
                entity_id VARCHAR NOT NULL,
                role VARCHAR NOT NULL,
                stage VARCHAR NOT NULL,
                known_at TIMESTAMP WITH TIME ZONE NOT NULL,
                source_id VARCHAR NOT NULL,
                note VARCHAR
            )
            """
        )
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS production_events (
                event_id VARCHAR PRIMARY KEY,
                project_id VARCHAR NOT NULL,
                event_type VARCHAR NOT NULL,
                event_at TIMESTAMP WITH TIME ZONE,
                known_at TIMESTAMP WITH TIME ZONE NOT NULL,
                stage VARCHAR NOT NULL,
                source_id VARCHAR NOT NULL,
                details_json VARCHAR NOT NULL
            )
            """
        )
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS consultancy_engagements (
                engagement_id VARCHAR PRIMARY KEY,
                project_id VARCHAR NOT NULL,
                entity_id VARCHAR NOT NULL,
                scope VARCHAR NOT NULL,
                stage VARCHAR NOT NULL,
                known_at TIMESTAMP WITH TIME ZONE NOT NULL,
                source_id VARCHAR NOT NULL,
                note VARCHAR
            )
            """
        )
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_production_events_project_known ON production_events(project_id, known_at)"
        )
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_project_entities_project_known ON project_entity_links(project_id, known_at)"
        )
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_consultancies_project_known ON consultancy_engagements(project_id, known_at)"
        )

    def _require_source(self, source_id: str) -> None:
        row = self.conn.execute(
            "SELECT 1 FROM production_sources WHERE source_id = ? LIMIT 1",
            [source_id],
        ).fetchone()
        if row is None:
            raise ProductionContextValidationError(
                f"Неизвестный source_id: {source_id!r}; сначала добавьте provenance"
            )

    def _require_project(self, project_id: str) -> None:
        row = self.conn.execute(
            "SELECT 1 FROM production_projects WHERE project_id = ? LIMIT 1",
            [project_id],
        ).fetchone()
        if row is None:
            raise ProductionContextValidationError(f"Неизвестный project_id: {project_id!r}")

    def _require_entity(self, entity_id: str) -> None:
        row = self.conn.execute(
            "SELECT 1 FROM production_entities WHERE entity_id = ? LIMIT 1",
            [entity_id],
        ).fetchone()
        if row is None:
            raise ProductionContextValidationError(f"Неизвестный entity_id: {entity_id!r}")

    def upsert_source(self, payload: dict[str, Any]) -> str:
        source_id = _clean_text(
            payload.get("source_id") or uuid4(),
            field_name="source_id",
            limit=160,
            required=True,
        )
        url = _clean_text(payload.get("url"), field_name="url", limit=2000, required=True)
        title = _clean_text(payload.get("title"), field_name="title", limit=500) or None
        publisher = _clean_text(payload.get("publisher"), field_name="publisher", limit=300) or None
        published_at = _parse_datetime(
            payload.get("published_at"), field_name="published_at", required=False
        )
        retrieved_at = _parse_datetime(
            payload.get("retrieved_at") or _now(), field_name="retrieved_at"
        )
        confidence = _bounded_confidence(payload.get("confidence", 1.0))
        note = _clean_text(payload.get("note"), field_name="note", limit=2000) or None

        self.conn.execute(
            """
            INSERT INTO production_sources(
                source_id, url, title, publisher, published_at,
                retrieved_at, confidence, note
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source_id) DO UPDATE SET
                url = excluded.url,
                title = excluded.title,
                publisher = excluded.publisher,
                published_at = excluded.published_at,
                retrieved_at = excluded.retrieved_at,
                confidence = excluded.confidence,
                note = excluded.note
            """,
            [source_id, url, title, publisher, published_at, retrieved_at, confidence, note],
        )
        return source_id

    def upsert_project(self, payload: dict[str, Any]) -> str:
        project_id = _clean_text(
            payload.get("project_id") or payload.get("imdb_id") or uuid4(),
            field_name="project_id",
            limit=160,
            required=True,
        )
        imdb_id = _clean_text(payload.get("imdb_id"), field_name="imdb_id", limit=16) or None
        if imdb_id and (not imdb_id.startswith("tt") or not imdb_id[2:].isdigit()):
            raise ProductionContextValidationError("imdb_id должен иметь вид tt1234567")
        title = _clean_text(payload.get("title"), field_name="title", limit=500, required=True)
        release_at = _parse_datetime(payload.get("release_at"), field_name="release_at", required=False)
        franchise_id = _clean_text(
            payload.get("franchise_id"), field_name="franchise_id", limit=160
        ) or None
        shared_universe_id = _clean_text(
            payload.get("shared_universe_id"), field_name="shared_universe_id", limit=160
        ) or None
        installment_index = payload.get("installment_index")
        if installment_index not in {None, ""}:
            try:
                installment_index = int(installment_index)
            except (TypeError, ValueError) as exc:
                raise ProductionContextValidationError(
                    "installment_index должен быть целым числом"
                ) from exc
            if installment_index < 1:
                raise ProductionContextValidationError("installment_index должен быть >= 1")
        else:
            installment_index = None
        identity_known_at = _parse_datetime(
            payload.get("identity_known_at") or _now(), field_name="identity_known_at"
        )
        now = _now()
        self.conn.execute(
            """
            INSERT INTO production_projects(
                project_id, imdb_id, title, release_at, franchise_id,
                shared_universe_id, installment_index, identity_known_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(project_id) DO UPDATE SET
                imdb_id = excluded.imdb_id,
                title = excluded.title,
                release_at = excluded.release_at,
                franchise_id = excluded.franchise_id,
                shared_universe_id = excluded.shared_universe_id,
                installment_index = excluded.installment_index,
                identity_known_at = excluded.identity_known_at,
                updated_at = excluded.updated_at
            """,
            [
                project_id,
                imdb_id,
                title,
                release_at,
                franchise_id,
                shared_universe_id,
                installment_index,
                identity_known_at,
                now,
            ],
        )
        return project_id

    def upsert_entity(self, payload: dict[str, Any]) -> str:
        kind = str(payload.get("kind") or "").strip()
        if kind not in ENTITY_KINDS:
            raise ProductionContextValidationError(f"Неизвестный kind сущности: {kind!r}")
        entity_id = _clean_text(
            payload.get("entity_id") or uuid4(),
            field_name="entity_id",
            limit=160,
            required=True,
        )
        name = _clean_text(payload.get("name"), field_name="name", limit=500, required=True)
        external_id = _clean_text(
            payload.get("external_id"), field_name="external_id", limit=300
        ) or None
        self.conn.execute(
            """
            INSERT INTO production_entities(entity_id, kind, name, external_id, created_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(entity_id) DO UPDATE SET
                kind = excluded.kind,
                name = excluded.name,
                external_id = excluded.external_id
            """,
            [entity_id, kind, name, external_id, _now()],
        )
        return entity_id

    def link_entity(self, payload: dict[str, Any]) -> str:
        project_id = _clean_text(
            payload.get("project_id"), field_name="project_id", limit=160, required=True
        )
        entity_id = _clean_text(
            payload.get("entity_id"), field_name="entity_id", limit=160, required=True
        )
        source_id = _clean_text(
            payload.get("source_id"), field_name="source_id", limit=160, required=True
        )
        role = str(payload.get("role") or "").strip()
        stage = str(payload.get("stage") or "unknown").strip()
        if role not in PROJECT_ROLES:
            raise ProductionContextValidationError(f"Неизвестная role: {role!r}")
        if stage not in PRODUCTION_STAGES:
            raise ProductionContextValidationError(f"Неизвестный stage: {stage!r}")
        self._require_project(project_id)
        self._require_entity(entity_id)
        self._require_source(source_id)
        known_at = _parse_datetime(payload.get("known_at"), field_name="known_at")
        link_id = _clean_text(
            payload.get("link_id") or uuid4(), field_name="link_id", limit=160, required=True
        )
        note = _clean_text(payload.get("note"), field_name="note", limit=2000) or None
        self.conn.execute(
            """
            INSERT INTO project_entity_links(
                link_id, project_id, entity_id, role, stage, known_at, source_id, note
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(link_id) DO UPDATE SET
                project_id = excluded.project_id,
                entity_id = excluded.entity_id,
                role = excluded.role,
                stage = excluded.stage,
                known_at = excluded.known_at,
                source_id = excluded.source_id,
                note = excluded.note
            """,
            [link_id, project_id, entity_id, role, stage, known_at, source_id, note],
        )
        return link_id

    def add_event(self, payload: dict[str, Any]) -> str:
        project_id = _clean_text(
            payload.get("project_id"), field_name="project_id", limit=160, required=True
        )
        source_id = _clean_text(
            payload.get("source_id"), field_name="source_id", limit=160, required=True
        )
        event_type = str(payload.get("event_type") or "").strip()
        stage = str(payload.get("stage") or "unknown").strip()
        if event_type not in EVENT_TYPES:
            raise ProductionContextValidationError(f"Неизвестный event_type: {event_type!r}")
        if stage not in PRODUCTION_STAGES:
            raise ProductionContextValidationError(f"Неизвестный stage: {stage!r}")
        self._require_project(project_id)
        self._require_source(source_id)
        event_id = _clean_text(
            payload.get("event_id") or uuid4(), field_name="event_id", limit=160, required=True
        )
        event_at = _parse_datetime(payload.get("event_at"), field_name="event_at", required=False)
        known_at = _parse_datetime(payload.get("known_at"), field_name="known_at")
        details = payload.get("details") or {}
        if not isinstance(details, dict):
            raise ProductionContextValidationError("details должен быть JSON-объектом")
        details_json = json.dumps(details, ensure_ascii=False, sort_keys=True)
        if len(details_json) > 20_000:
            raise ProductionContextValidationError("details слишком большой")
        self.conn.execute(
            """
            INSERT INTO production_events(
                event_id, project_id, event_type, event_at, known_at,
                stage, source_id, details_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(event_id) DO UPDATE SET
                project_id = excluded.project_id,
                event_type = excluded.event_type,
                event_at = excluded.event_at,
                known_at = excluded.known_at,
                stage = excluded.stage,
                source_id = excluded.source_id,
                details_json = excluded.details_json
            """,
            [event_id, project_id, event_type, event_at, known_at, stage, source_id, details_json],
        )
        return event_id

    def add_consultancy(self, payload: dict[str, Any]) -> str:
        project_id = _clean_text(
            payload.get("project_id"), field_name="project_id", limit=160, required=True
        )
        entity_id = _clean_text(
            payload.get("entity_id"), field_name="entity_id", limit=160, required=True
        )
        source_id = _clean_text(
            payload.get("source_id"), field_name="source_id", limit=160, required=True
        )
        scope = str(payload.get("scope") or "").strip()
        stage = str(payload.get("stage") or "unknown").strip()
        if scope not in CONSULTANCY_SCOPES:
            raise ProductionContextValidationError(f"Неизвестный consultancy scope: {scope!r}")
        if stage not in PRODUCTION_STAGES:
            raise ProductionContextValidationError(f"Неизвестный stage: {stage!r}")
        self._require_project(project_id)
        self._require_entity(entity_id)
        self._require_source(source_id)
        entity_kind = self.conn.execute(
            "SELECT kind FROM production_entities WHERE entity_id = ?",
            [entity_id],
        ).fetchone()[0]
        if entity_kind != "consultancy":
            raise ProductionContextValidationError(
                "Для consultancy engagement entity.kind должен быть consultancy"
            )
        engagement_id = _clean_text(
            payload.get("engagement_id") or uuid4(),
            field_name="engagement_id",
            limit=160,
            required=True,
        )
        known_at = _parse_datetime(payload.get("known_at"), field_name="known_at")
        note = _clean_text(payload.get("note"), field_name="note", limit=2000) or None
        self.conn.execute(
            """
            INSERT INTO consultancy_engagements(
                engagement_id, project_id, entity_id, scope, stage,
                known_at, source_id, note
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(engagement_id) DO UPDATE SET
                project_id = excluded.project_id,
                entity_id = excluded.entity_id,
                scope = excluded.scope,
                stage = excluded.stage,
                known_at = excluded.known_at,
                source_id = excluded.source_id,
                note = excluded.note
            """,
            [engagement_id, project_id, entity_id, scope, stage, known_at, source_id, note],
        )
        return engagement_id

    def features_as_of(self, project_id: str, cutoff: datetime | str) -> dict[str, float]:
        """Возвращает нейтральные pre-release proxy только из известных к cutoff фактов."""
        self._require_project(project_id)
        cutoff_dt = _parse_datetime(cutoff, field_name="cutoff")
        project = self.conn.execute(
            """
            SELECT franchise_id, shared_universe_id, installment_index, identity_known_at
            FROM production_projects WHERE project_id = ?
            """,
            [project_id],
        ).fetchone()
        identity_visible = bool(project and project[3] <= cutoff_dt)

        result: dict[str, float] = {
            "production_franchise_known": 1.0 if identity_visible and project[0] else 0.0,
            "production_shared_universe_known": 1.0 if identity_visible and project[1] else 0.0,
            "production_installment_index": (
                float(project[2]) if identity_visible and project[2] is not None else 0.0
            ),
        }

        role_rows = self.conn.execute(
            """
            SELECT role, COUNT(DISTINCT entity_id)
            FROM project_entity_links
            WHERE project_id = ? AND known_at <= ?
            GROUP BY role
            """,
            [project_id, cutoff_dt],
        ).fetchall()
        role_counts = {str(role): float(count) for role, count in role_rows}
        for role in (
            "studio",
            "production_company",
            "production_label",
            "producer",
            "creative_lead",
        ):
            result[f"production_{role}_count"] = role_counts.get(role, 0.0)

        event_rows = self.conn.execute(
            """
            SELECT event_type, COUNT(*)
            FROM production_events
            WHERE project_id = ? AND known_at <= ?
            GROUP BY event_type
            """,
            [project_id, cutoff_dt],
        ).fetchall()
        event_counts = {str(kind): float(count) for kind, count in event_rows}
        result["production_change_count"] = float(sum(event_counts.values()))
        for event_type in sorted(EVENT_TYPES - {"other"}):
            result[f"production_{event_type}_count"] = event_counts.get(event_type, 0.0)

        consultancy_rows = self.conn.execute(
            """
            SELECT scope, COUNT(*)
            FROM consultancy_engagements
            WHERE project_id = ? AND known_at <= ?
            GROUP BY scope
            """,
            [project_id, cutoff_dt],
        ).fetchall()
        consultancy_counts = {str(scope): float(count) for scope, count in consultancy_rows}
        result["production_consultancy_count"] = float(sum(consultancy_counts.values()))
        result["production_external_consultancy_present"] = (
            1.0 if result["production_consultancy_count"] > 0 else 0.0
        )
        for scope in sorted(CONSULTANCY_SCOPES - {"other"}):
            result[f"production_consultancy_{scope}_count"] = consultancy_counts.get(scope, 0.0)

        return result

    def timeline_as_of(self, project_id: str, cutoff: datetime | str) -> list[dict[str, Any]]:
        """Возвращает доказуемую временную линию без фактов, неизвестных на cutoff."""
        self._require_project(project_id)
        cutoff_dt = _parse_datetime(cutoff, field_name="cutoff")
        rows = self.conn.execute(
            """
            SELECT
                e.event_id, e.event_type, e.event_at, e.known_at,
                e.stage, e.source_id, e.details_json,
                s.url, s.title, s.publisher, s.confidence
            FROM production_events e
            JOIN production_sources s USING (source_id)
            WHERE e.project_id = ? AND e.known_at <= ?
            ORDER BY e.known_at, e.event_id
            """,
            [project_id, cutoff_dt],
        ).fetchall()
        return [
            {
                "event_id": row[0],
                "event_type": row[1],
                "event_at": row[2].isoformat() if row[2] else None,
                "known_at": row[3].isoformat(),
                "stage": row[4],
                "source_id": row[5],
                "details": json.loads(row[6]),
                "source": {
                    "url": row[7],
                    "title": row[8],
                    "publisher": row[9],
                    "confidence": float(row[10]),
                },
            }
            for row in rows
        ]
