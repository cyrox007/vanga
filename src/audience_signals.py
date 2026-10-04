from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

import duckdb

from settings import config


SIGNAL_TYPES = {
    "trailer_views",
    "trailer_likes",
    "trailer_comments",
    "search_interest_index",
    "mention_volume",
    "sentiment_mean",
    "sentiment_polarization",
    "wishlist_count",
}
USAGE_BASES = {"public_aggregate", "licensed", "first_party", "manual_reference"}


class AudienceSignalError(ValueError):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _clean(value: Any, *, field_name: str, limit: int = 500, required: bool = False) -> str:
    text = " ".join(str(value or "").strip().split())
    if required and not text:
        raise AudienceSignalError(f"{field_name} не должен быть пустым")
    if len(text) > limit:
        raise AudienceSignalError(f"{field_name} длиннее допустимых {limit} символов")
    return text


def _dt(value: Any, *, field_name: str, required: bool = True) -> datetime | None:
    if value in {None, ""}:
        if required:
            raise AudienceSignalError(f"{field_name} обязателен")
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
        except ValueError as exc:
            raise AudienceSignalError(f"{field_name} должен быть ISO-8601 datetime") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


class AudienceSignalStore:
    """Агрегатные pre-release audience signals без персональных данных/raw text."""

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path or config.AUDIENCE_SIGNALS_DB_PATH)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = duckdb.connect(str(self.path))
        self.conn.execute("SET threads = 1")
        self._ensure_schema()

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "AudienceSignalStore":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def _ensure_schema(self) -> None:
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS audience_signal_sources(
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
            CREATE TABLE IF NOT EXISTS audience_signal_projects(
                project_id VARCHAR PRIMARY KEY,
                imdb_id VARCHAR,
                title VARCHAR NOT NULL,
                created_at TIMESTAMPTZ NOT NULL,
                updated_at TIMESTAMPTZ NOT NULL
            )
            """
        )
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS audience_signal_observations(
                observation_id VARCHAR PRIMARY KEY,
                project_id VARCHAR NOT NULL,
                signal_type VARCHAR NOT NULL,
                value DOUBLE NOT NULL,
                unit VARCHAR NOT NULL,
                observed_at TIMESTAMPTZ NOT NULL,
                known_at TIMESTAMPTZ NOT NULL,
                planned_release_at TIMESTAMPTZ NOT NULL,
                method VARCHAR NOT NULL,
                method_version VARCHAR NOT NULL,
                source_id VARCHAR NOT NULL,
                sample_size BIGINT,
                coverage_fraction DOUBLE,
                created_at TIMESTAMPTZ NOT NULL
            )
            """
        )
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_audience_project_known ON audience_signal_observations(project_id, known_at)"
        )

    def upsert_source(self, payload: dict[str, Any]) -> str:
        source_id = _clean(payload.get("source_id") or uuid4(), field_name="source_id", limit=160, required=True)
        provider = _clean(payload.get("provider"), field_name="provider", limit=200, required=True)
        url = _clean(payload.get("url"), field_name="url", limit=2000) or None
        usage = _clean(payload.get("usage_basis"), field_name="usage_basis", limit=80, required=True)
        if usage not in USAGE_BASES:
            raise AudienceSignalError(f"Неизвестный usage_basis: {usage!r}")
        retrieved = _dt(payload.get("retrieved_at") or _now(), field_name="retrieved_at")
        self.conn.execute(
            """
            INSERT INTO audience_signal_sources VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(source_id) DO UPDATE SET provider=excluded.provider, url=excluded.url,
                usage_basis=excluded.usage_basis, retrieved_at=excluded.retrieved_at
            """,
            [source_id, provider, url, usage, retrieved],
        )
        return source_id

    def upsert_project(self, payload: dict[str, Any]) -> str:
        project_id = _clean(payload.get("project_id") or uuid4(), field_name="project_id", limit=160, required=True)
        imdb_id = _clean(payload.get("imdb_id"), field_name="imdb_id", limit=32) or None
        if imdb_id and (not imdb_id.startswith("tt") or not imdb_id[2:].isdigit()):
            raise AudienceSignalError(f"Некорректный IMDb id: {imdb_id!r}")
        title = _clean(payload.get("title"), field_name="title", limit=500, required=True)
        now = _now()
        self.conn.execute(
            """
            INSERT INTO audience_signal_projects VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(project_id) DO UPDATE SET imdb_id=excluded.imdb_id,
                title=excluded.title, updated_at=excluded.updated_at
            """,
            [project_id, imdb_id, title, now, now],
        )
        return project_id

    def _require(self, table: str, key: str, value: str) -> None:
        if self.conn.execute(f"SELECT 1 FROM {table} WHERE {key}=?", [value]).fetchone() is None:
            raise AudienceSignalError(f"Неизвестный {key}: {value}")

    def add_observation(self, payload: dict[str, Any]) -> str:
        project_id = _clean(payload.get("project_id"), field_name="project_id", limit=160, required=True)
        source_id = _clean(payload.get("source_id"), field_name="source_id", limit=160, required=True)
        self._require("audience_signal_projects", "project_id", project_id)
        self._require("audience_signal_sources", "source_id", source_id)
        signal_type = _clean(payload.get("signal_type"), field_name="signal_type", limit=100, required=True)
        if signal_type not in SIGNAL_TYPES:
            raise AudienceSignalError(f"Неизвестный signal_type: {signal_type!r}")
        try:
            value = float(payload.get("value"))
        except (TypeError, ValueError) as exc:
            raise AudienceSignalError("value должен быть числом") from exc
        if not value == value or value in {float("inf"), float("-inf")}:
            raise AudienceSignalError("value должен быть конечным числом")
        unit = _clean(payload.get("unit"), field_name="unit", limit=80, required=True)
        method = _clean(payload.get("method"), field_name="method", limit=160, required=True)
        method_version = _clean(payload.get("method_version"), field_name="method_version", limit=80, required=True)
        observed = _dt(payload.get("observed_at"), field_name="observed_at")
        known = _dt(payload.get("known_at"), field_name="known_at")
        release = _dt(payload.get("planned_release_at"), field_name="planned_release_at")
        if known < observed:
            raise AudienceSignalError("known_at не может быть раньше observed_at")
        if observed >= release or known >= release:
            raise AudienceSignalError("P8 принимает только факты, наблюдавшиеся и известные до planned_release_at")
        sample_size = payload.get("sample_size")
        if sample_size is not None:
            try:
                sample_size = int(sample_size)
            except (TypeError, ValueError) as exc:
                raise AudienceSignalError("sample_size должен быть целым") from exc
            if sample_size < 0:
                raise AudienceSignalError("sample_size должен быть >= 0")
        coverage = payload.get("coverage_fraction")
        if coverage is not None:
            try:
                coverage = float(coverage)
            except (TypeError, ValueError) as exc:
                raise AudienceSignalError("coverage_fraction должен быть числом") from exc
            if not 0.0 <= coverage <= 1.0:
                raise AudienceSignalError("coverage_fraction должен быть в диапазоне 0..1")
        observation_id = _clean(payload.get("observation_id") or uuid4(), field_name="observation_id", limit=180, required=True)
        self.conn.execute(
            """
            INSERT INTO audience_signal_observations VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [observation_id, project_id, signal_type, value, unit, observed, known, release,
             method, method_version, source_id, sample_size, coverage, _now()],
        )
        return observation_id

    def observations_as_of(self, project_id: str, cutoff: Any, *, release_at: Any) -> list[dict[str, Any]]:
        self._require("audience_signal_projects", "project_id", project_id)
        cutoff_dt = _dt(cutoff, field_name="cutoff")
        release_dt = _dt(release_at, field_name="release_at")
        if cutoff_dt >= release_dt:
            raise AudienceSignalError("cutoff должен быть строго раньше release_at")
        rows = self.conn.execute(
            """
            SELECT observation_id, signal_type, value, unit, observed_at, known_at,
                   planned_release_at, method, method_version, source_id,
                   sample_size, coverage_fraction
            FROM audience_signal_observations
            WHERE project_id=? AND observed_at<=? AND known_at<=?
              AND observed_at<? AND known_at<?
            ORDER BY known_at, observation_id
            """,
            [project_id, cutoff_dt, cutoff_dt, release_dt, release_dt],
        ).fetchall()
        return [
            {"observation_id": str(r[0]), "project_id": project_id, "signal_type": str(r[1]),
             "value": float(r[2]), "unit": str(r[3]), "observed_at": r[4].isoformat(),
             "known_at": r[5].isoformat(), "planned_release_at": r[6].isoformat(),
             "method": str(r[7]), "method_version": str(r[8]), "source_id": str(r[9]),
             "sample_size": int(r[10]) if r[10] is not None else None,
             "coverage_fraction": float(r[11]) if r[11] is not None else None}
            for r in rows
        ]

    def features_as_of(self, project_id: str, cutoff: Any, *, release_at: Any, protocols: list[dict[str, str]]) -> dict[str, float]:
        if not isinstance(protocols, list) or not protocols:
            raise AudienceSignalError("protocols должен быть непустым массивом")
        cutoff_dt = _dt(cutoff, field_name="cutoff")
        visible = self.observations_as_of(project_id, cutoff_dt, release_at=release_at)
        result: dict[str, float] = {}
        output_names: list[str] = []
        for protocol in protocols:
            if not isinstance(protocol, dict):
                raise AudienceSignalError("protocols[] должен содержать объекты")
            signal_type = _clean(protocol.get("signal_type"), field_name="signal_type", limit=100, required=True)
            method = _clean(protocol.get("method"), field_name="method", limit=160, required=True)
            version = _clean(protocol.get("method_version"), field_name="method_version", limit=80, required=True)
            unit = _clean(protocol.get("unit"), field_name="unit", limit=80, required=True)
            name = _clean(protocol.get("feature_name") or f"audience_{signal_type}", field_name="feature_name", limit=180, required=True)
            if name in output_names:
                raise AudienceSignalError(f"Дублирующийся feature_name: {name}")
            output_names.append(name)
            matches = [x for x in visible if x["signal_type"] == signal_type and x["method"] == method and x["method_version"] == version and x["unit"] == unit]
            matches.sort(key=lambda x: (x["known_at"], x["observation_id"]))
            latest = matches[-1] if matches else None
            result[name] = latest["value"] if latest else 0.0
            result[f"{name}_known"] = 1.0 if latest else 0.0
            result[f"{name}_age_days"] = max(0.0, (cutoff_dt - _dt(latest["observed_at"], field_name="observed_at")).total_seconds() / 86400.0) if latest else 0.0
            result[f"{name}_coverage"] = float(latest["coverage_fraction"]) if latest and latest["coverage_fraction"] is not None else 0.0
            result[f"{name}_sample_size"] = float(latest["sample_size"]) if latest and latest["sample_size"] is not None else 0.0
        result["audience_signal_protocol_count"] = float(len(output_names))
        result["audience_signal_known_ratio"] = sum(result[f"{name}_known"] for name in output_names) / len(output_names)
        return result
