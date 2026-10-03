from __future__ import annotations

import statistics
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

import duckdb

from settings import config


SOURCE_TYPES = {
    "novel",
    "novel_series",
    "short_story",
    "comic",
    "graphic_novel",
    "manga",
    "game",
    "play",
    "musical",
    "tv_series",
    "film",
    "remake",
    "real_events",
    "biography",
    "mythology",
    "other",
}

SOURCE_RELATIONS = {
    "based_on",
    "adaptation_of",
    "remake_of",
    "inspired_by",
    "based_on_real_events",
    "other",
}

ADAPTATION_FORMATS = {
    "film",
    "miniseries",
    "series",
    "animation_film",
    "animation_series",
    "other",
}

CREATOR_ROLES = {
    "author",
    "creator",
    "writer",
    "artist",
    "playwright",
    "game_creator",
    "other",
}


class SourceContextValidationError(ValueError):
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
        raise SourceContextValidationError(f"{field_name} не должен быть пустым")
    if len(text) > limit:
        raise SourceContextValidationError(
            f"{field_name} длиннее допустимых {limit} символов"
        )
    return text


def _parse_datetime(value: Any, *, field_name: str, required: bool = True) -> datetime | None:
    if value in {None, ""}:
        if required:
            raise SourceContextValidationError(f"{field_name} обязателен")
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        raw = str(value).strip().replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(raw)
        except ValueError as exc:
            raise SourceContextValidationError(
                f"{field_name} должен быть ISO datetime"
            ) from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _optional_positive_int(value: Any, *, field_name: str) -> int | None:
    if value in {None, ""}:
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise SourceContextValidationError(f"{field_name} должен быть целым числом") from exc
    if parsed < 1:
        raise SourceContextValidationError(f"{field_name} должен быть >= 1")
    return parsed


def _optional_nonnegative_float(value: Any, *, field_name: str) -> float | None:
    if value in {None, ""}:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise SourceContextValidationError(f"{field_name} должен быть числом") from exc
    if parsed < 0:
        raise SourceContextValidationError(f"{field_name} должен быть >= 0")
    return parsed


