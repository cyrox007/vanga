from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

import duckdb

from settings import config


USAGE_BASES = {"public_aggregate", "licensed", "first_party", "manual_reference", "public_record"}
PROJECT_STATUSES = {
    "announced",
    "pre_production",
    "filming",
    "post_production",
    "scheduled",
    "delayed",
    "cancelled",
    "released",
    "unknown",
}
PERSON_ROLES = {"director", "writer", "actor"}
ENTITY_KINDS = {
    "source_work",
    "franchise",
    "shared_universe",
    "production_label",
    "studio",
    "production_company",
}
ENTITY_RELATIONS = {
    "based_on",
    "part_of_franchise",
    "part_of_shared_universe",
    "produced_by",
    "label",
    "other",
}
RELEASE_PRECISIONS = {"exact", "month", "quarter", "year", "window"}


class FutureReleaseError(ValueError):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _clean(value: Any, *, field_name: str, limit: int = 500, required: bool = False) -> str:
    text = " ".join(str(value or "").strip().split())
    if required and not text:
        raise FutureReleaseError(f"{field_name} не должен быть пустым")
    if len(text) > limit:
        raise FutureReleaseError(f"{field_name} длиннее допустимых {limit} символов")
    return text


def _dt(value: Any, *, field_name: str, required: bool = True) -> datetime | None:
    if value in {None, ""}:
        if required:
            raise FutureReleaseError(f"{field_name} обязателен")
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
        except ValueError as exc:
            raise FutureReleaseError(f"{field_name} должен быть ISO-8601 datetime") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _confidence(value: Any) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise FutureReleaseError("confidence должен быть числом") from exc
    if not 0.0 <= result <= 1.0:
        raise FutureReleaseError("confidence должен быть в диапазоне 0..1")
    return result


def _normalize_alias(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).casefold().strip()
    text = re.sub(r"[^\w]+", " ", text, flags=re.UNICODE)
    return " ".join(text.split())


