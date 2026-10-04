from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from settings import config
from src.audience_signals import AudienceSignalStore
from src.production_context import ProductionContextStore
from src.proxy_ablation import verify_plan_fingerprint
from src.proxy_hypotheses import ProxyHypothesisValidationError
from src.proxy_materializers import (
    MATERIALIZER_VERSION,
    ProxySourceMaterializer,
    _available_timestamped,
    _iso,
    _missing,
    _parse_timestamp,
)
from src.source_context import SourceContextStore


AUDIENCE_FIELD_NAMES = {"value", "known", "age_days", "coverage", "sample_size"}
AUDIENCE_FEATURE_RE = re.compile(
    r"^audience__(?P<signal>[a-z0-9_]+)__(?P<method>[a-z0-9_]+)__"
    r"(?P<version>[a-z0-9_]+)__(?P<unit>[a-z0-9_]+)__(?P<field>value|known|age_days|coverage|sample_size)$"
)


def audience_feature_name(
    signal_type: str,
    method: str,
    method_version: str,
    unit: str,
    field: str = "value",
) -> str:
    """Каноническое имя: protocol metadata становится частью P6 plan fingerprint."""
    values = [signal_type, method, method_version, unit, field]
    if field not in AUDIENCE_FIELD_NAMES:
        raise ProxyHypothesisValidationError(f"Неизвестный audience field: {field}")
    for value in values:
        if not re.fullmatch(r"[a-z0-9_]+", str(value or "")):
            raise ProxyHypothesisValidationError(
                "Audience protocol components должны быть lowercase slug [a-z0-9_]"
            )
    return f"audience__{signal_type}__{method}__{method_version}__{unit}__{field}"


def parse_audience_feature_name(feature_name: str) -> dict[str, str]:
    match = AUDIENCE_FEATURE_RE.fullmatch(str(feature_name or ""))
    if match is None:
        raise ProxyHypothesisValidationError(
            "pre_release_public_signal feature должен использовать canonical имя: "
            "audience__<signal>__<method>__<version>__<unit>__<field>"
        )
    return match.groupdict()


