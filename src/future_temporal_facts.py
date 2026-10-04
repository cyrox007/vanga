from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from settings import config
from src.future_prediction_payload import (
    FuturePredictionPayloadBuilder,
    FuturePredictionPayloadError,
    _clean_genres,
    _runtime,
)
from src.future_releases import FutureReleaseError, FutureReleaseStore


FACT_TYPES = {"runtime_minutes", "genres", "synopsis"}


class FutureTemporalFactError(ValueError):
    pass


def _dt(value: Any, *, field_name: str) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value or "").strip().replace("Z", "+00:00"))
        except ValueError as exc:
            raise FutureTemporalFactError(
                f"{field_name} должен быть ISO-8601 datetime"
            ) from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _confidence(value: Any) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise FutureTemporalFactError("confidence должен быть числом") from exc
    if not 0.0 <= parsed <= 1.0:
        raise FutureTemporalFactError("confidence должен быть в диапазоне 0..1")
    return parsed


def _normalize_fact_value(fact_type: str, value: Any) -> Any:
    if fact_type == "runtime_minutes":
        parsed = _runtime(value)
        if parsed is None:
            raise FutureTemporalFactError("runtime_minutes не должен быть пустым")
        return parsed
    if fact_type == "genres":
        genres = _clean_genres(value)
        if not genres:
            raise FutureTemporalFactError("genres не должен быть пустым")
        return sorted(genres, key=str.casefold)
    if fact_type == "synopsis":
        text = " ".join(str(value or "").strip().split())
        if not text:
            raise FutureTemporalFactError("synopsis не должен быть пустым")
        if len(text) > 5000:
            raise FutureTemporalFactError("synopsis не должен превышать 5000 символов")
        return text
    raise FutureTemporalFactError(f"Неизвестный fact_type: {fact_type!r}")


