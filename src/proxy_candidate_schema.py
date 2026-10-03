from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from src.proxy_ablation_gate import ProxyAblationResultGate
from src.proxy_hypotheses import ProxyHypothesisStore, ProxyHypothesisValidationError


CANDIDATE_SCHEMA_REGISTRY_VERSION = 1
CANDIDATE_SCHEMA_STATUS = "frozen_research_candidate"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _canonical_json(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _fingerprint(payload: Any) -> str:
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def _clean(value: Any, *, field_name: str, limit: int = 300, required: bool = False) -> str:
    text = " ".join(str(value or "").strip().split())
    if required and not text:
        raise ProxyHypothesisValidationError(f"{field_name} не должен быть пустым")
    if len(text) > limit:
        raise ProxyHypothesisValidationError(
            f"{field_name} длиннее допустимых {limit} символов"
        )
    return text


def _positive_int(value: Any, *, field_name: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ProxyHypothesisValidationError(f"{field_name} должен быть целым числом") from exc
    if parsed < 1:
        raise ProxyHypothesisValidationError(f"{field_name} должен быть >= 1")
    return parsed


class ProxyCandidateSchemaRegistry:
    """Freeze accepted P6 hypotheses в воспроизводимый research candidate schema.

    Registry не добавляет признаки в CatBoost. Он лишь фиксирует совместимый набор
    feature contracts, accepted gate fingerprints и общий temporal dataset/holdout,
    на котором отдельные hypotheses действительно прошли ablation.
    """

    def __init__(self, store: ProxyHypothesisStore | str | Path) -> None:
        if isinstance(store, ProxyHypothesisStore):
            self.store = store
            self._owns_store = False
        else:
            self.store = ProxyHypothesisStore(store)
            self._owns_store = True
        self.conn = self.store.conn
        # #71 остаётся compatibility/history schema. evaluate() отключён, но
        # constructor безопасно гарантирует наличие proxy_ablation_results.
        ProxyAblationResultGate(self.store)
        self._ensure_schema()

    def close(self) -> None:
        if self._owns_store:
            self.store.close()

    def __enter__(self) -> "ProxyCandidateSchemaRegistry":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def _ensure_schema(self) -> None:
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS proxy_candidate_schemas (
                schema_id VARCHAR PRIMARY KEY,
                schema_label VARCHAR NOT NULL UNIQUE,
                schema_version INTEGER NOT NULL UNIQUE,
                base_schema VARCHAR NOT NULL,
                dataset_fingerprint_sha256 VARCHAR NOT NULL,
                holdout_json VARCHAR NOT NULL,
                hypothesis_ids_json VARCHAR NOT NULL,
                acceptance_results_json VARCHAR NOT NULL,
                feature_contracts_json VARCHAR NOT NULL,
                schema_json VARCHAR NOT NULL,
                schema_fingerprint_sha256 VARCHAR NOT NULL UNIQUE,
                status VARCHAR NOT NULL,
                created_at TIMESTAMPTZ NOT NULL
            )
            """
        )

    @staticmethod
    def _normalize_hypothesis_ids(value: Any) -> list[str]:
        if not isinstance(value, (list, tuple)) or not value:
            raise ProxyHypothesisValidationError("hypothesis_ids должен быть непустым массивом")
        result = sorted(
            {
                _clean(item, field_name="hypothesis_ids[]", limit=180, required=True)
                for item in value
            }
        )
        if len(result) != len(value):
            raise ProxyHypothesisValidationError("hypothesis_ids не должен содержать дубликаты")
        return result

    def _accepted_result(self, hypothesis_id: str) -> dict[str, Any]:
        row = self.conn.execute(
            """
            SELECT
                result_id, plan_fingerprint_sha256, result_fingerprint_sha256,
                dataset_fingerprint_sha256, holdout_policy,
                holdout_start_year, holdout_end_year, holdout_row_count,
                baseline_mae, candidate_mae, mae_delta, verdict, recorded_at
            FROM proxy_ablation_results
            WHERE hypothesis_id = ? AND passed = TRUE
            ORDER BY recorded_at DESC, result_id DESC
            LIMIT 1
            """,
            [hypothesis_id],
        ).fetchone()
        if row is None:
            raise ProxyHypothesisValidationError(
                f"Accepted hypothesis {hypothesis_id} не имеет сохранённого passing ablation result"
            )
        return {
            "hypothesis_id": hypothesis_id,
            "result_id": str(row[0]),
            "plan_fingerprint_sha256": str(row[1]),
            "result_fingerprint_sha256": str(row[2]),
            "dataset_fingerprint_sha256": str(row[3]),
            "holdout": {
                "policy": str(row[4]),
                "start_year": int(row[5]),
                "end_year": int(row[6]),
                "row_count": int(row[7]),
            },
            "baseline_mae": float(row[8]),
            "candidate_mae": float(row[9]),
            "mae_delta": float(row[10]),
            "verdict": str(row[11]),
            "recorded_at": row[12].isoformat(),
        }

    def _validate_acceptance_is_current(
        self,
        hypothesis_id: str,
        accepted_result: dict[str, Any],
    ) -> dict[str, Any]:
        row = self.conn.execute(
            "SELECT status FROM proxy_hypotheses WHERE hypothesis_id = ?",
            [hypothesis_id],
        ).fetchone()
        if row is None:
            raise ProxyHypothesisValidationError(f"Неизвестная hypothesis: {hypothesis_id}")
        if str(row[0]) != "accepted":
            raise ProxyHypothesisValidationError(
                f"Hypothesis {hypothesis_id} должна иметь status=accepted, сейчас {row[0]}"
            )

        # После acceptance export plan отличается только status. Восстанавливаем
        # preregistered ablation_ready payload и проверяем fingerprint результата.
        current_plan = self.store.export_ablation_plan(hypothesis_id)
        reconstructed = dict(current_plan)
        reconstructed.pop("plan_fingerprint_sha256", None)
        reconstructed["status"] = "ablation_ready"
        reconstructed_fp = _fingerprint(reconstructed)
        if reconstructed_fp != accepted_result["plan_fingerprint_sha256"]:
            raise ProxyHypothesisValidationError(
                f"Hypothesis {hypothesis_id}: registry contract изменился после accepted ablation; "
                "нужна повторная preregistered проверка"
            )
        return current_plan

    def _feature_rows(self, hypothesis_id: str) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT feature_name, source_layer, temporal_contract, proxy_role,
                   coverage_feature
            FROM proxy_feature_specs
            WHERE hypothesis_id = ?
            ORDER BY feature_name
            """,
            [hypothesis_id],
        ).fetchall()
        if not rows:
            raise ProxyHypothesisValidationError(
                f"Hypothesis {hypothesis_id} не содержит proxy features"
            )
        return [
            {
                "feature_name": str(row[0]),
                "source_layer": str(row[1]),
                "temporal_contract": str(row[2]),
                "proxy_role": str(row[3]),
                "coverage_feature": str(row[4]) if row[4] is not None else None,
            }
            for row in rows
        ]

    @staticmethod
    def _merge_feature_contract(
        merged: dict[str, dict[str, Any]],
        *,
        feature_name: str,
        source_layer: str,
        temporal_contract: str,
        role: str,
        hypothesis_id: str,
        result_fingerprint: str,
        coverage_for: str | None = None,
    ) -> None:
        existing = merged.get(feature_name)
        if existing is None:
            existing = {
                "feature_name": feature_name,
                "source_layer": source_layer,
                "temporal_contract": temporal_contract,
                "proxy_roles": set(),
                "supporting_hypothesis_ids": set(),
                "acceptance_result_fingerprints": set(),
                "coverage_for_features": set(),
            }
            merged[feature_name] = existing
        elif (
            existing["source_layer"] != source_layer
            or existing["temporal_contract"] != temporal_contract
        ):
            raise ProxyHypothesisValidationError(
                f"Feature {feature_name} имеет конфликтующий contract: "
                f"{existing['source_layer']}/{existing['temporal_contract']} vs "
                f"{source_layer}/{temporal_contract}"
            )
        existing["proxy_roles"].add(role)
        existing["supporting_hypothesis_ids"].add(hypothesis_id)
        existing["acceptance_result_fingerprints"].add(result_fingerprint)
        if coverage_for:
            existing["coverage_for_features"].add(coverage_for)

    def freeze(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise ProxyHypothesisValidationError("candidate schema payload должен быть JSON-объектом")
        schema_id = _clean(
            payload.get("schema_id") or f"proxy-schema-{uuid4().hex[:16]}",
            field_name="schema_id",
            limit=180,
            required=True,
        )
        schema_label = _clean(
            payload.get("schema_label"),
            field_name="schema_label",
            limit=180,
            required=True,
        )
        schema_version = _positive_int(payload.get("schema_version"), field_name="schema_version")
        hypothesis_ids = self._normalize_hypothesis_ids(payload.get("hypothesis_ids"))

        base_schemas: set[str] = set()
        accepted_results: list[dict[str, Any]] = []
        feature_contracts: dict[str, dict[str, Any]] = {}
        dataset_fingerprints: set[str] = set()
        holdouts: set[str] = set()

        for hypothesis_id in hypothesis_ids:
            accepted = self._accepted_result(hypothesis_id)
            plan = self._validate_acceptance_is_current(hypothesis_id, accepted)
            base_schema = str(plan["ablation"]["baseline_schema"])
            base_schemas.add(base_schema)
            accepted_results.append(accepted)
            dataset_fingerprints.add(accepted["dataset_fingerprint_sha256"])
            holdouts.add(_canonical_json(accepted["holdout"]))

            for spec in self._feature_rows(hypothesis_id):
                feature_name = spec["feature_name"]
                self._merge_feature_contract(
                    feature_contracts,
                    feature_name=feature_name,
                    source_layer=spec["source_layer"],
                    temporal_contract=spec["temporal_contract"],
                    role=spec["proxy_role"],
                    hypothesis_id=hypothesis_id,
                    result_fingerprint=accepted["result_fingerprint_sha256"],
                )
                coverage = spec["coverage_feature"]
                if coverage:
                    self._merge_feature_contract(
                        feature_contracts,
                        feature_name=coverage,
                        source_layer=spec["source_layer"],
                        temporal_contract=spec["temporal_contract"],
                        role="coverage",
                        hypothesis_id=hypothesis_id,
                        result_fingerprint=accepted["result_fingerprint_sha256"],
                        coverage_for=feature_name,
                    )

        if len(base_schemas) != 1:
            raise ProxyHypothesisValidationError(
                "Все hypotheses candidate schema должны иметь один baseline_schema: "
                + ", ".join(sorted(base_schemas))
            )
        if len(dataset_fingerprints) != 1:
            raise ProxyHypothesisValidationError(
                "Accepted hypotheses проверены на разных dataset fingerprints; "
                "перед объединением нужен повторный ablation на одном snapshot"
            )
        if len(holdouts) != 1:
            raise ProxyHypothesisValidationError(
                "Accepted hypotheses имеют разные temporal holdout; перед объединением нужна повторная проверка"
            )

        normalized_contracts: list[dict[str, Any]] = []
        for name in sorted(feature_contracts):
            item = feature_contracts[name]
            normalized_contracts.append(
                {
                    "feature_name": name,
                    "source_layer": item["source_layer"],
                    "temporal_contract": item["temporal_contract"],
                    "proxy_roles": sorted(item["proxy_roles"]),
                    "supporting_hypothesis_ids": sorted(item["supporting_hypothesis_ids"]),
                    "acceptance_result_fingerprints": sorted(
                        item["acceptance_result_fingerprints"]
                    ),
                    "coverage_for_features": sorted(item["coverage_for_features"]),
                }
            )

        accepted_results.sort(key=lambda item: item["hypothesis_id"])
        holdout = json.loads(next(iter(holdouts)))
        dataset_fingerprint = next(iter(dataset_fingerprints))
        body = {
            "registry_version": CANDIDATE_SCHEMA_REGISTRY_VERSION,
            "schema_id": schema_id,
            "schema_label": schema_label,
            "schema_version": schema_version,
            "status": CANDIDATE_SCHEMA_STATUS,
            "base_schema": next(iter(base_schemas)),
            "dataset_fingerprint_sha256": dataset_fingerprint,
            "holdout": holdout,
            "hypothesis_ids": hypothesis_ids,
            "accepted_results": accepted_results,
            "feature_contracts": normalized_contracts,
            "model_feature_names": [item["feature_name"] for item in normalized_contracts],
            "required_source_layers": sorted(
                {item["source_layer"] for item in normalized_contracts}
            ),
            "materializer_contract": "proxy_source_materializers_v1",
            "combined_ablation_required": len(hypothesis_ids) > 1,
            "production_quality_gate_required": True,
            "automatic_catboost_inclusion": False,
            "production_publication_allowed": False,
        }
        schema_fingerprint = _fingerprint(body)

        existing = self.conn.execute(
            """
            SELECT schema_json, schema_fingerprint_sha256, created_at
            FROM proxy_candidate_schemas WHERE schema_id = ?
            """,
            [schema_id],
        ).fetchone()
        if existing is not None:
            if str(existing[1]) != schema_fingerprint:
                raise ProxyHypothesisValidationError(
                    f"schema_id={schema_id} уже frozen с другим fingerprint"
                )
            result = json.loads(str(existing[0]))
            result["schema_fingerprint_sha256"] = str(existing[1])
            result["created_at"] = existing[2].isoformat()
            result["idempotent"] = True
            return result

        collision = self.conn.execute(
            """
            SELECT schema_id FROM proxy_candidate_schemas
            WHERE schema_label = ? OR schema_version = ?
            LIMIT 1
            """,
            [schema_label, schema_version],
        ).fetchone()
        if collision is not None:
            raise ProxyHypothesisValidationError(
                f"schema_label/schema_version уже занят schema_id={collision[0]}"
            )

        created_at = _now()
        schema_json = _canonical_json(body)
        self.conn.execute(
            """
            INSERT INTO proxy_candidate_schemas(
                schema_id, schema_label, schema_version, base_schema,
                dataset_fingerprint_sha256, holdout_json, hypothesis_ids_json,
                acceptance_results_json, feature_contracts_json, schema_json,
                schema_fingerprint_sha256, status, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                schema_id,
                schema_label,
                schema_version,
                body["base_schema"],
                dataset_fingerprint,
                _canonical_json(holdout),
                _canonical_json(hypothesis_ids),
                _canonical_json(accepted_results),
                _canonical_json(normalized_contracts),
                schema_json,
                schema_fingerprint,
                CANDIDATE_SCHEMA_STATUS,
                created_at,
            ],
        )
        result = dict(body)
        result["schema_fingerprint_sha256"] = schema_fingerprint
        result["created_at"] = created_at.isoformat()
        result["idempotent"] = False
        return result

    def get(self, schema_id: str) -> dict[str, Any]:
        schema_id = _clean(
            schema_id, field_name="schema_id", limit=180, required=True
        )
        row = self.conn.execute(
            """
            SELECT schema_json, schema_fingerprint_sha256, created_at
            FROM proxy_candidate_schemas WHERE schema_id = ?
            """,
            [schema_id],
        ).fetchone()
        if row is None:
            raise ProxyHypothesisValidationError(f"Неизвестный candidate schema: {schema_id}")
        payload = json.loads(str(row[0]))
        payload["schema_fingerprint_sha256"] = str(row[1])
        payload["created_at"] = row[2].isoformat()
        return payload

    def list_schemas(self) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT schema_id, schema_label, schema_version, base_schema,
                   schema_fingerprint_sha256, status, created_at
            FROM proxy_candidate_schemas
            ORDER BY schema_version, schema_id
            """
        ).fetchall()
        return [
            {
                "schema_id": str(row[0]),
                "schema_label": str(row[1]),
                "schema_version": int(row[2]),
                "base_schema": str(row[3]),
                "schema_fingerprint_sha256": str(row[4]),
                "status": str(row[5]),
                "created_at": row[6].isoformat(),
            }
            for row in rows
        ]