class ProxyUnifiedMaterializer(ProxySourceMaterializer):
    """P6 materializer v2: Source/Production/IMDb + P8 audience signals."""

    def __init__(
        self,
        *,
        source_db_path: str | Path | None = None,
        production_db_path: str | Path | None = None,
        imdb_db_path: str | Path | None = None,
        audience_db_path: str | Path | None = None,
    ) -> None:
        super().__init__(
            source_db_path=source_db_path,
            production_db_path=production_db_path,
            imdb_db_path=imdb_db_path,
        )
        self.audience_db_path = Path(
            audience_db_path or config.AUDIENCE_SIGNALS_DB_PATH
        )

    @classmethod
    def _normalize_targets(
        cls,
        payload: dict[str, Any] | list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        normalized = super()._normalize_targets(payload)
        raw_targets = payload if isinstance(payload, list) else payload.get("targets")
        raw_by_id = {
            str(item.get("target_id")): item
            for item in raw_targets
            if isinstance(item, dict)
        }
        for item in normalized:
            raw = raw_by_id.get(item["target_id"]) or {}
            explicit = str(raw.get("audience_project_id") or "").strip()
            item["audience_project_id"] = explicit or None
        return normalized

    def materialize(
        self,
        plan: dict[str, Any],
        targets_payload: dict[str, Any] | list[dict[str, Any]],
    ) -> dict[str, Any]:
        plan_fingerprint = verify_plan_fingerprint(plan)
        specs = self._feature_specs(plan)
        targets = self._normalize_targets(targets_payload)
        observations: list[dict[str, Any]] = []

        source_store = SourceContextStore(self.source_db_path)
        production_store = ProductionContextStore(self.production_db_path)
        audience_store = AudienceSignalStore(self.audience_db_path)
        try:
            for target in targets:
                for spec in specs:
                    layer = str(spec.get("source_layer") or "").strip()
                    if layer == "source_context":
                        row = self._source_context_observation(source_store, target, spec)
                    elif layer == "production_context":
                        row = self._production_context_observation(
                            production_store, target, spec
                        )
                    elif layer == "imdb_history":
                        row = self._imdb_history_observation(source_store, target, spec)
                    elif layer == "pre_release_public_signal":
                        row = self._audience_observation(audience_store, target, spec)
                    else:
                        raise ProxyHypothesisValidationError(
                            f"Неизвестный source_layer={layer!r}"
                        )
                    observations.append(row)
        finally:
            audience_store.close()
            production_store.close()
            source_store.close()

        public_targets = [
            {
                "target_id": item["target_id"],
                "target_year": item["target_year"],
                "cutoff_at": item["cutoff_at"],
                "release_at": item["release_at"],
            }
            for item in targets
        ]
        return {
            "version": MATERIALIZER_VERSION,
            "plan_fingerprint_sha256": plan_fingerprint,
            "targets": public_targets,
            "observations": observations,
            "materializer": {
                "version": 2,
                "supported_source_layers": [
                    "imdb_history",
                    "source_context",
                    "production_context",
                    "pre_release_public_signal",
                ],
                "audience_feature_contract": "canonical_protocol_in_feature_name_v1",
            },
        }

    def _resolve_audience_project_id(
        self,
        store: AudienceSignalStore,
        target: dict[str, Any],
    ) -> str | None:
        explicit = target.get("audience_project_id")
        if explicit:
            row = store.conn.execute(
                "SELECT project_id FROM audience_signal_projects WHERE project_id=?",
                [explicit],
            ).fetchone()
            return str(row[0]) if row else None
        rows = store.conn.execute(
            """
            SELECT project_id FROM audience_signal_projects
            WHERE imdb_id=? ORDER BY project_id LIMIT 2
            """,
            [target["target_id"]],
        ).fetchall()
        if len(rows) > 1:
            raise ProxyHypothesisValidationError(
                f"Для {target['target_id']} найдено несколько audience project_id; укажите явно"
            )
        return str(rows[0][0]) if rows else None

    def _audience_observation(
        self,
        store: AudienceSignalStore,
        target: dict[str, Any],
        spec: dict[str, Any],
    ) -> dict[str, Any]:
        target_id = target["target_id"]
        feature_name = str(spec["feature_name"])
        source_layer = str(spec["source_layer"])
        contract = str(spec["temporal_contract"])
        if contract != "known_at_lte_cutoff":
            raise ProxyHypothesisValidationError(
                "P8 adapter v1 поддерживает только known_at_lte_cutoff; published_at не подменяется known_at"
            )
        parsed = parse_audience_feature_name(feature_name)
        cutoff = _parse_timestamp(target["cutoff_at"], field_name="cutoff_at")
        release_at = _parse_timestamp(target["release_at"], field_name="release_at")
        project_id = self._resolve_audience_project_id(store, target)
        if not project_id:
            return _missing(
                target_id=target_id,
                feature_name=feature_name,
                source_layer=source_layer,
                temporal_contract=contract,
                reason="audience signal project не найден",
            )

        visible = store.observations_as_of(
            project_id,
            cutoff,
            release_at=release_at,
        )
        matches = [
            row
            for row in visible
            if row["signal_type"] == parsed["signal"]
            and row["method"] == parsed["method"]
            and row["method_version"] == parsed["version"]
            and row["unit"] == parsed["unit"]
        ]
        matches.sort(key=lambda row: (row["known_at"], row["observation_id"]))
        latest = matches[-1] if matches else None
        field = parsed["field"]

        if field == "known":
            value = 1.0 if latest else 0.0
            timestamp = (
                _parse_timestamp(latest["known_at"], field_name="known_at")
                if latest
                else cutoff
            )
            provenance = (
                f"audience:{project_id}:{latest['observation_id']}"
                if latest
                else f"audience:{project_id}:no_observation_as_of:{_iso(cutoff)}"
            )
            return _available_timestamped(
                target_id=target_id,
                feature_name=feature_name,
                source_layer=source_layer,
                temporal_contract=contract,
                value=value,
                provenance_id=provenance,
                source_timestamp=timestamp,
            )

        if latest is None:
            return _missing(
                target_id=target_id,
                feature_name=feature_name,
                source_layer=source_layer,
                temporal_contract=contract,
                reason="нет observation exact audience protocol на cutoff",
            )

        observed = _parse_timestamp(latest["observed_at"], field_name="observed_at")
        known = _parse_timestamp(latest["known_at"], field_name="known_at")
        if field == "value":
            value = float(latest["value"])
        elif field == "age_days":
            value = max(0.0, (cutoff - observed).total_seconds() / 86400.0)
        elif field == "coverage":
            if latest["coverage_fraction"] is None:
                return _missing(
                    target_id=target_id,
                    feature_name=feature_name,
                    source_layer=source_layer,
                    temporal_contract=contract,
                    reason="coverage_fraction отсутствует у audience observation",
                )
            value = float(latest["coverage_fraction"])
        elif field == "sample_size":
            if latest["sample_size"] is None:
                return _missing(
                    target_id=target_id,
                    feature_name=feature_name,
                    source_layer=source_layer,
                    temporal_contract=contract,
                    reason="sample_size отсутствует у audience observation",
                )
            value = float(latest["sample_size"])
        else:
            raise ProxyHypothesisValidationError(f"Неизвестный audience field: {field}")

        return _available_timestamped(
            target_id=target_id,
            feature_name=feature_name,
            source_layer=source_layer,
            temporal_contract=contract,
            value=value,
            provenance_id=f"audience:{project_id}:{latest['observation_id']}:{latest['source_id']}",
            source_timestamp=known,
        )
