from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from src.proxy_hypotheses import ProxyHypothesisValidationError
from src.source_complexity import SourceComplexityContext
from src.source_context import SourceContextStore, _parse_datetime
from src.source_format_pressure import SourceFormatPressureContext


MATERIALIZER_VERSION = 1
SUPPORTED_TEMPORAL_CONTRACTS = {"known_at_lte_cutoff", "planned_before_release"}

# Алиасы P6 отражают смысл гипотезы, но всегда разворачиваются в конкретный
# versioned Source Context feature. Никакого автоматического выбора измерителя.
FEATURE_ALIASES = {
    "planned_runtime_minutes": "source_planned_runtime_minutes",
    "planned_episode_count": "source_planned_episode_count",
    "planned_episode_runtime_minutes": "source_planned_episode_runtime_minutes",
    "planned_total_runtime_minutes": "source_planned_total_runtime_minutes",
    "source_worldbuilding_entity_count": "source_complexity_worldbuilding_entity_count_mean",
    "source_complexity_coverage": "source_complexity_work_coverage_ratio",
}

_PROJECT_FORMAT_FEATURES = {
    "source_format_known",
    "source_planned_runtime_minutes",
    "source_planned_episode_count",
    "source_planned_episode_runtime_minutes",
    "source_planned_total_runtime_minutes",
}

_PRESSURE_FORMAT_FEATURES = {
    "source_age_at_release_known_ratio",
    "source_age_at_release_years_mean",
    "source_age_at_release_years_min",
    "source_age_at_release_years_max",
    "source_publication_after_release_count",
    "source_format_pressure_runtime_known",
    "source_runtime_per_linked_work_known",
    "source_runtime_per_linked_work_minutes",
    "source_runtime_per_primary_work_known",
    "source_runtime_per_primary_work_minutes",
    "source_episodes_per_linked_work_known",
    "source_episodes_per_linked_work",
}

_LINK_ALWAYS_FEATURES = {
    "source_work_count",
    "source_primary_work_count",
    "source_creator_count",
    "source_age_known_ratio",
    "source_series_size_known_ratio",
    "source_series_count",
    "source_series_progress_known_ratio",
    "source_type_diversity",
    "source_relation_diversity",
}


