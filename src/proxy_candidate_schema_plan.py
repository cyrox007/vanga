from __future__ import annotations

import hashlib
import json
from typing import Any

from src.proxy_candidate_schema import ProxyCandidateSchemaRegistry
from src.proxy_hypotheses import ProxyHypothesisValidationError


PLAN_VERSION = 1


def _canonical_json(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _fingerprint(payload: Any) -> str:
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


class ProxyCandidateSchemaPlanBuilder:
    """Строит preregistered combined plan из immutable candidate schema.

    Plan совместим с ProxySourceMaterializer/TemporalProxyAvailabilityAuditor/
    GenericProxyAblationGate, но не является proxy_hypothesis и поэтому не может
    быть передан в ProxyAblationPipeline для изменения hypothesis status.
    """

    def __init__(self, registry: ProxyCandidateSchemaRegistry) -> None:
        self.registry = registry

    def build(
        self,
        schema_id: str,
        *,
        require_improvement: bool = True,
        max_mae_regression: float = 0.0,
    ) -> dict[str, Any]:
        schema = self.registry.get(schema_id)
        try:
            max_regression = float(max_mae_regression)
        except (TypeError, ValueError) as exc:
            raise ProxyHypothesisValidationError(
                "max_mae_regression должен быть числом"
            ) from exc
        if not 0.0 <= max_regression <= 1.0:
            raise ProxyHypothesisValidationError(
                "max_mae_regression должен быть в диапазоне 0..1"
            )

        feature_specs: list[dict[str, Any]] = []
        for contract in schema["feature_contracts"]:
            roles = list(contract.get("proxy_roles") or [])
            if "coverage" in roles and len(roles) == 1:
                role = "coverage"
            elif "primary" in roles:
                role = "primary"
            elif roles:
                role = sorted(roles)[0]
            else:
                role = "context"
            feature_specs.append(
                {
                    "feature_name": contract["feature_name"],
                    "source_layer": contract["source_layer"],
                    "temporal_contract": contract["temporal_contract"],
                    "proxy_role": role,
                    # Frozen schema уже содержит coverage feature как отдельный
                    # model feature; повторно ссылаться на него здесь не нужно.
                    "coverage_feature": None,
                    "notes": f"frozen schema {schema['schema_id']}",
                }
            )

        body = {
            "registry_version": PLAN_VERSION,
            "hypothesis_id": f"candidate-schema:{schema['schema_id']}",
            "title": schema["schema_label"],
            "candidate_schema_id": schema["schema_id"],
            "candidate_schema_fingerprint_sha256": schema[
                "schema_fingerprint_sha256"
            ],
            "candidate_pre_release_features": sorted(
                feature_specs, key=lambda item: item["feature_name"]
            ),
            "ablation": {
                "baseline_schema": schema["base_schema"],
                "candidate_label": schema["schema_label"],
                "holdout_policy": schema["holdout"]["policy"],
                "primary_metric": "mae",
                "max_mae_regression": max_regression,
                "require_improvement": bool(require_improvement),
                "dataset_fingerprint_required": True,
                "preregistered_at": schema["created_at"],
            },
            "status": "candidate_schema_frozen",
            "expected_dataset_fingerprint_sha256": schema[
                "dataset_fingerprint_sha256"
            ],
            "source_hypothesis_ids": schema["hypothesis_ids"],
            "post_release_features_allowed": False,
            "expert_interpretation_as_feature": False,
            "automatic_catboost_inclusion": False,
            "production_publication_allowed": False,
        }
        plan = dict(body)
        plan["plan_fingerprint_sha256"] = _fingerprint(body)
        return plan