def _canonical_value(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class FutureTemporalFactStore:
    """Temporal-safe факты будущего фильма поверх P9 registry.

    Каждое наблюдение имеет ``known_at`` и ``source_id``. На cutoff берётся
    последнее наблюдение каждого источника. Если актуальные источники расходятся,
    значение не выбирается эвристически: возвращается conflict.

    Store может работать поверх уже открытого ``FutureReleaseStore``. Это нужно
    batch importer-у, чтобы temporal facts участвовали в той же транзакции, что и
    проекты, источники, люди и release windows.
    """

    def __init__(
        self,
        store: FutureReleaseStore | str | Path | None = None,
    ) -> None:
        if isinstance(store, FutureReleaseStore):
            self.registry = store
            self._owns_registry = False
        else:
            self.registry = FutureReleaseStore(
                store or config.FUTURE_RELEASE_DB_PATH
            )
            self._owns_registry = True
        self.conn = self.registry.conn
        self._ensure_schema()

    def close(self) -> None:
        if self._owns_registry:
            self.registry.close()

    def __enter__(self) -> "FutureTemporalFactStore":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def _ensure_schema(self) -> None:
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS future_release_temporal_facts(
                observation_id VARCHAR PRIMARY KEY,
                project_id VARCHAR NOT NULL,
                fact_type VARCHAR NOT NULL,
                value_json VARCHAR NOT NULL,
                known_at TIMESTAMPTZ NOT NULL,
                source_id VARCHAR NOT NULL,
                confidence DOUBLE NOT NULL
            )
            """
        )
        self.conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_future_temporal_fact_known
            ON future_release_temporal_facts(project_id, fact_type, known_at)
            """
        )

    def add_fact(self, payload: dict[str, Any]) -> str:
        project_id = str(payload.get("project_id") or "").strip()
        source_id = str(payload.get("source_id") or "").strip()
        fact_type = str(payload.get("fact_type") or "").strip()
        if not project_id:
            raise FutureTemporalFactError("project_id обязателен")
        if not source_id:
            raise FutureTemporalFactError("source_id обязателен")
        if fact_type not in FACT_TYPES:
            raise FutureTemporalFactError(f"Неизвестный fact_type: {fact_type!r}")
        try:
            self.registry._require("future_release_projects", "project_id", project_id)
            self.registry._require("future_release_sources", "source_id", source_id)
        except FutureReleaseError as exc:
            raise FutureTemporalFactError(str(exc)) from exc

        normalized = _normalize_fact_value(fact_type, payload.get("value"))
        observation_id = str(payload.get("observation_id") or uuid4()).strip()
        if not observation_id:
            raise FutureTemporalFactError("observation_id не должен быть пустым")
        if len(observation_id) > 180:
            raise FutureTemporalFactError("observation_id длиннее 180 символов")
        self.conn.execute(
            """
            INSERT INTO future_release_temporal_facts
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                observation_id,
                project_id,
                fact_type,
                _canonical_value(normalized),
                _dt(payload.get("known_at"), field_name="known_at"),
                source_id,
                _confidence(payload.get("confidence", 1.0)),
            ],
        )
        return observation_id

    def candidates_as_of(
        self,
        project_id: str,
        fact_type: str,
        cutoff: Any,
    ) -> list[dict[str, Any]]:
        if fact_type not in FACT_TYPES:
            raise FutureTemporalFactError(f"Неизвестный fact_type: {fact_type!r}")
        cutoff_dt = _dt(cutoff, field_name="cutoff")
        try:
            self.registry._require("future_release_projects", "project_id", project_id)
        except FutureReleaseError as exc:
            raise FutureTemporalFactError(str(exc)) from exc
        rows = self.conn.execute(
            """
            SELECT observation_id, value_json, known_at, source_id, confidence
            FROM (
                SELECT *, ROW_NUMBER() OVER (
                    PARTITION BY source_id
                    ORDER BY known_at DESC, observation_id DESC
                ) AS rn
                FROM future_release_temporal_facts
                WHERE project_id = ? AND fact_type = ? AND known_at <= ?
            ) ranked
            WHERE rn = 1
            ORDER BY source_id
            """,
            [project_id, fact_type, cutoff_dt],
        ).fetchall()
        grouped: dict[str, dict[str, Any]] = {}
        for row in rows:
            value = json.loads(str(row[1]))
            key = _canonical_value(value)
            item = grouped.setdefault(key, {"value": value, "evidence": []})
            item["evidence"].append(
                {
                    "observation_id": str(row[0]),
                    "known_at": row[2].isoformat(),
                    "source_id": str(row[3]),
                    "confidence": float(row[4]),
                }
            )
        result = list(grouped.values())
        for item in result:
            item["source_count"] = len(item["evidence"])
            item["max_confidence"] = max(x["confidence"] for x in item["evidence"])
        result.sort(key=lambda item: _canonical_value(item["value"]))
        return result

    def snapshot_as_of(self, project_id: str, cutoff: Any) -> dict[str, Any]:
        facts: dict[str, Any] = {}
        conflicts: list[str] = []
        candidates: dict[str, list[dict[str, Any]]] = {}
        for fact_type in sorted(FACT_TYPES):
            current = self.candidates_as_of(project_id, fact_type, cutoff)
            candidates[fact_type] = current
            if len(current) == 1:
                facts[fact_type] = current[0]["value"]
            elif len(current) > 1:
                conflicts.append(fact_type)
        return {
            "project_id": project_id,
            "cutoff_at": _dt(cutoff, field_name="cutoff").isoformat(),
            "facts": facts,
            "conflicts": conflicts,
            "candidates": candidates,
            "network_required_for_inference": False,
        }


class TemporalFuturePredictionPayloadBuilder(FuturePredictionPayloadBuilder):
    """P9 payload builder с temporal facts перед недатированным IMDb fallback."""

    def build(self, project_id: str, cutoff: Any, **kwargs: Any) -> dict[str, Any]:
        result = super().build(project_id, cutoff, **kwargs)
        with FutureTemporalFactStore(self.future_db_path) as facts_store:
            temporal = facts_store.snapshot_as_of(project_id, cutoff)

        result["temporal_facts"] = temporal
        facts = temporal["facts"]
        conflicts = set(temporal["conflicts"])
        blockers = list(result.get("blockers") or [])
        warnings = list(result.get("warnings") or [])
        input_sources = result.setdefault("input_sources", {})

        runtime_source = input_sources.get("runtime")
        temporal_runtime: int | None = None
        if runtime_source in {None, "imdb_current_snapshot"}:
            if "runtime_minutes" in conflicts:
                blockers = [item for item in blockers if item != "runtime_missing"]
                if "runtime_fact_conflict" not in blockers:
                    blockers.append("runtime_fact_conflict")
            elif facts.get("runtime_minutes") is not None:
                temporal_runtime = int(facts["runtime_minutes"])
                blockers = [item for item in blockers if item != "runtime_missing"]
                input_sources["runtime"] = "p9_temporal_fact"

        genres_source = input_sources.get("genres")
        temporal_genres: list[str] = []
        if genres_source in {None, "imdb_current_snapshot"}:
            if "genres" in conflicts:
                blockers = [item for item in blockers if item != "genres_missing"]
                if "genres_fact_conflict" not in blockers:
                    blockers.append("genres_fact_conflict")
            elif facts.get("genres"):
                temporal_genres = list(facts["genres"])
                blockers = [item for item in blockers if item != "genres_missing"]
                input_sources["genres"] = "p9_temporal_fact"

        synopsis_override = kwargs.get("synopsis")
        synopsis = " ".join(str(synopsis_override or "").strip().split())
        if not synopsis:
            if "synopsis" in conflicts:
                warnings.append("synopsis_fact_conflict")
            else:
                synopsis = str(facts.get("synopsis") or "").strip()
                if synopsis:
                    input_sources["synopsis"] = "p9_temporal_fact"
        else:
            input_sources["synopsis"] = "override"

        result["blockers"] = blockers
        result["warnings"] = list(dict.fromkeys(warnings))
        result["prediction_ready"] = not blockers

        if not blockers:
            snapshot = result["future_release_snapshot"]
            existing_request = result.get("request") or {}
            release_at = datetime.fromisoformat(
                str(snapshot["release_at"]).replace("Z", "+00:00")
            )

            final_runtime_source = input_sources.get("runtime")
            if final_runtime_source == "p9_temporal_fact":
                resolved_runtime = temporal_runtime
            elif final_runtime_source == "override":
                resolved_runtime = _runtime(kwargs.get("runtime_override"))
            elif final_runtime_source == "source_context":
                resolved_runtime = (result.get("source_context") or {}).get("planned_runtime")
            elif final_runtime_source == "imdb_current_snapshot":
                resolved_runtime = (result.get("imdb_current_snapshot") or {}).get("runtime")
            else:
                resolved_runtime = existing_request.get("runtime")

            final_genres_source = input_sources.get("genres")
            if final_genres_source == "p9_temporal_fact":
                resolved_genres = temporal_genres
            elif final_genres_source == "override":
                resolved_genres = _clean_genres(kwargs.get("genres_override"))
            elif final_genres_source == "imdb_current_snapshot":
                resolved_genres = list(
                    (result.get("imdb_current_snapshot") or {}).get("genres") or []
                )
            else:
                resolved_genres = list(existing_request.get("genres") or [])

            if resolved_runtime is None:
                raise FuturePredictionPayloadError(
                    "prediction_ready без разрешённого runtime нарушает контракт"
                )
            if not resolved_genres:
                raise FuturePredictionPayloadError(
                    "prediction_ready без разрешённых genres нарушает контракт"
                )
            directors = [str(item["canonical_name"]) for item in snapshot["directors"]]
            writers = [str(item["canonical_name"]) for item in snapshot["writers"]]
            actors = [str(item["canonical_name"]) for item in snapshot["cast"]]
            request_payload = {
                "title": snapshot["canonical_title"],
                "imdb_id": snapshot.get("imdb_id") or "",
                "director": directors[0],
                "directors": directors[:8],
                "writer": writers[0] if writers else "",
                "year": int(release_at.year),
                "runtime": int(resolved_runtime),
                "genres": list(resolved_genres),
                "actors": actors[:32],
                "source": (result.get("source_context") or {}).get("source") or {},
            }
            if synopsis:
                request_payload["synopsis"] = synopsis
            result["request"] = request_payload
        else:
            result["request"] = None

        temporal_contract = result.setdefault("temporal_contract", {})
        temporal_contract["p9_temporal_facts_used"] = bool(
            input_sources.get("runtime") == "p9_temporal_fact"
            or input_sources.get("genres") == "p9_temporal_fact"
            or input_sources.get("synopsis") == "p9_temporal_fact"
        )
        current_snapshot_used = bool(
            input_sources.get("runtime") == "imdb_current_snapshot"
            or input_sources.get("genres") == "imdb_current_snapshot"
        )
        temporal_contract["current_imdb_snapshot_used"] = current_snapshot_used
        temporal_contract["historical_backtest_safe"] = not current_snapshot_used
        return result
