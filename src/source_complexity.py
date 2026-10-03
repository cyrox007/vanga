from __future__ import annotations

import json
import statistics
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from src.source_context import (
    SourceContextStore,
    SourceContextValidationError,
    _clean_text,
    _parse_datetime,
)


# Только наблюдаемые/воспроизводимые raw measurements. Содержательные StoryMap-
# признаки (события, causal links, preservation) относятся к P4 и сюда не входят.
COMPLEXITY_METRICS = {
    "source_length_words",
    "source_length_pages",
    "source_length_minutes",
    "chapter_count",
    "character_count",
    "major_character_count",
    "plotline_count",
    "major_arc_count",
    "location_count",
    "faction_count",
    "worldbuilding_entity_count",
    "worldbuilding_relation_count",
}

# Эти значения выводятся только из raw metrics одного и того же measurement
# snapshot. Нельзя делить count из method=A на length из method=B.
DERIVED_DENSITY_INPUTS = {
    "character_density_per_10k_words": "character_count",
    "major_character_density_per_10k_words": "major_character_count",
    "plotline_density_per_10k_words": "plotline_count",
    "major_arc_density_per_10k_words": "major_arc_count",
    "worldbuilding_entity_density_per_10k_words": "worldbuilding_entity_count",
    "worldbuilding_relation_density_per_10k_words": "worldbuilding_relation_count",
}