class SourceContextStore:
    """Pre-release registry первоисточников с provenance и as-of semantics."""

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path or config.SOURCE_CONTEXT_DB_PATH)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = duckdb.connect(str(self.path))
        self._ensure_schema()

    def close(self) -> None:
        self.conn.close()

    def _ensure_schema(self) -> None:
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS source_context_sources (
                source_id VARCHAR PRIMARY KEY,
                url VARCHAR NOT NULL,
                title VARCHAR,
                publisher VARCHAR,
                published_at TIMESTAMP WITH TIME ZONE,
                retrieved_at TIMESTAMP WITH TIME ZONE NOT NULL,
                confidence DOUBLE NOT NULL
            )
            """
        )
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS source_works (
                work_id VARCHAR PRIMARY KEY,
                title VARCHAR NOT NULL,
                source_type VARCHAR NOT NULL,
                first_publication_at TIMESTAMP WITH TIME ZONE,
                series_id VARCHAR,
                series_position INTEGER,
                series_size INTEGER,
                external_id VARCHAR,
                created_at TIMESTAMP WITH TIME ZONE NOT NULL
            )
            """
        )
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS source_creators (
                creator_id VARCHAR PRIMARY KEY,
                name VARCHAR NOT NULL,
                external_id VARCHAR,
                created_at TIMESTAMP WITH TIME ZONE NOT NULL
            )
            """
        )
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS source_work_creators (
                link_id VARCHAR PRIMARY KEY,
                work_id VARCHAR NOT NULL,
                creator_id VARCHAR NOT NULL,
                role VARCHAR NOT NULL,
                known_at TIMESTAMP WITH TIME ZONE NOT NULL,
                source_id VARCHAR NOT NULL
            )
            """
        )
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS source_context_projects (
                project_id VARCHAR PRIMARY KEY,
                imdb_id VARCHAR,
                title VARCHAR NOT NULL,
                release_at TIMESTAMP WITH TIME ZONE,
                adaptation_format VARCHAR NOT NULL,
                planned_runtime_minutes DOUBLE,
                planned_episode_count INTEGER,
                planned_episode_runtime_minutes DOUBLE,
                format_known_at TIMESTAMP WITH TIME ZONE NOT NULL,
                updated_at TIMESTAMP WITH TIME ZONE NOT NULL
            )
            """
        )
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS project_source_links (
                link_id VARCHAR PRIMARY KEY,
                project_id VARCHAR NOT NULL,
                work_id VARCHAR NOT NULL,
                relation_type VARCHAR NOT NULL,
                is_primary BOOLEAN NOT NULL,
                known_at TIMESTAMP WITH TIME ZONE NOT NULL,
                source_id VARCHAR NOT NULL,
                note VARCHAR
            )
            """
        )
        self.conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_source_project_links_known
            ON project_source_links(project_id, known_at)
            """
        )
        self.conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_source_creator_links_work_known
            ON source_work_creators(work_id, known_at)
            """
        )

    def _require_source(self, source_id: str) -> None:
        if not self.conn.execute(
            "SELECT 1 FROM source_context_sources WHERE source_id = ?",
            [source_id],
        ).fetchone():
            raise SourceContextValidationError(f"Неизвестный source_id: {source_id}")

    def _require_work(self, work_id: str) -> None:
        if not self.conn.execute(
            "SELECT 1 FROM source_works WHERE work_id = ?", [work_id]
        ).fetchone():
            raise SourceContextValidationError(f"Неизвестный work_id: {work_id}")

    def _require_creator(self, creator_id: str) -> None:
        if not self.conn.execute(
            "SELECT 1 FROM source_creators WHERE creator_id = ?", [creator_id]
        ).fetchone():
            raise SourceContextValidationError(f"Неизвестный creator_id: {creator_id}")

    def _require_project(self, project_id: str) -> None:
        if not self.conn.execute(
            "SELECT 1 FROM source_context_projects WHERE project_id = ?", [project_id]
        ).fetchone():
            raise SourceContextValidationError(f"Неизвестный project_id: {project_id}")

    def upsert_source(self, payload: dict[str, Any]) -> str:
        source_id = _clean_text(
            payload.get("source_id") or uuid4(),
            field_name="source_id",
            limit=160,
            required=True,
        )
        url = _clean_text(payload.get("url"), field_name="url", limit=2000, required=True)
        title = _clean_text(payload.get("title"), field_name="title", limit=500) or None
        publisher = _clean_text(
            payload.get("publisher"), field_name="publisher", limit=300
        ) or None
        published_at = _parse_datetime(
            payload.get("published_at"), field_name="published_at", required=False
        )
        retrieved_at = _parse_datetime(
            payload.get("retrieved_at") or _now(), field_name="retrieved_at"
        )
        try:
            confidence = float(payload.get("confidence", 1.0))
        except (TypeError, ValueError) as exc:
            raise SourceContextValidationError("confidence должен быть числом") from exc
        if not 0.0 <= confidence <= 1.0:
            raise SourceContextValidationError("confidence должен быть в диапазоне 0..1")
        self.conn.execute(
            """
            INSERT INTO source_context_sources VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source_id) DO UPDATE SET
                url=excluded.url, title=excluded.title, publisher=excluded.publisher,
                published_at=excluded.published_at, retrieved_at=excluded.retrieved_at,
                confidence=excluded.confidence
            """,
            [source_id, url, title, publisher, published_at, retrieved_at, confidence],
        )
        return source_id

    def upsert_work(self, payload: dict[str, Any]) -> str:
        work_id = _clean_text(
            payload.get("work_id") or uuid4(),
            field_name="work_id",
            limit=160,
            required=True,
        )
        title = _clean_text(
            payload.get("title"), field_name="work.title", limit=500, required=True
        )
        source_type = str(payload.get("source_type") or "").strip()
        if source_type not in SOURCE_TYPES:
            raise SourceContextValidationError(f"Неизвестный source_type: {source_type!r}")
        publication_at = _parse_datetime(
            payload.get("first_publication_at"),
            field_name="first_publication_at",
            required=False,
        )
        series_id = _clean_text(
            payload.get("series_id"), field_name="series_id", limit=160
        ) or None
        series_position = _optional_positive_int(
            payload.get("series_position"), field_name="series_position"
        )
        series_size = _optional_positive_int(
            payload.get("series_size"), field_name="series_size"
        )
        if series_position and series_size and series_position > series_size:
            raise SourceContextValidationError("series_position не может быть больше series_size")
        external_id = _clean_text(
            payload.get("external_id"), field_name="external_id", limit=300
        ) or None
        self.conn.execute(
            """
            INSERT INTO source_works VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(work_id) DO UPDATE SET
                title=excluded.title, source_type=excluded.source_type,
                first_publication_at=excluded.first_publication_at,
                series_id=excluded.series_id, series_position=excluded.series_position,
                series_size=excluded.series_size, external_id=excluded.external_id
            """,
            [
                work_id,
                title,
                source_type,
                publication_at,
                series_id,
                series_position,
                series_size,
                external_id,
                _now(),
            ],
        )
        return work_id

    def upsert_creator(self, payload: dict[str, Any]) -> str:
        creator_id = _clean_text(
            payload.get("creator_id") or uuid4(),
            field_name="creator_id",
            limit=160,
            required=True,
        )
        name = _clean_text(
            payload.get("name"), field_name="creator.name", limit=500, required=True
        )
        external_id = _clean_text(
            payload.get("external_id"), field_name="creator.external_id", limit=300
        ) or None
        self.conn.execute(
            """
            INSERT INTO source_creators VALUES (?, ?, ?, ?)
            ON CONFLICT(creator_id) DO UPDATE SET
                name=excluded.name, external_id=excluded.external_id
            """,
            [creator_id, name, external_id, _now()],
        )
        return creator_id

    def link_creator(self, payload: dict[str, Any]) -> str:
        work_id = _clean_text(
            payload.get("work_id"), field_name="work_id", limit=160, required=True
        )
        creator_id = _clean_text(
            payload.get("creator_id"), field_name="creator_id", limit=160, required=True
        )
        role = str(payload.get("role") or "").strip()
        if role not in CREATOR_ROLES:
            raise SourceContextValidationError(f"Неизвестная creator role: {role!r}")
        source_id = _clean_text(
            payload.get("source_id"), field_name="source_id", limit=160, required=True
        )
        self._require_work(work_id)
        self._require_creator(creator_id)
        self._require_source(source_id)
        known_at = _parse_datetime(payload.get("known_at"), field_name="known_at")
        link_id = _clean_text(
            payload.get("link_id") or uuid4(),
            field_name="link_id",
            limit=160,
            required=True,
        )
        self.conn.execute(
            """
            INSERT INTO source_work_creators VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(link_id) DO UPDATE SET
                work_id=excluded.work_id, creator_id=excluded.creator_id,
                role=excluded.role, known_at=excluded.known_at, source_id=excluded.source_id
            """,
            [link_id, work_id, creator_id, role, known_at, source_id],
        )
        return link_id

    def upsert_project(self, payload: dict[str, Any]) -> str:
        project_id = _clean_text(
            payload.get("project_id"), field_name="project_id", limit=160, required=True
        )
        imdb_id = _clean_text(payload.get("imdb_id"), field_name="imdb_id", limit=32) or None
        title = _clean_text(
            payload.get("title"), field_name="project.title", limit=500, required=True
        )
        release_at = _parse_datetime(
            payload.get("release_at"), field_name="release_at", required=False
        )
        adaptation_format = str(payload.get("adaptation_format") or "film").strip()
        if adaptation_format not in ADAPTATION_FORMATS:
            raise SourceContextValidationError(
                f"Неизвестный adaptation_format: {adaptation_format!r}"
            )
        runtime = _optional_nonnegative_float(
            payload.get("planned_runtime_minutes"), field_name="planned_runtime_minutes"
        )
        episodes = _optional_positive_int(
            payload.get("planned_episode_count"), field_name="planned_episode_count"
        )
        episode_runtime = _optional_nonnegative_float(
            payload.get("planned_episode_runtime_minutes"),
            field_name="planned_episode_runtime_minutes",
        )
        known_at = _parse_datetime(
            payload.get("format_known_at"), field_name="format_known_at"
        )
        self.conn.execute(
            """
            INSERT INTO source_context_projects VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(project_id) DO UPDATE SET
                imdb_id=excluded.imdb_id, title=excluded.title,
                release_at=excluded.release_at, adaptation_format=excluded.adaptation_format,
                planned_runtime_minutes=excluded.planned_runtime_minutes,
                planned_episode_count=excluded.planned_episode_count,
                planned_episode_runtime_minutes=excluded.planned_episode_runtime_minutes,
                format_known_at=excluded.format_known_at,
                updated_at=excluded.updated_at
            """,
            [
                project_id,
                imdb_id,
                title,
                release_at,
                adaptation_format,
                runtime,
                episodes,
                episode_runtime,
                known_at,
                _now(),
            ],
        )
        return project_id

    def link_source(self, payload: dict[str, Any]) -> str:
        project_id = _clean_text(
            payload.get("project_id"), field_name="project_id", limit=160, required=True
        )
        work_id = _clean_text(
            payload.get("work_id"), field_name="work_id", limit=160, required=True
        )
        relation_type = str(payload.get("relation_type") or "").strip()
        if relation_type not in SOURCE_RELATIONS:
            raise SourceContextValidationError(
                f"Неизвестный source relation: {relation_type!r}"
            )
        source_id = _clean_text(
            payload.get("source_id"), field_name="source_id", limit=160, required=True
        )
        self._require_project(project_id)
        self._require_work(work_id)
        self._require_source(source_id)
        known_at = _parse_datetime(payload.get("known_at"), field_name="known_at")
        is_primary = bool(payload.get("is_primary", False))
        link_id = _clean_text(
            payload.get("link_id") or uuid4(),
            field_name="link_id",
            limit=160,
            required=True,
        )
        note = _clean_text(payload.get("note"), field_name="note", limit=2000) or None
        self.conn.execute(
            """
            INSERT INTO project_source_links VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(link_id) DO UPDATE SET
                project_id=excluded.project_id, work_id=excluded.work_id,
                relation_type=excluded.relation_type, is_primary=excluded.is_primary,
                known_at=excluded.known_at, source_id=excluded.source_id,
                note=excluded.note
            """,
            [
                link_id,
                project_id,
                work_id,
                relation_type,
                is_primary,
                known_at,
                source_id,
                note,
            ],
        )
        return link_id

    def links_as_of(self, project_id: str, cutoff) -> list[dict[str, Any]]:
        self._require_project(project_id)
        cutoff_dt = _parse_datetime(cutoff, field_name="cutoff")
        rows = self.conn.execute(
            """
            SELECT DISTINCT
                l.work_id, w.title, w.source_type, w.first_publication_at,
                w.series_id, w.series_position, w.series_size,
                l.relation_type, l.is_primary, MIN(l.known_at) OVER (
                    PARTITION BY l.work_id, l.relation_type, l.is_primary
                ) AS first_known_at
            FROM project_source_links l
            JOIN source_works w USING (work_id)
            WHERE l.project_id = ? AND l.known_at <= ?
            ORDER BY l.is_primary DESC, first_known_at, l.work_id
            """,
            [project_id, cutoff_dt],
        ).fetchall()
        result: list[dict[str, Any]] = []
        seen: set[tuple[str, str, bool]] = set()
        for row in rows:
            key = (str(row[0]), str(row[7]), bool(row[8]))
            if key in seen:
                continue
            seen.add(key)
            result.append(
                {
                    "work_id": str(row[0]),
                    "title": str(row[1]),
                    "source_type": str(row[2]),
                    "first_publication_at": row[3],
                    "series_id": row[4],
                    "series_position": row[5],
                    "series_size": row[6],
                    "relation_type": str(row[7]),
                    "is_primary": bool(row[8]),
                    "known_at": row[9],
                }
            )
        return result

    def features_as_of(self, project_id: str, cutoff) -> dict[str, float]:
        """Возвращает только pre-release source/format facts, известные на cutoff."""
        self._require_project(project_id)
        cutoff_dt = _parse_datetime(cutoff, field_name="cutoff")
        project = self.conn.execute(
            """
            SELECT adaptation_format, planned_runtime_minutes,
                   planned_episode_count, planned_episode_runtime_minutes,
                   format_known_at
            FROM source_context_projects WHERE project_id = ?
            """,
            [project_id],
        ).fetchone()
        format_visible = bool(project and project[4] <= cutoff_dt)
        links = self.links_as_of(project_id, cutoff_dt)
        work_ids = sorted({item["work_id"] for item in links})
        primary_work_ids = sorted(
            {item["work_id"] for item in links if item["is_primary"]}
        )
        work_by_id = {item["work_id"]: item for item in links}

        ages: list[float] = []
        series_sizes: list[float] = []
        series_positions: list[float] = []
        for work_id in work_ids:
            item = work_by_id[work_id]
            publication_at = item["first_publication_at"]
            if publication_at is not None and publication_at <= cutoff_dt:
                ages.append(
                    max(0.0, (cutoff_dt - publication_at).total_seconds() / 86400.0 / 365.25)
                )
            if item["series_size"] is not None:
                series_sizes.append(float(item["series_size"]))
            if item["series_position"] is not None:
                series_positions.append(float(item["series_position"]))

        creator_count = 0
        if work_ids:
            row = self.conn.execute(
                """
                SELECT COUNT(DISTINCT creator_id)
                FROM source_work_creators
                WHERE work_id IN (SELECT * FROM UNNEST(?))
                  AND known_at <= ?
                """,
                [work_ids, cutoff_dt],
            ).fetchone()
            creator_count = int(row[0] or 0)

        result: dict[str, float] = {
            "source_context_known": 1.0 if work_ids else 0.0,
            "source_work_count": float(len(work_ids)),
            "source_primary_work_count": float(len(primary_work_ids)),
            "source_creator_count": float(creator_count),
            "source_age_known_ratio": (
                float(len(ages)) / float(len(work_ids)) if work_ids else 0.0
            ),
            "source_age_years_mean": float(statistics.fmean(ages)) if ages else 0.0,
            "source_age_years_min": float(min(ages, default=0.0)),
            "source_age_years_max": float(max(ages, default=0.0)),
            "source_series_size_known_ratio": (
                float(len(series_sizes)) / float(len(work_ids)) if work_ids else 0.0
            ),
            "source_series_size_mean": (
                float(statistics.fmean(series_sizes)) if series_sizes else 0.0
            ),
            "source_series_size_max": float(max(series_sizes, default=0.0)),
            "source_series_position_mean": (
                float(statistics.fmean(series_positions)) if series_positions else 0.0
            ),
            "source_format_known": 1.0 if format_visible else 0.0,
            "source_planned_runtime_minutes": (
                float(project[1]) if format_visible and project[1] is not None else 0.0
            ),
            "source_planned_episode_count": (
                float(project[2]) if format_visible and project[2] is not None else 0.0
            ),
            "source_planned_episode_runtime_minutes": (
                float(project[3]) if format_visible and project[3] is not None else 0.0
            ),
        }

        if format_visible:
            total_runtime = 0.0
            if project[1] is not None:
                total_runtime = float(project[1])
            elif project[2] is not None and project[3] is not None:
                total_runtime = float(project[2]) * float(project[3])
            result["source_planned_total_runtime_minutes"] = total_runtime
        else:
            result["source_planned_total_runtime_minutes"] = 0.0

        type_by_work = {work_id: work_by_id[work_id]["source_type"] for work_id in work_ids}
        for source_type in sorted(SOURCE_TYPES):
            result[f"source_type_{source_type}_count"] = float(
                sum(1 for value in type_by_work.values() if value == source_type)
            )
        for relation in sorted(SOURCE_RELATIONS):
            result[f"source_relation_{relation}_count"] = float(
                len({item["work_id"] for item in links if item["relation_type"] == relation})
            )
        for adaptation_format in sorted(ADAPTATION_FORMATS):
            result[f"source_adaptation_format_{adaptation_format}"] = (
                1.0 if format_visible and project[0] == adaptation_format else 0.0
            )
        return result
