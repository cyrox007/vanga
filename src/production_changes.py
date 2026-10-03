from __future__ import annotations

import json
import statistics
from typing import Any

from src.production_context import (
    EVENT_TYPES,
    PRODUCTION_STAGES,
    ProductionContextStore,
    ProductionContextValidationError,
    _parse_datetime,
)


TEAM_CHANGE_TYPES = {
    "director_change",
    "writer_change",
    "creative_lead_change",
    "production_label_change",
}

REWORK_TYPES = {
    "rewrite",
    "reshoot",
    "recut",
}


def _normalized_token(value: Any) -> str:
    return "_".join(str(value or "").strip().casefold().replace("-", " ").split())


class ProductionChangeContext:
    """Derived factual proxies поверх timestamped production events.

    Слой не оценивает причину или качество события. Он только нормализует
    наблюдаемые counts и, когда event details содержат обе даты релиза,
    вычисляет направление/длительность переноса.
    """

    def __init__(self, store: ProductionContextStore) -> None:
        self.store = store
        self.conn = store.conn

    @staticmethod
    def _release_shift_days(details: dict[str, Any]) -> float | None:
        old_raw = details.get("old_release_at")
        new_raw = details.get("new_release_at")
        if old_raw in {None, ""} or new_raw in {None, ""}:
            return None
        try:
            old_dt = _parse_datetime(old_raw, field_name="details.old_release_at")
            new_dt = _parse_datetime(new_raw, field_name="details.new_release_at")
        except ProductionContextValidationError:
            raise
        if old_dt is None or new_dt is None:
            return None
        return (new_dt - old_dt).total_seconds() / 86400.0

    @staticmethod
    def _is_additional_photography(details: dict[str, Any]) -> bool:
        candidates = {
            _normalized_token(details.get("activity")),
            _normalized_token(details.get("subtype")),
            _normalized_token(details.get("shoot_type")),
        }
        return bool(
            candidates.intersection(
                {
                    "additional_photography",
                    "additional_shooting",
                    "additional_shoots",
                }
            )
        )

    def changes_as_of(self, project_id: str, cutoff) -> list[dict[str, Any]]:
        """Возвращает все известные к cutoff события с derived metadata."""
        self.store._require_project(project_id)
        cutoff_dt = _parse_datetime(cutoff, field_name="cutoff")
        rows = self.conn.execute(
            """
            SELECT
                event_id, event_type, event_at, known_at,
                stage, source_id, details_json
            FROM production_events
            WHERE project_id = ? AND known_at <= ?
            ORDER BY known_at, event_id
            """,
            [project_id, cutoff_dt],
        ).fetchall()
        result: list[dict[str, Any]] = []
        for event_id, event_type, event_at, known_at, stage, source_id, details_json in rows:
            details = json.loads(details_json)
            shift_days = (
                self._release_shift_days(details)
                if str(event_type) == "release_date_change"
                else None
            )
            result.append(
                {
                    "event_id": str(event_id),
                    "event_type": str(event_type),
                    "event_at": event_at,
                    "known_at": known_at,
                    "stage": str(stage),
                    "source_id": str(source_id),
                    "details": details,
                    "release_shift_days": shift_days,
                    "additional_photography": (
                        str(event_type) == "reshoot"
                        and self._is_additional_photography(details)
                    ),
                }
            )
        return result

    def features_as_of(self, project_id: str, cutoff) -> dict[str, float]:
        """Строит прозрачные pre-release production-change proxies."""
        events = self.changes_as_of(project_id, cutoff)
        counts = {kind: 0.0 for kind in EVENT_TYPES}
        stage_counts = {stage: 0.0 for stage in PRODUCTION_STAGES}
        release_changes = 0
        release_shifts: list[float] = []
        additional_photography_count = 0
        event_date_known = 0

        for event in events:
            event_type = event["event_type"]
            stage = event["stage"]
            if event_type in counts:
                counts[event_type] += 1.0
            if stage in stage_counts:
                stage_counts[stage] += 1.0
            if event["event_at"] is not None:
                event_date_known += 1
            if event_type == "release_date_change":
                release_changes += 1
                if event["release_shift_days"] is not None:
                    release_shifts.append(float(event["release_shift_days"]))
            if event["additional_photography"]:
                additional_photography_count += 1

        delays = [value for value in release_shifts if value > 0]
        advances = [-value for value in release_shifts if value < 0]

        result: dict[str, float] = {
            "production_team_change_count": float(
                sum(counts[kind] for kind in TEAM_CHANGE_TYPES)
            ),
            "production_rework_count": float(
                sum(counts[kind] for kind in REWORK_TYPES)
            ),
            "production_additional_photography_count": float(
                additional_photography_count
            ),
            "production_release_delay_count": float(len(delays)),
            "production_release_delay_days_total": float(sum(delays)),
            "production_release_delay_days_max": float(max(delays, default=0.0)),
            "production_release_delay_days_mean": (
                float(statistics.fmean(delays)) if delays else 0.0
            ),
            "production_release_advance_count": float(len(advances)),
            "production_release_advance_days_total": float(sum(advances)),
            "production_release_advance_days_max": float(max(advances, default=0.0)),
            "production_release_shift_known_ratio": (
                float(len(release_shifts)) / float(release_changes)
                if release_changes
                else 0.0
            ),
            "production_change_event_date_known_ratio": (
                float(event_date_known) / float(len(events)) if events else 0.0
            ),
        }

        for stage in sorted(PRODUCTION_STAGES):
            result[f"production_change_stage_{stage}_count"] = stage_counts[stage]

        return result
