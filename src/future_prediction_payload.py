from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import duckdb

from settings import config
from src.future_releases import FutureReleaseError, FutureReleaseStore
from src.production_context import ProductionContextStore
from src.source_context import SourceContextStore


class FuturePredictionPayloadError(ValueError):
    pass


def _canonical_json(payload: Any) -> str:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _fingerprint(payload: Any) -> str:
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def _parse_dt(value: Any, *, field_name: str) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
        except ValueError as exc:
            raise FuturePredictionPayloadError(
                f"{field_name} должен быть ISO-8601 datetime"
            ) from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _clean_genres(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        raw = value.split(",")
    elif isinstance(value, (list, tuple)):
        raw = value
    else:
        raise FuturePredictionPayloadError("genres_override должен быть строкой или массивом")
    result: list[str] = []
    for item in raw:
        text = " ".join(str(item or "").strip().split())
        if text and text not in result:
            result.append(text)
    return result


def _runtime(value: Any) -> int | None:
    if value in {None, ""}:
        return None
    try:
        parsed = int(round(float(value)))
    except (TypeError, ValueError) as exc:
        raise FuturePredictionPayloadError("runtime должен быть числом") from exc
    if not 1 <= parsed <= 1000:
        raise FuturePredictionPayloadError("runtime должен быть в диапазоне 1..1000 минут")
    return parsed


class FuturePredictionPayloadBuilder:
    """Собирает cache-only payload будущего фильма для существующего `/predict`.

    Класс не обращается к сети. По умолчанию используются только temporal facts
    с доказуемым ``known_at`` и явные overrides запроса. Текущий IMDb snapshot
    runtime/genres не имеет собственного point-in-time timestamp, поэтому он
    запрещён по умолчанию и доступен только через явный opt-in для интерактивного
    сценария «что известно сейчас». Такой fallback нельзя использовать как
    historical/backtest evidence.
    """

    def __init__(
        self,
        *,
        future_db_path: str | Path | None = None,
        imdb_db_path: str | Path | None = None,
        source_db_path: str | Path | None = None,
        production_db_path: str | Path | None = None,
    ) -> None:
        self.future_db_path = Path(future_db_path or config.FUTURE_RELEASE_DB_PATH)
        self.imdb_db_path = Path(imdb_db_path or config.IMDB_DB_PATH)
        self.source_db_path = Path(source_db_path or config.SOURCE_CONTEXT_DB_PATH)
        self.production_db_path = Path(
            production_db_path or config.PRODUCTION_CONTEXT_DB_PATH
        )

    @staticmethod
    def _single_project_id(
        conn: duckdb.DuckDBPyConnection,
        *,
        table: str,
        imdb_id: str,
        cutoff: datetime | None = None,
        known_column: str | None = None,
    ) -> tuple[str | None, bool]:
        clauses = ["imdb_id = ?"]
        params: list[Any] = [imdb_id]
        if cutoff is not None and known_column:
            clauses.append(f"{known_column} <= ?")
            params.append(cutoff)
        rows = conn.execute(
            f"SELECT project_id FROM {table} WHERE {' AND '.join(clauses)} ORDER BY project_id LIMIT 2",
            params,
        ).fetchall()
        if len(rows) > 1:
            return None, True
        return (str(rows[0][0]), False) if rows else (None, False)

    def _imdb_context(self, imdb_id: str | None) -> dict[str, Any]:
        result = {
            "available": False,
            "runtime": None,
            "genres": [],
            "point_in_time": False,
            "observation_time": None,
        }
        if not imdb_id or not self.imdb_db_path.exists():
            return result
        conn = duckdb.connect(str(self.imdb_db_path), read_only=True)
        try:
            try:
                row = conn.execute(
                    """
                    SELECT runtimeMinutes, genres
                    FROM title_basics
                    WHERE tconst = ?
                    LIMIT 1
                    """,
                    [imdb_id],
                ).fetchone()
            except duckdb.Error:
                return result
        finally:
            conn.close()
        if row is None:
            return result
        result["available"] = True
        result["runtime"] = _runtime(row[0]) if row[0] is not None else None
        result["genres"] = _clean_genres(row[1]) if row[1] is not None else []
        return result

    def _source_context(
        self,
        imdb_id: str | None,
        cutoff: datetime,
    ) -> dict[str, Any]:
        result: dict[str, Any] = {
            "available": False,
            "ambiguous": False,
            "project_id": None,
            "planned_runtime": None,
            "source": {},
            "features": {},
        }
        if not imdb_id or not self.source_db_path.exists():
            return result
        store = SourceContextStore(self.source_db_path)
        try:
            project_id, ambiguous = self._single_project_id(
                store.conn,
                table="source_context_projects",
                imdb_id=imdb_id,
            )
            result["ambiguous"] = ambiguous
            if not project_id:
                return result
            result["available"] = True
            result["project_id"] = project_id
            result["features"] = store.features_as_of(project_id, cutoff)
            row = store.conn.execute(
                """
                SELECT adaptation_format, planned_runtime_minutes, format_known_at
                FROM source_context_projects
                WHERE project_id = ?
                """,
                [project_id],
            ).fetchone()
            if row and row[2] <= cutoff:
                result["planned_runtime"] = (
                    _runtime(row[1]) if row[1] is not None else None
                )
                adaptation_format = str(row[0] or "").strip()
            else:
                adaptation_format = ""

            links = store.links_as_of(project_id, cutoff)
            unique_works: dict[str, dict[str, Any]] = {}
            for item in links:
                unique_works.setdefault(str(item["work_id"]), item)
            primary = [item for item in unique_works.values() if item.get("is_primary")]
            chosen = primary[0] if len(primary) == 1 else None
            if chosen is None and len(unique_works) == 1:
                chosen = next(iter(unique_works.values()))
            if chosen is not None:
                source_payload: dict[str, Any] = {
                    "type": str(chosen.get("source_type") or ""),
                    "title": str(chosen.get("title") or ""),
                }
                if adaptation_format:
                    source_payload["format"] = adaptation_format
                if chosen.get("series_size") is not None:
                    source_payload["series_size"] = int(chosen["series_size"])
                result["source"] = {
                    key: value for key, value in source_payload.items() if value not in {None, ""}
                }
            result["linked_work_count"] = len(unique_works)
            result["primary_work_count"] = len(primary)
            return result
        finally:
            store.close()

    def _production_context(
        self,
        imdb_id: str | None,
        cutoff: datetime,
    ) -> dict[str, Any]:
        result: dict[str, Any] = {
            "available": False,
            "ambiguous": False,
            "project_id": None,
            "features": {},
        }
        if not imdb_id or not self.production_db_path.exists():
            return result
        store = ProductionContextStore(self.production_db_path)
        try:
            project_id, ambiguous = self._single_project_id(
                store.conn,
                table="production_projects",
                imdb_id=imdb_id,
                cutoff=cutoff,
                known_column="identity_known_at",
            )
            result["ambiguous"] = ambiguous
            if not project_id:
                return result
            result["available"] = True
            result["project_id"] = project_id
            result["features"] = store.features_as_of(project_id, cutoff)
            return result
        finally:
            store.close()

    def build(
        self,
        project_id: str,
        cutoff: Any,
        *,
        territory: str = "worldwide",
        runtime_override: Any = None,
        genres_override: Any = None,
        synopsis: str | None = None,
        allow_current_imdb_snapshot: bool = False,
    ) -> dict[str, Any]:
        cutoff_dt = _parse_dt(cutoff, field_name="cutoff")
        with FutureReleaseStore(self.future_db_path) as future:
            snapshot = future.snapshot_as_of(
                project_id,
                cutoff_dt,
                territory=territory,
            )

        blockers: list[str] = []
        warnings: list[str] = []
        if snapshot["release_date_conflict"]:
            blockers.append("release_date_conflict")
        release_at_raw = snapshot.get("release_at")
        release_at = (
            _parse_dt(release_at_raw, field_name="release_at")
            if release_at_raw
            else None
        )
        if release_at is None:
            blockers.append("exact_release_date_missing")
        elif cutoff_dt >= release_at:
            blockers.append("cutoff_must_be_pre_release")

        directors = [str(item["canonical_name"]) for item in snapshot["directors"]]
        if not directors:
            blockers.append("director_missing")
        writers = [str(item["canonical_name"]) for item in snapshot["writers"]]
        actors = [str(item["canonical_name"]) for item in snapshot["cast"]]
        if len(writers) > 1:
            warnings.append("multiple_writers_first_writer_contract")

        imdb_context = (
            self._imdb_context(snapshot.get("imdb_id"))
            if allow_current_imdb_snapshot
            else {
                "available": False,
                "runtime": None,
                "genres": [],
                "point_in_time": False,
                "observation_time": None,
                "disabled_reason": "undated_current_snapshot_requires_explicit_opt_in",
            }
        )
        source_context = self._source_context(snapshot.get("imdb_id"), cutoff_dt)
        production_context = self._production_context(snapshot.get("imdb_id"), cutoff_dt)
        if source_context.get("ambiguous"):
            warnings.append("source_context_project_ambiguous")
        if production_context.get("ambiguous"):
            warnings.append("production_context_project_ambiguous")

        if runtime_override is not None:
            runtime = _runtime(runtime_override)
            runtime_source = "override"
        elif source_context.get("planned_runtime") is not None:
            runtime = int(source_context["planned_runtime"])
            runtime_source = "source_context"
        elif allow_current_imdb_snapshot and imdb_context.get("runtime") is not None:
            runtime = int(imdb_context["runtime"])
            runtime_source = "imdb_current_snapshot"
        else:
            runtime = None
            runtime_source = None
            blockers.append("runtime_missing")

        if genres_override is not None:
            genres = _clean_genres(genres_override)
            genres_source = "override"
        elif allow_current_imdb_snapshot:
            genres = list(imdb_context.get("genres") or [])
            genres_source = "imdb_current_snapshot" if genres else None
        else:
            genres = []
            genres_source = None
        if not genres:
            blockers.append("genres_missing")

        current_imdb_snapshot_used = bool(
            runtime_source == "imdb_current_snapshot"
            or genres_source == "imdb_current_snapshot"
        )
        if current_imdb_snapshot_used:
            warnings.append("current_imdb_snapshot_not_point_in_time")

        request_payload: dict[str, Any] | None = None
        if not blockers:
            request_payload = {
                "title": snapshot["canonical_title"],
                "imdb_id": snapshot.get("imdb_id") or "",
                "director": directors[0],
                "directors": directors[:8],
                "writer": writers[0] if writers else "",
                "year": int(release_at.year),
                "runtime": int(runtime),
                "genres": genres,
                "actors": actors[:32],
                "source": source_context.get("source") or {},
            }
            clean_synopsis = " ".join(str(synopsis or "").strip().split())
            if clean_synopsis:
                if len(clean_synopsis) > 5000:
                    raise FuturePredictionPayloadError(
                        "synopsis не должен превышать 5000 символов"
                    )
                request_payload["synopsis"] = clean_synopsis

        result = {
            "version": 2,
            "project_id": project_id,
            "cutoff_at": cutoff_dt.isoformat(),
            "prediction_ready": not blockers,
            "blockers": blockers,
            "warnings": warnings,
            "request": request_payload,
            "future_release_snapshot": snapshot,
            "input_sources": {
                "runtime": runtime_source,
                "genres": genres_source,
                "imdb_local_available": bool(imdb_context.get("available")),
                "current_imdb_snapshot_allowed": bool(allow_current_imdb_snapshot),
                "source_context_available": bool(source_context.get("available")),
                "production_context_available": bool(
                    production_context.get("available")
                ),
            },
            "source_context": source_context,
            "production_context": production_context,
            "imdb_current_snapshot": imdb_context,
            "temporal_contract": {
                "current_imdb_snapshot_allowed": bool(allow_current_imdb_snapshot),
                "current_imdb_snapshot_used": current_imdb_snapshot_used,
                "current_imdb_snapshot_point_in_time": False,
                "historical_backtest_safe": not current_imdb_snapshot_used,
            },
            "identity_hints": {
                "directors": [
                    {
                        "name": item["canonical_name"],
                        "imdb_id": item.get("imdb_id"),
                        "wikidata_id": item.get("wikidata_id"),
                    }
                    for item in snapshot["directors"]
                ],
                "writers": [
                    {
                        "name": item["canonical_name"],
                        "imdb_id": item.get("imdb_id"),
                        "wikidata_id": item.get("wikidata_id"),
                    }
                    for item in snapshot["writers"]
                ],
                "cast": [
                    {
                        "name": item["canonical_name"],
                        "imdb_id": item.get("imdb_id"),
                        "wikidata_id": item.get("wikidata_id"),
                    }
                    for item in snapshot["cast"]
                ],
            },
            "network_required": False,
        }
        result["payload_fingerprint_sha256"] = _fingerprint(result)
        return result