class SourceComplexityContext:
    """Versioned research measurements первоисточника.

    Контракт намеренно не выбирает «лучший» метод автоматически. Любой feature
    snapshot требует точного ``method + method_version``; это не позволяет
    незаметно смешать результаты разных экстракторов/разметчиков.
    """

    def __init__(self, store: SourceContextStore) -> None:
        self.store = store
        self.conn = store.conn
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS source_complexity_snapshots (
                snapshot_id VARCHAR PRIMARY KEY,
                work_id VARCHAR NOT NULL,
                method VARCHAR NOT NULL,
                method_version VARCHAR NOT NULL,
                measured_at TIMESTAMP WITH TIME ZONE NOT NULL,
                known_at TIMESTAMP WITH TIME ZONE NOT NULL,
                source_id VARCHAR NOT NULL,
                coverage_fraction DOUBLE NOT NULL,
                metrics_json VARCHAR NOT NULL,
                note VARCHAR,
                created_at TIMESTAMP WITH TIME ZONE NOT NULL
            )
            """
        )
        self.conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_source_complexity_work_method_known
            ON source_complexity_snapshots(work_id, method, method_version, known_at)
            """
        )

    @staticmethod
    def _validate_method(value: Any, *, field_name: str) -> str:
        text = " ".join(str(value or "").strip().split())
        if not text:
            raise SourceContextValidationError(f"{field_name} обязателен")
        if len(text) > 120:
            raise SourceContextValidationError(f"{field_name} длиннее 120 символов")
        return text

    @staticmethod
    def _validate_metrics(payload: Any) -> dict[str, float]:
        if not isinstance(payload, dict) or not payload:
            raise SourceContextValidationError("complexity metrics должен быть непустым JSON-объектом")
        result: dict[str, float] = {}
        unknown = sorted(set(payload) - COMPLEXITY_METRICS)
        if unknown:
            raise SourceContextValidationError(
                "Неизвестные source complexity metrics: " + ", ".join(unknown)
            )
        for key, raw in payload.items():
            try:
                value = float(raw)
            except (TypeError, ValueError) as exc:
                raise SourceContextValidationError(f"complexity metric {key} должен быть числом") from exc
            if value < 0:
                raise SourceContextValidationError(f"complexity metric {key} должен быть >= 0")
            result[str(key)] = value
        return result

    def add_snapshot(self, payload: dict[str, Any]) -> str:
        work_id = _clean_text(
            payload.get("work_id"), field_name="work_id", limit=160, required=True
        )
        source_id = _clean_text(
            payload.get("source_id"), field_name="source_id", limit=160, required=True
        )
        self.store._require_work(work_id)
        self.store._require_source(source_id)
        method = self._validate_method(payload.get("method"), field_name="method")
        method_version = self._validate_method(
            payload.get("method_version"), field_name="method_version"
        )
        measured_at = _parse_datetime(payload.get("measured_at"), field_name="measured_at")
        known_at = _parse_datetime(payload.get("known_at"), field_name="known_at")
        if known_at < measured_at:
            raise SourceContextValidationError(
                "known_at complexity snapshot не может быть раньше measured_at"
            )
        try:
            coverage = float(payload.get("coverage_fraction", 1.0))
        except (TypeError, ValueError) as exc:
            raise SourceContextValidationError("coverage_fraction должен быть числом") from exc
        if not 0.0 <= coverage <= 1.0:
            raise SourceContextValidationError("coverage_fraction должен быть в диапазоне 0..1")
        metrics = self._validate_metrics(payload.get("metrics"))
        snapshot_id = _clean_text(
            payload.get("snapshot_id") or uuid4(),
            field_name="snapshot_id",
            limit=180,
            required=True,
        )
        note = _clean_text(payload.get("note"), field_name="note", limit=3000) or None
        now = datetime.now(timezone.utc)
        self.conn.execute(
            """
            INSERT INTO source_complexity_snapshots(
                snapshot_id, work_id, method, method_version, measured_at, known_at,
                source_id, coverage_fraction, metrics_json, note, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(snapshot_id) DO UPDATE SET
                work_id=excluded.work_id,
                method=excluded.method,
                method_version=excluded.method_version,
                measured_at=excluded.measured_at,
                known_at=excluded.known_at,
                source_id=excluded.source_id,
                coverage_fraction=excluded.coverage_fraction,
                metrics_json=excluded.metrics_json,
                note=excluded.note
            """,
            [
                snapshot_id,
                work_id,
                method,
                method_version,
                measured_at,
                known_at,
                source_id,
                coverage,
                json.dumps(metrics, ensure_ascii=False, sort_keys=True),
                note,
                now,
            ],
        )
        return snapshot_id

    @staticmethod
    def _derived_metrics(metrics: dict[str, float]) -> dict[str, float]:
        words = float(metrics.get("source_length_words", 0.0))
        if words <= 0:
            return {}
        result: dict[str, float] = {}
        for output_name, count_name in DERIVED_DENSITY_INPUTS.items():
            if count_name in metrics:
                result[output_name] = float(metrics[count_name]) / words * 10000.0
        return result

    def snapshots_as_of(
        self,
        work_id: str,
        cutoff,
        *,
        method: str | None = None,
        method_version: str | None = None,
    ) -> list[dict[str, Any]]:
        self.store._require_work(work_id)
        cutoff_dt = _parse_datetime(cutoff, field_name="cutoff")
        clauses = ["work_id = ?", "known_at <= ?"]
        params: list[Any] = [work_id, cutoff_dt]
        if method is not None:
            clauses.append("method = ?")
            params.append(self._validate_method(method, field_name="method"))
        if method_version is not None:
            clauses.append("method_version = ?")
            params.append(self._validate_method(method_version, field_name="method_version"))
        rows = self.conn.execute(
            f"""
            SELECT snapshot_id, method, method_version, measured_at, known_at,
                   source_id, coverage_fraction, metrics_json, note
            FROM source_complexity_snapshots
            WHERE {' AND '.join(clauses)}
            ORDER BY known_at, snapshot_id
            """,
            params,
        ).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            raw = {str(k): float(v) for k, v in json.loads(row[7]).items()}
            result.append(
                {
                    "snapshot_id": str(row[0]),
                    "work_id": work_id,
                    "method": str(row[1]),
                    "method_version": str(row[2]),
                    "measured_at": row[3].isoformat(),
                    "known_at": row[4].isoformat(),
                    "source_id": str(row[5]),
                    "coverage_fraction": float(row[6]),
                    "metrics": raw,
                    "derived": self._derived_metrics(raw),
                    "note": row[8],
                }
            )
        return result

    def available_methods_as_of(self, project_id: str, cutoff) -> list[dict[str, Any]]:
        work_ids = sorted({item["work_id"] for item in self.store.links_as_of(project_id, cutoff)})
        if not work_ids:
            return []
        cutoff_dt = _parse_datetime(cutoff, field_name="cutoff")
        rows = self.conn.execute(
            """
            SELECT method, method_version,
                   COUNT(DISTINCT work_id) AS measured_works,
                   AVG(coverage_fraction) AS avg_coverage
            FROM source_complexity_snapshots
            WHERE work_id IN (SELECT * FROM UNNEST(?))
              AND known_at <= ?
            GROUP BY method, method_version
            ORDER BY method, method_version
            """,
            [work_ids, cutoff_dt],
        ).fetchall()
        total = float(len(work_ids))
        return [
            {
                "method": str(row[0]),
                "method_version": str(row[1]),
                "measured_work_count": int(row[2]),
                "linked_work_count": len(work_ids),
                "work_coverage_ratio": float(row[2]) / total,
                "measurement_coverage_mean": float(row[3] or 0.0),
            }
            for row in rows
        ]

    def features_as_of(
        self,
        project_id: str,
        cutoff,
        *,
        method: str,
        method_version: str,
    ) -> dict[str, float]:
        """Агрегирует только один точный measurement protocol."""
        self.store._require_project(project_id)
        method = self._validate_method(method, field_name="method")
        method_version = self._validate_method(method_version, field_name="method_version")
        work_ids = sorted({item["work_id"] for item in self.store.links_as_of(project_id, cutoff)})
        cutoff_dt = _parse_datetime(cutoff, field_name="cutoff")

        latest_by_work: dict[str, tuple[float, dict[str, float]]] = {}
        if work_ids:
            rows = self.conn.execute(
                """
                SELECT work_id, coverage_fraction, metrics_json
                FROM source_complexity_snapshots
                WHERE work_id IN (SELECT * FROM UNNEST(?))
                  AND method = ? AND method_version = ? AND known_at <= ?
                QUALIFY ROW_NUMBER() OVER (
                    PARTITION BY work_id ORDER BY known_at DESC, snapshot_id DESC
                ) = 1
                """,
                [work_ids, method, method_version, cutoff_dt],
            ).fetchall()
            for work_id, coverage, metrics_json in rows:
                raw = {str(k): float(v) for k, v in json.loads(metrics_json).items()}
                raw.update(self._derived_metrics(raw))
                latest_by_work[str(work_id)] = (float(coverage), raw)

        result: dict[str, float] = {
            "source_complexity_linked_work_count": float(len(work_ids)),
            "source_complexity_measured_work_count": float(len(latest_by_work)),
            "source_complexity_work_coverage_ratio": (
                float(len(latest_by_work)) / float(len(work_ids)) if work_ids else 0.0
            ),
            "source_complexity_measurement_coverage_mean": (
                float(statistics.fmean(value[0] for value in latest_by_work.values()))
                if latest_by_work
                else 0.0
            ),
        }

        metric_names = sorted(COMPLEXITY_METRICS | set(DERIVED_DENSITY_INPUTS))
        denominator = float(len(work_ids))
        for metric in metric_names:
            values = [
                metrics[metric]
                for _, metrics in latest_by_work.values()
                if metric in metrics
            ]
            prefix = f"source_complexity_{metric}"
            result[f"{prefix}_known_ratio"] = (
                float(len(values)) / denominator if denominator else 0.0
            )
            result[f"{prefix}_mean"] = (
                float(statistics.fmean(values)) if values else 0.0
            )
            result[f"{prefix}_max"] = float(max(values, default=0.0))
        return result
