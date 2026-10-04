from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from settings import config
from src.future_releases import FutureReleaseError, FutureReleaseStore


ISO_TERRITORY_RE = re.compile(r"^iso3166:([a-z]{2})$")
WIKIDATA_TERRITORY_RE = re.compile(r"^wikidata:q([1-9][0-9]*)$")


class FutureTerritoryError(ValueError):
    pass


def _dt(value: Any, *, field_name: str) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value or "").strip().replace("Z", "+00:00"))
        except ValueError as exc:
            raise FutureTerritoryError(f"{field_name} должен быть ISO-8601 datetime") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _confidence(value: Any) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise FutureTerritoryError("confidence должен быть числом") from exc
    if not 0.0 <= parsed <= 1.0:
        raise FutureTerritoryError("confidence должен быть в диапазоне 0..1")
    return parsed


def _canonical_iso(value: Any) -> str:
    text = str(value or "").strip().casefold()
    if text.startswith("iso3166:"):
        text = text.split(":", 1)[1]
    if len(text) != 2 or not text.isalpha():
        raise FutureTerritoryError(f"Некорректный ISO 3166-1 alpha-2: {value!r}")
    return f"iso3166:{text}"


def _raw_territory(value: Any) -> str:
    return str(value or "").strip().casefold()


class FutureTerritoryStore:
    """Temporal mapping raw territory identities в canonical ISO territory.

    Исходные release observations не переписываются. Mapping применяется только
    при чтении на заданный cutoff, поэтому поздно обнаруженный Wikidata→ISO mapping
    не протекает в исторический snapshot.
    """

    def __init__(self, store: FutureReleaseStore | str | Path | None = None) -> None:
        if isinstance(store, FutureReleaseStore):
            self.registry = store
            self._owns_registry = False
        else:
            self.registry = FutureReleaseStore(store or config.FUTURE_RELEASE_DB_PATH)
            self._owns_registry = True
        self.conn = self.registry.conn
        self._ensure_schema()

    def close(self) -> None:
        if self._owns_registry:
            self.registry.close()

    def __enter__(self) -> "FutureTerritoryStore":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def _ensure_schema(self) -> None:
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS future_release_territory_mappings(
                mapping_id VARCHAR PRIMARY KEY,
                source_territory VARCHAR NOT NULL,
                canonical_territory VARCHAR NOT NULL,
                known_at TIMESTAMPTZ NOT NULL,
                source_id VARCHAR NOT NULL,
                confidence DOUBLE NOT NULL
            )
            """
        )
        self.conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_future_territory_mapping_known
            ON future_release_territory_mappings(source_territory, known_at)
            """
        )

    def add_mapping(self, payload: dict[str, Any]) -> str:
        raw = _raw_territory(payload.get("source_territory"))
        if not WIKIDATA_TERRITORY_RE.match(raw):
            raise FutureTerritoryError(
                "source_territory mapping пока поддерживает только wikidata:Q..."
            )
        canonical = _canonical_iso(payload.get("canonical_territory"))
        source_id = str(payload.get("source_id") or "").strip()
        if not source_id:
            raise FutureTerritoryError("source_id обязателен")
        try:
            self.registry._require("future_release_sources", "source_id", source_id)
        except FutureReleaseError as exc:
            raise FutureTerritoryError(str(exc)) from exc
        mapping_id = str(payload.get("mapping_id") or uuid4()).strip()
        known_at = _dt(payload.get("known_at"), field_name="known_at")
        confidence = _confidence(payload.get("confidence", 1.0))
        self.conn.execute(
            """
            INSERT INTO future_release_territory_mappings
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            [mapping_id, raw, canonical, known_at, source_id, confidence],
        )
        return mapping_id

    def resolve_as_of(self, territory: str, cutoff: Any) -> dict[str, Any]:
        raw = _raw_territory(territory)
        cutoff_dt = _dt(cutoff, field_name="cutoff")
        if ISO_TERRITORY_RE.match(raw):
            return {
                "source_territory": raw,
                "canonical_territory": raw,
                "resolved": True,
                "conflict": False,
                "evidence": [],
            }
        if raw in {"worldwide", "unspecified"}:
            return {
                "source_territory": raw,
                "canonical_territory": raw,
                "resolved": raw == "worldwide",
                "conflict": False,
                "evidence": [],
            }
        if not WIKIDATA_TERRITORY_RE.match(raw):
            return {
                "source_territory": raw,
                "canonical_territory": None,
                "resolved": False,
                "conflict": False,
                "evidence": [],
            }
        rows = self.conn.execute(
            """
            SELECT mapping_id, canonical_territory, known_at, source_id, confidence
            FROM future_release_territory_mappings
            WHERE source_territory = ? AND known_at <= ?
            ORDER BY known_at DESC, mapping_id DESC
            """,
            [raw, cutoff_dt],
        ).fetchall()
        if not rows:
            return {
                "source_territory": raw,
                "canonical_territory": None,
                "resolved": False,
                "conflict": False,
                "evidence": [],
            }
        latest_by_source: dict[str, tuple] = {}
        for row in rows:
            latest_by_source.setdefault(str(row[3]), row)
        values = {str(row[1]) for row in latest_by_source.values()}
        evidence = [
            {
                "mapping_id": str(row[0]),
                "canonical_territory": str(row[1]),
                "known_at": row[2].isoformat(),
                "source_id": str(row[3]),
                "confidence": float(row[4]),
            }
            for row in latest_by_source.values()
        ]
        return {
            "source_territory": raw,
            "canonical_territory": next(iter(values)) if len(values) == 1 else None,
            "resolved": len(values) == 1,
            "conflict": len(values) > 1,
            "evidence": sorted(evidence, key=lambda item: item["source_id"]),
        }

    def release_snapshot_as_of(
        self,
        project_id: str,
        cutoff: Any,
        *,
        canonical_territory: str,
    ) -> dict[str, Any]:
        canonical = _canonical_iso(canonical_territory)
        cutoff_dt = _dt(cutoff, field_name="cutoff")
        try:
            self.registry._require("future_release_projects", "project_id", project_id)
        except FutureReleaseError as exc:
            raise FutureTerritoryError(str(exc)) from exc
        rows = self.conn.execute(
            """
            SELECT observation_id, territory, release_start_at, release_end_at,
                   precision, known_at, source_id, confidence
            FROM future_release_windows
            WHERE project_id = ? AND known_at <= ?
            ORDER BY known_at DESC, observation_id DESC
            """,
            [project_id, cutoff_dt],
        ).fetchall()

        candidates: list[dict[str, Any]] = []
        unresolved: list[dict[str, Any]] = []
        for row in rows:
            raw_territory = _raw_territory(row[1])
            resolved = self.resolve_as_of(raw_territory, cutoff_dt)
            if not resolved["resolved"]:
                if raw_territory.startswith("wikidata:"):
                    unresolved.append(
                        {
                            "observation_id": str(row[0]),
                            "territory": raw_territory,
                            "mapping_conflict": bool(resolved["conflict"]),
                        }
                    )
                continue
            if resolved["canonical_territory"] != canonical:
                continue
            candidates.append(
                {
                    "observation_id": str(row[0]),
                    "source_territory": raw_territory,
                    "canonical_territory": canonical,
                    "release_start_at": row[2].isoformat(),
                    "release_end_at": row[3].isoformat(),
                    "precision": str(row[4]),
                    "known_at": row[5].isoformat(),
                    "source_id": str(row[6]),
                    "confidence": float(row[7]),
                }
            )

        latest_by_source: dict[str, dict[str, Any]] = {}
        for item in candidates:
            latest_by_source.setdefault(item["source_id"], item)
        latest = list(latest_by_source.values())
        groups: dict[str, dict[str, Any]] = {}
        for item in latest:
            key = json.dumps(
                [item["release_start_at"], item["release_end_at"], item["precision"]],
                separators=(",", ":"),
            )
            group = groups.setdefault(
                key,
                {
                    "release_start_at": item["release_start_at"],
                    "release_end_at": item["release_end_at"],
                    "precision": item["precision"],
                    "evidence": [],
                },
            )
            group["evidence"].append(item)
        grouped = list(groups.values())
        grouped.sort(key=lambda item: (item["release_start_at"], item["release_end_at"]))
        for item in grouped:
            item["source_count"] = len(item["evidence"])
        return {
            "project_id": project_id,
            "cutoff_at": cutoff_dt.isoformat(),
            "canonical_territory": canonical,
            "resolved": len(grouped) == 1,
            "conflict": len(grouped) > 1,
            "release_window": grouped[0] if len(grouped) == 1 else None,
            "candidates": grouped,
            "unresolved_observations": unresolved,
            "network_required_for_inference": False,
        }