def _canonical_json(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _fingerprint(payload: Any) -> str:
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def _clean(value: Any, *, field_name: str, limit: int = 300, required: bool = False) -> str:
    text = " ".join(str(value or "").strip().split())
    if required and not text:
        raise ProxyHypothesisValidationError(f"{field_name} не должен быть пустым")
    if len(text) > limit:
        raise ProxyHypothesisValidationError(f"{field_name} длиннее {limit} символов")
    return text


def _iso(value: Any, *, field_name: str) -> str:
    parsed = _parse_datetime(value, field_name=field_name)
    assert parsed is not None
    return parsed.isoformat()


class ProxySourceContextMaterializer:
    """Materialize P6 source_context proxies в контракт #70.

    Один вызов принимает только source_context features. Это намеренно: пока
    IMDb/Production adapters не объединены composer-ом, частичный mixed-source
    payload нельзя случайно принять за готовый materialization.
    """

    def __init__(self, store: SourceContextStore) -> None:
        self.store = store
        self.conn = store.conn
        self.pressure = SourceFormatPressureContext(store)
        self.complexity = SourceComplexityContext(store)

    def materialize(
        self,
        plan: dict[str, Any],
        targets: list[dict[str, Any]],
        *,
        complexity_method: str | None = None,
        complexity_method_version: str | None = None,
    ) -> dict[str, Any]:
        specs = self._source_specs(plan)
        if not isinstance(targets, list) or not targets:
            raise ProxyHypothesisValidationError("targets должен быть непустым массивом")

        needs_complexity = any(self._canonical_name(name).startswith("source_complexity_") for name in specs)
        if needs_complexity:
            complexity_method = _clean(
                complexity_method,
                field_name="complexity_method",
                limit=120,
                required=True,
            )
            complexity_method_version = _clean(
                complexity_method_version,
                field_name="complexity_method_version",
                limit=120,
                required=True,
            )

        normalized_targets: list[dict[str, Any]] = []
        observations: list[dict[str, Any]] = []
        seen_targets: set[str] = set()
        for raw in targets:
            if not isinstance(raw, dict):
                raise ProxyHypothesisValidationError("targets[] должен содержать JSON-объекты")
            target_id = _clean(raw.get("target_id"), field_name="target_id", limit=180, required=True)
            project_id = _clean(
                raw.get("project_id") or target_id,
                field_name=f"{target_id}.project_id",
                limit=180,
                required=True,
            )
            if target_id in seen_targets:
                raise ProxyHypothesisValidationError(f"Дублирующий target_id: {target_id}")
            seen_targets.add(target_id)
            try:
                target_year = int(raw.get("target_year"))
            except (TypeError, ValueError) as exc:
                raise ProxyHypothesisValidationError(f"{target_id}.target_year должен быть целым") from exc
            cutoff_at = _iso(raw.get("cutoff_at"), field_name=f"{target_id}.cutoff_at")
            release_at = _iso(raw.get("release_at"), field_name=f"{target_id}.release_at")
            normalized_targets.append(
                {
                    "target_id": target_id,
                    "target_year": target_year,
                    "cutoff_at": cutoff_at,
                    "release_at": release_at,
                }
            )
            observations.extend(
                self._materialize_target(
                    target_id=target_id,
                    project_id=project_id,
                    cutoff_at=cutoff_at,
                    specs=specs,
                    complexity_method=complexity_method,
                    complexity_method_version=complexity_method_version,
                )
            )

        payload = {
            "version": MATERIALIZER_VERSION,
            "targets": sorted(normalized_targets, key=lambda item: item["target_id"]),
            "observations": sorted(
                observations,
                key=lambda item: (item["target_id"], item["feature_name"]),
            ),
        }
        # Fingerprint informational only; authoritative fingerprints are computed
        # later by TemporalProxyAvailabilityAuditor.
        payload["materializer_fingerprint_sha256"] = _fingerprint(payload)
        return payload

    def _source_specs(self, plan: dict[str, Any]) -> dict[str, dict[str, Any]]:
        if not isinstance(plan, dict):
            raise ProxyHypothesisValidationError("plan должен быть JSON-объектом")
        raw = plan.get("candidate_pre_release_features") or []
        if not isinstance(raw, list) or not raw:
            raise ProxyHypothesisValidationError("plan не содержит candidate_pre_release_features")
        specs: dict[str, dict[str, Any]] = {}
        foreign: list[str] = []
        for item in raw:
            if not isinstance(item, dict):
                raise ProxyHypothesisValidationError("candidate feature spec должен быть объектом")
            name = _clean(item.get("feature_name"), field_name="feature_name", limit=180, required=True)
            layer = _clean(item.get("source_layer"), field_name=f"{name}.source_layer", limit=80, required=True)
            contract = _clean(
                item.get("temporal_contract"),
                field_name=f"{name}.temporal_contract",
                limit=80,
                required=True,
            )
            if layer != "source_context":
                foreign.append(f"{name}:{layer}")
                continue
            if contract not in SUPPORTED_TEMPORAL_CONTRACTS:
                raise ProxyHypothesisValidationError(
                    f"Source Context materializer не поддерживает temporal_contract={contract!r} для {name}; "
                    "published_at_lte_cutoff требует отдельного public-signal adapter"
                )
            if name in specs:
                raise ProxyHypothesisValidationError(f"Дублирующий feature_name: {name}")
            specs[name] = dict(item)
        if foreign:
            raise ProxyHypothesisValidationError(
                "Source Context materializer не принимает mixed-source plan; чужие features: "
                + ", ".join(sorted(foreign))
            )
        return specs

    @staticmethod
    def _canonical_name(feature_name: str) -> str:
        return FEATURE_ALIASES.get(feature_name, feature_name)

    def _project_exists(self, project_id: str) -> bool:
        return self.conn.execute(
            "SELECT 1 FROM source_context_projects WHERE project_id = ? LIMIT 1",
            [project_id],
        ).fetchone() is not None

    def _project_row(self, project_id: str) -> tuple | None:
        return self.conn.execute(
            """
            SELECT adaptation_format, planned_runtime_minutes,
                   planned_episode_count, planned_episode_runtime_minutes,
                   format_known_at, release_at
            FROM source_context_projects WHERE project_id = ?
            """,
            [project_id],
        ).fetchone()

    def _visible_link_evidence(self, project_id: str, cutoff_at: str) -> list[dict[str, Any]]:
        cutoff = _parse_datetime(cutoff_at, field_name="cutoff_at")
        rows = self.conn.execute(
            """
            SELECT work_id, relation_type, is_primary,
                   MIN(known_at) AS first_known_at
            FROM project_source_links
            WHERE project_id = ? AND known_at <= ?
            GROUP BY work_id, relation_type, is_primary
            ORDER BY work_id, relation_type, is_primary
            """,
            [project_id, cutoff],
        ).fetchall()
        return [
            {
                "work_id": str(row[0]),
                "relation_type": str(row[1]),
                "is_primary": bool(row[2]),
                "known_at": row[3],
            }
            for row in rows
        ]

    def _creator_timestamp(self, work_ids: list[str], cutoff_at: str) -> datetime | None:
        if not work_ids:
            return None
        cutoff = _parse_datetime(cutoff_at, field_name="cutoff_at")
        row = self.conn.execute(
            """
            SELECT MAX(known_at)
            FROM source_work_creators
            WHERE work_id IN (SELECT * FROM UNNEST(?)) AND known_at <= ?
            """,
            [work_ids, cutoff],
        ).fetchone()
        return row[0] if row and row[0] is not None else None

    def _complexity_evidence(
        self,
        work_ids: list[str],
        cutoff_at: str,
        *,
        method: str,
        method_version: str,
    ) -> list[dict[str, Any]]:
        if not work_ids:
            return []
        cutoff = _parse_datetime(cutoff_at, field_name="cutoff_at")
        rows = self.conn.execute(
            """
            SELECT work_id, snapshot_id, known_at
            FROM source_complexity_snapshots
            WHERE work_id IN (SELECT * FROM UNNEST(?))
              AND method = ? AND method_version = ? AND known_at <= ?
            QUALIFY ROW_NUMBER() OVER (
                PARTITION BY work_id ORDER BY known_at DESC, snapshot_id DESC
            ) = 1
            ORDER BY work_id
            """,
            [work_ids, method, method_version, cutoff],
        ).fetchall()
        return [
            {"work_id": str(row[0]), "snapshot_id": str(row[1]), "known_at": row[2]}
            for row in rows
        ]

    @staticmethod
    def _max_timestamp(*groups: list[dict[str, Any]], extra: list[datetime] | None = None) -> datetime | None:
        values: list[datetime] = []
        for group in groups:
            for item in group:
                value = item.get("known_at")
                if isinstance(value, datetime):
                    values.append(value)
        if extra:
            values.extend(value for value in extra if isinstance(value, datetime))
        return max(values) if values else None

    @staticmethod
    def _provenance(project_id: str, feature_name: str, parts: list[str]) -> str:
        payload = {
            "project_id": project_id,
            "feature_name": feature_name,
            "parts": sorted(set(parts)),
        }
        return f"source-context:{project_id}:{feature_name}:{_fingerprint(payload)[:20]}"

    def _materialize_target(
        self,
        *,
        target_id: str,
        project_id: str,
        cutoff_at: str,
        specs: dict[str, dict[str, Any]],
        complexity_method: str | None,
        complexity_method_version: str | None,
    ) -> list[dict[str, Any]]:
        if not self._project_exists(project_id):
            return [
                self._missing(target_id, name, spec, "source_project_not_found")
                for name, spec in sorted(specs.items())
            ]

        cutoff = _parse_datetime(cutoff_at, field_name="cutoff_at")
        project = self._project_row(project_id)
        assert project is not None
        format_known_at = project[4]
        format_visible = bool(format_known_at is not None and format_known_at <= cutoff)
        links = self._visible_link_evidence(project_id, cutoff_at)
        work_ids = sorted({item["work_id"] for item in links})
        link_timestamp = self._max_timestamp(links)
        creator_timestamp = self._creator_timestamp(work_ids, cutoff_at)

        base = self.store.features_as_of(project_id, cutoff_at)
        pressure = self.pressure.features_as_of(project_id, cutoff_at)
        merged: dict[str, float] = {**base, **pressure}

        complexity_values: dict[str, float] = {}
        complexity_evidence: list[dict[str, Any]] = []
        if any(self._canonical_name(name).startswith("source_complexity_") for name in specs):
            assert complexity_method is not None and complexity_method_version is not None
            complexity_values = self.complexity.features_as_of(
                project_id,
                cutoff_at,
                method=complexity_method,
                method_version=complexity_method_version,
            )
            merged.update(complexity_values)
            complexity_evidence = self._complexity_evidence(
                work_ids,
                cutoff_at,
                method=complexity_method,
                method_version=complexity_method_version,
            )

        rows: list[dict[str, Any]] = []
        for requested_name, spec in sorted(specs.items()):
            canonical = self._canonical_name(requested_name)
            if canonical not in merged:
                raise ProxyHypothesisValidationError(
                    f"Source Context materializer не знает feature {requested_name!r} "
                    f"(canonical={canonical!r})"
                )
            available, reason = self._availability(
                canonical,
                merged=merged,
                project=project,
                format_visible=format_visible,
                links=links,
                complexity_values=complexity_values,
            )
            if not available:
                rows.append(self._missing(target_id, requested_name, spec, reason or "source_value_missing"))
                continue

            timestamp, parts = self._proof(
                canonical,
                project_id=project_id,
                project=project,
                format_visible=format_visible,
                links=links,
                link_timestamp=link_timestamp,
                creator_timestamp=creator_timestamp,
                complexity_evidence=complexity_evidence,
                complexity_method=complexity_method,
                complexity_method_version=complexity_method_version,
            )
            if timestamp is None:
                rows.append(self._missing(target_id, requested_name, spec, "temporal_proof_missing"))
                continue
            rows.append(
                {
                    "target_id": target_id,
                    "feature_name": requested_name,
                    "source_layer": "source_context",
                    "temporal_contract": spec["temporal_contract"],
                    "available": True,
                    "value": float(merged[canonical]),
                    "provenance_id": self._provenance(project_id, requested_name, parts),
                    "source_timestamp": timestamp.astimezone(timezone.utc).isoformat(),
                }
            )
        return rows

    @staticmethod
    def _availability(
        name: str,
        *,
        merged: dict[str, float],
        project: tuple,
        format_visible: bool,
        links: list[dict[str, Any]],
        complexity_values: dict[str, float],
    ) -> tuple[bool, str | None]:
        has_links = bool(links)
        if name in _PROJECT_FORMAT_FEATURES or name.startswith("source_adaptation_format_"):
            if not format_visible:
                return False, "format_not_known_by_cutoff"
            if name == "source_planned_runtime_minutes" and project[1] is None:
                return False, "planned_runtime_missing"
            if name == "source_planned_episode_count" and project[2] is None:
                return False, "planned_episode_count_missing"
            if name == "source_planned_episode_runtime_minutes" and project[3] is None:
                return False, "planned_episode_runtime_missing"
            if name == "source_planned_total_runtime_minutes":
                if project[1] is None and not (project[2] is not None and project[3] is not None):
                    return False, "planned_total_runtime_missing"
            return True, None

        if name in _PRESSURE_FORMAT_FEATURES:
            if not format_visible:
                return False, "format_not_known_by_cutoff"
            if not has_links:
                return False, "source_links_missing"
            if name.startswith("source_age_at_release_years_") and merged.get(
                "source_age_at_release_known_ratio", 0.0
            ) <= 0:
                return False, "source_publication_date_missing"
            if name == "source_runtime_per_linked_work_minutes" and merged.get(
                "source_runtime_per_linked_work_known", 0.0
            ) <= 0:
                return False, "runtime_per_work_missing"
            if name == "source_runtime_per_primary_work_minutes" and merged.get(
                "source_runtime_per_primary_work_known", 0.0
            ) <= 0:
                return False, "runtime_per_primary_missing"
            if name == "source_episodes_per_linked_work" and merged.get(
                "source_episodes_per_linked_work_known", 0.0
            ) <= 0:
                return False, "episodes_per_work_missing"
            return True, None

        if name.startswith("source_complexity_"):
            if not has_links:
                return False, "source_links_missing"
            meta = {
                "source_complexity_linked_work_count",
                "source_complexity_measured_work_count",
                "source_complexity_work_coverage_ratio",
                "source_complexity_measurement_coverage_mean",
            }
            if name in meta:
                return True, None
            if name.endswith("_known_ratio"):
                return True, None
            if name.endswith("_mean") or name.endswith("_max"):
                prefix = name.rsplit("_", 1)[0]
                if complexity_values.get(f"{prefix}_known_ratio", 0.0) <= 0:
                    return False, "complexity_metric_missing"
                return True, None
            return False, "unsupported_complexity_feature_shape"

        if not has_links:
            return False, "source_links_missing"
        if name in _LINK_ALWAYS_FEATURES or name.startswith("source_type_") or name.startswith("source_relation_"):
            return True, None
        if name.startswith("source_age_years_") and merged.get("source_age_known_ratio", 0.0) <= 0:
            return False, "source_publication_date_missing"
        if name.startswith("source_series_size_") and name not in {"source_series_size_known_ratio"}:
            if merged.get("source_series_size_known_ratio", 0.0) <= 0:
                return False, "source_series_size_missing"
        if name == "source_series_position_mean":
            # В base API нет отдельного ratio: нулевое значение не доказывает наличие position.
            return (merged.get(name, 0.0) > 0.0, None if merged.get(name, 0.0) > 0 else "source_series_position_missing")
        if name == "source_context_known":
            return True, None
        return True, None

    def _proof(
        self,
        name: str,
        *,
        project_id: str,
        project: tuple,
        format_visible: bool,
        links: list[dict[str, Any]],
        link_timestamp: datetime | None,
        creator_timestamp: datetime | None,
        complexity_evidence: list[dict[str, Any]],
        complexity_method: str | None,
        complexity_method_version: str | None,
    ) -> tuple[datetime | None, list[str]]:
        parts: list[str] = []
        timestamps: list[datetime] = []
        if links:
            parts.extend(
                f"link:{item['work_id']}:{item['relation_type']}:{int(item['is_primary'])}:{item['known_at'].isoformat()}"
                for item in links
            )
            if link_timestamp is not None:
                timestamps.append(link_timestamp)

        if name in _PROJECT_FORMAT_FEATURES or name.startswith("source_adaptation_format_") or name in _PRESSURE_FORMAT_FEATURES:
            if format_visible and isinstance(project[4], datetime):
                timestamps.append(project[4])
                parts.append(f"project-format:{project_id}:{project[4].isoformat()}")

        if name == "source_creator_count" and creator_timestamp is not None:
            timestamps.append(creator_timestamp)
            parts.append(f"creator-links-max:{creator_timestamp.isoformat()}")

        if name.startswith("source_complexity_"):
            for item in complexity_evidence:
                timestamps.append(item["known_at"])
                parts.append(
                    f"complexity:{item['work_id']}:{item['snapshot_id']}:{item['known_at'].isoformat()}"
                )
            parts.append(f"complexity-protocol:{complexity_method}:{complexity_method_version}")

        if not timestamps and format_visible and isinstance(project[4], datetime):
            # Project-level record is the minimal provenance anchor when a feature
            # itself is a project-format fact with no source links.
            timestamps.append(project[4])
            parts.append(f"project-format:{project_id}:{project[4].isoformat()}")
        return (max(timestamps) if timestamps else None, parts)

    @staticmethod
    def _missing(
        target_id: str,
        feature_name: str,
        spec: dict[str, Any],
        reason: str,
    ) -> dict[str, Any]:
        return {
            "target_id": target_id,
            "feature_name": feature_name,
            "source_layer": "source_context",
            "temporal_contract": spec["temporal_contract"],
            "available": False,
            "value": None,
            "missing_reason": reason,
        }