def _fingerprint(payload: Any) -> str:
    raw = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class FutureReleaseStore:
    """Локальный P9 registry будущих релизов с temporal provenance.

    Registry намеренно ничего не запрашивает из сети. Collector/enrichment может
    работать отдельно, а prediction/catalog читают только сохранённый cache.
    """

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path or config.FUTURE_RELEASE_DB_PATH)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = duckdb.connect(str(self.path))
        self.conn.execute("SET threads = 1")
        # TIMESTAMPTZ DuckDB отображает в timezone текущего соединения. Без
        # явного UTC один и тот же момент сериализовался как +00:00 или +03:00
        # в зависимости от окружения и менял API/fingerprint.
        self.conn.execute("SET TimeZone = 'UTC'")
        self._ensure_schema()

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "FutureReleaseStore":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def _ensure_schema(self) -> None:
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS future_release_sources(
                source_id VARCHAR PRIMARY KEY,
                provider VARCHAR NOT NULL,
                url VARCHAR,
                usage_basis VARCHAR NOT NULL,
                retrieved_at TIMESTAMPTZ NOT NULL
            )
            """
        )
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS future_release_projects(
                project_id VARCHAR PRIMARY KEY,
                imdb_id VARCHAR,
                wikidata_id VARCHAR,
                canonical_title VARCHAR NOT NULL,
                created_at TIMESTAMPTZ NOT NULL,
                updated_at TIMESTAMPTZ NOT NULL
            )
            """
        )
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS future_release_aliases(
                alias_id VARCHAR PRIMARY KEY,
                project_id VARCHAR NOT NULL,
                alias VARCHAR NOT NULL,
                normalized_alias VARCHAR NOT NULL,
                known_at TIMESTAMPTZ NOT NULL,
                source_id VARCHAR NOT NULL,
                confidence DOUBLE NOT NULL
            )
            """
        )
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS future_release_windows(
                observation_id VARCHAR PRIMARY KEY,
                project_id VARCHAR NOT NULL,
                territory VARCHAR NOT NULL,
                release_start_at TIMESTAMPTZ NOT NULL,
                release_end_at TIMESTAMPTZ NOT NULL,
                precision VARCHAR NOT NULL,
                known_at TIMESTAMPTZ NOT NULL,
                source_id VARCHAR NOT NULL,
                confidence DOUBLE NOT NULL
            )
            """
        )
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS future_release_statuses(
                observation_id VARCHAR PRIMARY KEY,
                project_id VARCHAR NOT NULL,
                status VARCHAR NOT NULL,
                known_at TIMESTAMPTZ NOT NULL,
                source_id VARCHAR NOT NULL,
                confidence DOUBLE NOT NULL
            )
            """
        )
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS future_release_people(
                person_id VARCHAR PRIMARY KEY,
                imdb_id VARCHAR,
                wikidata_id VARCHAR,
                canonical_name VARCHAR NOT NULL,
                created_at TIMESTAMPTZ NOT NULL,
                updated_at TIMESTAMPTZ NOT NULL
            )
            """
        )
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS future_release_project_people(
                link_id VARCHAR PRIMARY KEY,
                project_id VARCHAR NOT NULL,
                person_id VARCHAR NOT NULL,
                role VARCHAR NOT NULL,
                billing_order INTEGER,
                known_at TIMESTAMPTZ NOT NULL,
                source_id VARCHAR NOT NULL,
                confidence DOUBLE NOT NULL
            )
            """
        )
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS future_release_entities(
                entity_id VARCHAR PRIMARY KEY,
                kind VARCHAR NOT NULL,
                canonical_name VARCHAR NOT NULL,
                external_id VARCHAR,
                created_at TIMESTAMPTZ NOT NULL,
                updated_at TIMESTAMPTZ NOT NULL
            )
            """
        )
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS future_release_project_entities(
                link_id VARCHAR PRIMARY KEY,
                project_id VARCHAR NOT NULL,
                entity_id VARCHAR NOT NULL,
                relation_type VARCHAR NOT NULL,
                known_at TIMESTAMPTZ NOT NULL,
                source_id VARCHAR NOT NULL,
                confidence DOUBLE NOT NULL
            )
            """
        )
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_future_release_window_known ON future_release_windows(project_id, known_at)"
        )
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_future_release_people_known ON future_release_project_people(project_id, known_at)"
        )
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_future_release_entity_known ON future_release_project_entities(project_id, known_at)"
        )
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_future_release_alias_norm ON future_release_aliases(normalized_alias, known_at)"
        )

    def _require(self, table: str, key: str, value: str) -> None:
        row = self.conn.execute(
            f"SELECT 1 FROM {table} WHERE {key} = ? LIMIT 1", [value]
        ).fetchone()
        if row is None:
            raise FutureReleaseError(f"Неизвестный {key}: {value}")

    def upsert_source(self, payload: dict[str, Any]) -> str:
        source_id = _clean(
            payload.get("source_id") or uuid4(),
            field_name="source_id",
            limit=160,
            required=True,
        )
        provider = _clean(
            payload.get("provider"), field_name="provider", limit=200, required=True
        )
        url = _clean(payload.get("url"), field_name="url", limit=2000) or None
        usage_basis = _clean(
            payload.get("usage_basis"),
            field_name="usage_basis",
            limit=80,
            required=True,
        )
        if usage_basis not in USAGE_BASES:
            raise FutureReleaseError(f"Неизвестный usage_basis: {usage_basis!r}")
        retrieved_at = _dt(
            payload.get("retrieved_at") or _now(), field_name="retrieved_at"
        )

        existing = self.conn.execute(
            """
            SELECT provider, url, usage_basis, retrieved_at
            FROM future_release_sources WHERE source_id = ?
            """,
            [source_id],
        ).fetchone()
        if existing is not None:
            existing_retrieved = existing[3].astimezone(timezone.utc)
            if (
                str(existing[0]) != provider
                or (str(existing[1]) if existing[1] is not None else None) != url
                or str(existing[2]) != usage_basis
                or existing_retrieved != retrieved_at
            ):
                raise FutureReleaseError(
                    f"source_id={source_id} уже существует с другим immutable snapshot"
                )
            return source_id

        self.conn.execute(
            "INSERT INTO future_release_sources VALUES (?, ?, ?, ?, ?)",
            [source_id, provider, url, usage_basis, retrieved_at],
        )
        return source_id

    def upsert_project(self, payload: dict[str, Any]) -> str:
        project_id = _clean(
            payload.get("project_id") or uuid4(),
            field_name="project_id",
            limit=160,
            required=True,
        )
        imdb_id = _clean(payload.get("imdb_id"), field_name="imdb_id", limit=32) or None
        if imdb_id and (not imdb_id.startswith("tt") or not imdb_id[2:].isdigit()):
            raise FutureReleaseError(f"Некорректный IMDb id: {imdb_id!r}")
        wikidata_id = _clean(
            payload.get("wikidata_id"), field_name="wikidata_id", limit=32
        ) or None
        if wikidata_id and (not wikidata_id.startswith("Q") or not wikidata_id[1:].isdigit()):
            raise FutureReleaseError(f"Некорректный Wikidata id: {wikidata_id!r}")
        title = _clean(
            payload.get("canonical_title"),
            field_name="canonical_title",
            limit=500,
            required=True,
        )
        now = _now()
        self.conn.execute(
            """
            INSERT INTO future_release_projects VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(project_id) DO UPDATE SET
                imdb_id=excluded.imdb_id,
                wikidata_id=excluded.wikidata_id,
                canonical_title=excluded.canonical_title,
                updated_at=excluded.updated_at
            """,
            [project_id, imdb_id, wikidata_id, title, now, now],
        )
        return project_id

    def add_alias(self, payload: dict[str, Any]) -> str:
        project_id = _clean(
            payload.get("project_id"), field_name="project_id", limit=160, required=True
        )
        source_id = _clean(
            payload.get("source_id"), field_name="source_id", limit=160, required=True
        )
        self._require("future_release_projects", "project_id", project_id)
        self._require("future_release_sources", "source_id", source_id)
        alias = _clean(payload.get("alias"), field_name="alias", limit=500, required=True)
        normalized = _normalize_alias(alias)
        if not normalized:
            raise FutureReleaseError("alias после нормализации не должен быть пустым")
        alias_id = _clean(
            payload.get("alias_id") or uuid4(),
            field_name="alias_id",
            limit=180,
            required=True,
        )
        known_at = _dt(payload.get("known_at"), field_name="known_at")
        confidence = _confidence(payload.get("confidence", 1.0))
        existing = self.conn.execute(
            """
            SELECT project_id, normalized_alias, known_at, source_id, confidence
            FROM future_release_aliases WHERE alias_id = ?
            """,
            [alias_id],
        ).fetchone()
        if existing is not None:
            if str(existing[0]) != project_id or str(existing[1]) != normalized:
                raise FutureReleaseError(
                    f"alias_id={alias_id} уже принадлежит другому logical alias"
                )
            # Stable logical alias сохраняет первое известное наблюдение. Если
            # более раннее evidence импортируется задним числом, двигаем first-known
            # назад; более поздний повтор не переписывает provenance прошлого.
            if known_at < existing[2].astimezone(timezone.utc):
                self.conn.execute(
                    """
                    UPDATE future_release_aliases
                    SET alias = ?, known_at = ?, source_id = ?, confidence = ?
                    WHERE alias_id = ?
                    """,
                    [alias, known_at, source_id, confidence, alias_id],
                )
            return alias_id

        self.conn.execute(
            """
            INSERT INTO future_release_aliases VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                alias_id,
                project_id,
                alias,
                normalized,
                known_at,
                source_id,
                confidence,
            ],
        )
        return alias_id

    def add_release_window(self, payload: dict[str, Any]) -> str:
        project_id = _clean(
            payload.get("project_id"), field_name="project_id", limit=160, required=True
        )
        source_id = _clean(
            payload.get("source_id"), field_name="source_id", limit=160, required=True
        )
        self._require("future_release_projects", "project_id", project_id)
        self._require("future_release_sources", "source_id", source_id)
        start = _dt(payload.get("release_start_at"), field_name="release_start_at")
        end = _dt(payload.get("release_end_at") or start, field_name="release_end_at")
        if end < start:
            raise FutureReleaseError("release_end_at не может быть раньше release_start_at")
        precision = _clean(
            payload.get("precision") or "exact",
            field_name="precision",
            limit=40,
            required=True,
        )
        if precision not in RELEASE_PRECISIONS:
            raise FutureReleaseError(f"Неизвестный release precision: {precision!r}")
        if precision == "exact" and end != start:
            raise FutureReleaseError("precision=exact требует release_start_at == release_end_at")
        territory = _clean(
            payload.get("territory") or "worldwide",
            field_name="territory",
            limit=80,
            required=True,
        ).casefold()
        observation_id = _clean(
            payload.get("observation_id") or uuid4(),
            field_name="observation_id",
            limit=180,
            required=True,
        )
        self.conn.execute(
            """
            INSERT INTO future_release_windows VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                observation_id,
                project_id,
                territory,
                start,
                end,
                precision,
                _dt(payload.get("known_at"), field_name="known_at"),
                source_id,
                _confidence(payload.get("confidence", 1.0)),
            ],
        )
        return observation_id

    def add_status(self, payload: dict[str, Any]) -> str:
        project_id = _clean(
            payload.get("project_id"), field_name="project_id", limit=160, required=True
        )
        source_id = _clean(
            payload.get("source_id"), field_name="source_id", limit=160, required=True
        )
        self._require("future_release_projects", "project_id", project_id)
        self._require("future_release_sources", "source_id", source_id)
        status = _clean(
            payload.get("status"), field_name="status", limit=80, required=True
        )
        if status not in PROJECT_STATUSES:
            raise FutureReleaseError(f"Неизвестный project status: {status!r}")
        observation_id = _clean(
            payload.get("observation_id") or uuid4(),
            field_name="observation_id",
            limit=180,
            required=True,
        )
        self.conn.execute(
            """
            INSERT INTO future_release_statuses VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                observation_id,
                project_id,
                status,
                _dt(payload.get("known_at"), field_name="known_at"),
                source_id,
                _confidence(payload.get("confidence", 1.0)),
            ],
        )
        return observation_id

    def upsert_person(self, payload: dict[str, Any]) -> str:
        person_id = _clean(
            payload.get("person_id") or uuid4(),
            field_name="person_id",
            limit=160,
            required=True,
        )
        imdb_id = _clean(payload.get("imdb_id"), field_name="imdb_id", limit=32) or None
        if imdb_id and (not imdb_id.startswith("nm") or not imdb_id[2:].isdigit()):
            raise FutureReleaseError(f"Некорректный IMDb person id: {imdb_id!r}")
        wikidata_id = _clean(
            payload.get("wikidata_id"), field_name="wikidata_id", limit=32
        ) or None
        name = _clean(
            payload.get("canonical_name"),
            field_name="canonical_name",
            limit=300,
            required=True,
        )
        now = _now()
        self.conn.execute(
            """
            INSERT INTO future_release_people VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(person_id) DO UPDATE SET
                imdb_id=excluded.imdb_id,
                wikidata_id=excluded.wikidata_id,
                canonical_name=excluded.canonical_name,
                updated_at=excluded.updated_at
            """,
            [person_id, imdb_id, wikidata_id, name, now, now],
        )
        return person_id

    def link_person(self, payload: dict[str, Any]) -> str:
        project_id = _clean(
            payload.get("project_id"), field_name="project_id", limit=160, required=True
        )
        person_id = _clean(
            payload.get("person_id"), field_name="person_id", limit=160, required=True
        )
        source_id = _clean(
            payload.get("source_id"), field_name="source_id", limit=160, required=True
        )
        self._require("future_release_projects", "project_id", project_id)
        self._require("future_release_people", "person_id", person_id)
        self._require("future_release_sources", "source_id", source_id)
        role = _clean(payload.get("role"), field_name="role", limit=40, required=True)
        if role not in PERSON_ROLES:
            raise FutureReleaseError(f"Неизвестная person role: {role!r}")
        billing_order = payload.get("billing_order")
        if billing_order is not None:
            try:
                billing_order = int(billing_order)
            except (TypeError, ValueError) as exc:
                raise FutureReleaseError("billing_order должен быть целым") from exc
            if billing_order < 0:
                raise FutureReleaseError("billing_order должен быть >= 0")
        link_id = _clean(
            payload.get("link_id") or uuid4(), field_name="link_id", limit=180, required=True
        )
        self.conn.execute(
            """
            INSERT INTO future_release_project_people VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                link_id,
                project_id,
                person_id,
                role,
                billing_order,
                _dt(payload.get("known_at"), field_name="known_at"),
                source_id,
                _confidence(payload.get("confidence", 1.0)),
            ],
        )
        return link_id

    def upsert_entity(self, payload: dict[str, Any]) -> str:
        entity_id = _clean(
            payload.get("entity_id") or uuid4(),
            field_name="entity_id",
            limit=160,
            required=True,
        )
        kind = _clean(payload.get("kind"), field_name="kind", limit=80, required=True)
        if kind not in ENTITY_KINDS:
            raise FutureReleaseError(f"Неизвестный entity kind: {kind!r}")
        name = _clean(
            payload.get("canonical_name"),
            field_name="canonical_name",
            limit=500,
            required=True,
        )
        external_id = _clean(
            payload.get("external_id"), field_name="external_id", limit=160
        ) or None
        now = _now()
        self.conn.execute(
            """
            INSERT INTO future_release_entities VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(entity_id) DO UPDATE SET
                kind=excluded.kind,
                canonical_name=excluded.canonical_name,
                external_id=excluded.external_id,
                updated_at=excluded.updated_at
            """,
            [entity_id, kind, name, external_id, now, now],
        )
        return entity_id

    def link_entity(self, payload: dict[str, Any]) -> str:
        project_id = _clean(
            payload.get("project_id"), field_name="project_id", limit=160, required=True
        )
        entity_id = _clean(
            payload.get("entity_id"), field_name="entity_id", limit=160, required=True
        )
        source_id = _clean(
            payload.get("source_id"), field_name="source_id", limit=160, required=True
        )
        self._require("future_release_projects", "project_id", project_id)
        self._require("future_release_entities", "entity_id", entity_id)
        self._require("future_release_sources", "source_id", source_id)
        relation = _clean(
            payload.get("relation_type"),
            field_name="relation_type",
            limit=80,
            required=True,
        )
        if relation not in ENTITY_RELATIONS:
            raise FutureReleaseError(f"Неизвестный entity relation: {relation!r}")
        link_id = _clean(
            payload.get("link_id") or uuid4(), field_name="link_id", limit=180, required=True
        )
        self.conn.execute(
            """
            INSERT INTO future_release_project_entities VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                link_id,
                project_id,
                entity_id,
                relation,
                _dt(payload.get("known_at"), field_name="known_at"),
                source_id,
                _confidence(payload.get("confidence", 1.0)),
            ],
        )
        return link_id

    def resolve_title_as_of(self, title: str, cutoff: Any) -> dict[str, Any] | None:
        normalized = _normalize_alias(title)
        if not normalized:
            return None
        cutoff_dt = _dt(cutoff, field_name="cutoff")
        rows = self.conn.execute(
            """
            SELECT DISTINCT a.project_id, p.canonical_title, p.imdb_id, p.wikidata_id
            FROM future_release_aliases a
            JOIN future_release_projects p USING(project_id)
            WHERE a.normalized_alias = ? AND a.known_at <= ?
            ORDER BY a.project_id
            """,
            [normalized, cutoff_dt],
        ).fetchall()
        if not rows:
            return None
        if len(rows) > 1:
            raise FutureReleaseError(
                "Alias неоднозначен: " + ", ".join(str(row[0]) for row in rows)
            )
        row = rows[0]
        return {
            "project_id": str(row[0]),
            "canonical_title": str(row[1]),
            "imdb_id": str(row[2]) if row[2] else None,
            "wikidata_id": str(row[3]) if row[3] else None,
        }

    def _latest_source_rows(self, project_id: str, cutoff: datetime, *, table: str, partition: str) -> list[tuple]:
        self._require("future_release_projects", "project_id", project_id)
        return self.conn.execute(
            f"""
            SELECT * EXCLUDE(rn) FROM (
                SELECT *, ROW_NUMBER() OVER (
                    PARTITION BY {partition}
                    ORDER BY known_at DESC, observation_id DESC
                ) AS rn
                FROM {table}
                WHERE project_id = ? AND known_at <= ?
            ) ranked
            WHERE rn = 1
            ORDER BY source_id
            """,
            [project_id, cutoff],
        ).fetchall()

    def release_candidates_as_of(self, project_id: str, cutoff: Any, *, territory: str = "worldwide") -> list[dict[str, Any]]:
        cutoff_dt = _dt(cutoff, field_name="cutoff")
        territory = _clean(territory, field_name="territory", limit=80, required=True).casefold()
        rows = self._latest_source_rows(
            project_id,
            cutoff_dt,
            table="future_release_windows",
            partition="source_id, territory",
        )
        grouped: dict[tuple[str, str, str], dict[str, Any]] = {}
        for row in rows:
            if str(row[2]) != territory:
                continue
            key = (row[3].isoformat(), row[4].isoformat(), str(row[5]))
            item = grouped.setdefault(
                key,
                {
                    "release_start_at": row[3].isoformat(),
                    "release_end_at": row[4].isoformat(),
                    "precision": str(row[5]),
                    "territory": territory,
                    "evidence": [],
                },
            )
            item["evidence"].append(
                {
                    "observation_id": str(row[0]),
                    "known_at": row[6].isoformat(),
                    "source_id": str(row[7]),
                    "confidence": float(row[8]),
                }
            )
        result = list(grouped.values())
        for item in result:
            item["max_confidence"] = max(x["confidence"] for x in item["evidence"])
            item["source_count"] = len(item["evidence"])
        result.sort(
            key=lambda item: (
                item["release_start_at"],
                item["release_end_at"],
                item["precision"],
            )
        )
        return result

    def status_candidates_as_of(self, project_id: str, cutoff: Any) -> list[dict[str, Any]]:
        cutoff_dt = _dt(cutoff, field_name="cutoff")
        rows = self._latest_source_rows(
            project_id,
            cutoff_dt,
            table="future_release_statuses",
            partition="source_id",
        )
        grouped: dict[str, dict[str, Any]] = {}
        for row in rows:
            status = str(row[2])
            item = grouped.setdefault(status, {"status": status, "evidence": []})
            item["evidence"].append(
                {
                    "observation_id": str(row[0]),
                    "known_at": row[3].isoformat(),
                    "source_id": str(row[4]),
                    "confidence": float(row[5]),
                }
            )
        result = list(grouped.values())
        for item in result:
            item["max_confidence"] = max(x["confidence"] for x in item["evidence"])
            item["source_count"] = len(item["evidence"])
        result.sort(key=lambda item: item["status"])
        return result

    def people_as_of(self, project_id: str, cutoff: Any) -> list[dict[str, Any]]:
        self._require("future_release_projects", "project_id", project_id)
        cutoff_dt = _dt(cutoff, field_name="cutoff")
        rows = self.conn.execute(
            """
            SELECT l.person_id, p.canonical_name, p.imdb_id, p.wikidata_id,
                   l.role, l.billing_order, MIN(l.known_at) AS first_known_at,
                   MAX(l.confidence) AS max_confidence,
                   LIST(DISTINCT l.source_id ORDER BY l.source_id) AS source_ids
            FROM future_release_project_people l
            JOIN future_release_people p USING(person_id)
            WHERE l.project_id = ? AND l.known_at <= ?
            GROUP BY l.person_id, p.canonical_name, p.imdb_id, p.wikidata_id,
                     l.role, l.billing_order
            ORDER BY l.role, COALESCE(l.billing_order, 2147483647), l.person_id
            """,
            [project_id, cutoff_dt],
        ).fetchall()
        return [
            {
                "person_id": str(row[0]),
                "canonical_name": str(row[1]),
                "imdb_id": str(row[2]) if row[2] else None,
                "wikidata_id": str(row[3]) if row[3] else None,
                "role": str(row[4]),
                "billing_order": int(row[5]) if row[5] is not None else None,
                "first_known_at": row[6].isoformat(),
                "max_confidence": float(row[7]),
                "source_ids": list(row[8] or []),
            }
            for row in rows
        ]

    def entities_as_of(self, project_id: str, cutoff: Any) -> list[dict[str, Any]]:
        self._require("future_release_projects", "project_id", project_id)
        cutoff_dt = _dt(cutoff, field_name="cutoff")
        rows = self.conn.execute(
            """
            SELECT l.entity_id, e.kind, e.canonical_name, e.external_id,
                   l.relation_type, MIN(l.known_at) AS first_known_at,
                   MAX(l.confidence) AS max_confidence,
                   LIST(DISTINCT l.source_id ORDER BY l.source_id) AS source_ids
            FROM future_release_project_entities l
            JOIN future_release_entities e USING(entity_id)
            WHERE l.project_id = ? AND l.known_at <= ?
            GROUP BY l.entity_id, e.kind, e.canonical_name, e.external_id,
                     l.relation_type
            ORDER BY e.kind, l.entity_id, l.relation_type
            """,
            [project_id, cutoff_dt],
        ).fetchall()
        return [
            {
                "entity_id": str(row[0]),
                "kind": str(row[1]),
                "canonical_name": str(row[2]),
                "external_id": str(row[3]) if row[3] is not None else None,
                "relation_type": str(row[4]),
                "first_known_at": row[5].isoformat(),
                "max_confidence": float(row[6]),
                "source_ids": list(row[7] or []),
            }
            for row in rows
        ]

    def snapshot_as_of(self, project_id: str, cutoff: Any, *, territory: str = "worldwide") -> dict[str, Any]:
        cutoff_dt = _dt(cutoff, field_name="cutoff")
        row = self.conn.execute(
            """
            SELECT imdb_id, wikidata_id, canonical_title
            FROM future_release_projects WHERE project_id = ?
            """,
            [project_id],
        ).fetchone()
        if row is None:
            raise FutureReleaseError(f"Неизвестный project_id: {project_id}")
        releases = self.release_candidates_as_of(project_id, cutoff_dt, territory=territory)
        statuses = self.status_candidates_as_of(project_id, cutoff_dt)
        people = self.people_as_of(project_id, cutoff_dt)
        entities = self.entities_as_of(project_id, cutoff_dt)
        release_conflict = len(releases) > 1
        status_conflict = len(statuses) > 1
        resolved_release = releases[0] if len(releases) == 1 else None
        resolved_status = statuses[0]["status"] if len(statuses) == 1 else None
        directors = [item for item in people if item["role"] == "director"]
        writers = [item for item in people if item["role"] == "writer"]
        cast = [item for item in people if item["role"] == "actor"]
        return {
            "project_id": project_id,
            "imdb_id": str(row[0]) if row[0] else None,
            "wikidata_id": str(row[1]) if row[1] else None,
            "canonical_title": str(row[2]),
            "cutoff_at": cutoff_dt.isoformat(),
            "territory": territory.casefold(),
            "release_date_conflict": release_conflict,
            "release_candidates": releases,
            "release_window": resolved_release,
            "release_at": (
                resolved_release["release_start_at"]
                if resolved_release and resolved_release["precision"] == "exact"
                else None
            ),
            "status_conflict": status_conflict,
            "status_candidates": statuses,
            "status": resolved_status,
            "directors": directors,
            "writers": writers,
            "cast": cast,
            "entities": entities,
            "network_required_for_inference": False,
        }

    def catalog_as_of(
        self,
        cutoff: Any,
        *,
        territory: str = "worldwide",
        from_at: Any | None = None,
        to_at: Any | None = None,
        include_conflicts: bool = True,
    ) -> dict[str, Any]:
        cutoff_dt = _dt(cutoff, field_name="cutoff")
        from_dt = _dt(from_at, field_name="from_at", required=False) or cutoff_dt
        to_dt = _dt(to_at, field_name="to_at", required=False)
        project_ids = [
            str(row[0])
            for row in self.conn.execute(
                "SELECT project_id FROM future_release_projects ORDER BY project_id"
            ).fetchall()
        ]
        items: list[dict[str, Any]] = []
        for project_id in project_ids:
            snapshot = self.snapshot_as_of(project_id, cutoff_dt, territory=territory)
            candidates = snapshot["release_candidates"]
            if not candidates:
                continue
            if snapshot["release_date_conflict"] and not include_conflicts:
                continue
            overlaps = False
            for candidate in candidates:
                start = _dt(candidate["release_start_at"], field_name="release_start_at")
                end = _dt(candidate["release_end_at"], field_name="release_end_at")
                if end < from_dt:
                    continue
                if to_dt is not None and start > to_dt:
                    continue
                overlaps = True
                break
            if overlaps:
                items.append(snapshot)
        payload = {
            "version": 1,
            "cutoff_at": cutoff_dt.isoformat(),
            "territory": territory.casefold(),
            "from_at": from_dt.isoformat(),
            "to_at": to_dt.isoformat() if to_dt else None,
            "items": items,
            "network_required_for_inference": False,
        }
        payload["catalog_fingerprint_sha256"] = _fingerprint(payload)
        return payload