from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any

from src.proxy_hypotheses import ProxyHypothesisValidationError


PROXY_AUDIT_VERSION = 1
PROXY_ABLATION_GATE_VERSION = 1

COMPARABLE_RESULT_KEYS = (
    "train_year_from",
    "train_year_to",
    "test_year_from",
    "test_year_to",
    "train_rows",
    "test_rows",
    "total_rows",
)


def _canonical_json(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _fingerprint(payload: Any) -> str:
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def _clean(value: Any, *, field_name: str, limit: int = 500, required: bool = False) -> str:
    text = " ".join(str(value or "").strip().split())
    if required and not text:
        raise ProxyHypothesisValidationError(f"{field_name} не должен быть пустым")
    if len(text) > limit:
        raise ProxyHypothesisValidationError(
            f"{field_name} длиннее допустимых {limit} символов"
        )
    return text


def _parse_timestamp(value: Any, *, field_name: str) -> datetime:
    text = _clean(value, field_name=field_name, limit=80, required=True)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ProxyHypothesisValidationError(
            f"{field_name} должен быть ISO-8601 timestamp"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ProxyHypothesisValidationError(
            f"{field_name} должен содержать timezone offset"
        )
    return parsed


def verify_plan_fingerprint(plan: dict[str, Any]) -> str:
    if not isinstance(plan, dict):
        raise ProxyHypothesisValidationError("Ablation plan должен быть JSON-объектом")
    supplied = _clean(
        plan.get("plan_fingerprint_sha256"),
        field_name="plan_fingerprint_sha256",
        limit=64,
        required=True,
    ).lower()
    if len(supplied) != 64 or any(char not in "0123456789abcdef" for char in supplied):
        raise ProxyHypothesisValidationError(
            "plan_fingerprint_sha256 должен быть SHA-256 hex"
        )
    canonical = dict(plan)
    canonical.pop("plan_fingerprint_sha256", None)
    actual = _fingerprint(canonical)
    if supplied != actual:
        raise ProxyHypothesisValidationError(
            "Ablation plan fingerprint mismatch: plan изменён после preregistration"
        )
    return supplied


def _feature_specs(plan: dict[str, Any]) -> dict[str, dict[str, Any]]:
    raw = plan.get("candidate_pre_release_features") or []
    if not isinstance(raw, list) or not raw:
        raise ProxyHypothesisValidationError(
            "Ablation plan не содержит candidate_pre_release_features"
        )
    result: dict[str, dict[str, Any]] = {}
    for item in raw:
        if not isinstance(item, dict):
            raise ProxyHypothesisValidationError(
                "candidate_pre_release_features[] должен содержать JSON-объекты"
            )
        name = _clean(
            item.get("feature_name"),
            field_name="feature_name",
            limit=180,
            required=True,
        )
        if name in result:
            raise ProxyHypothesisValidationError(
                f"Дублирующийся feature_name в plan: {name}"
            )
        result[name] = dict(item)
    return result


class TemporalProxyAvailabilityAuditor:
    """Проверяет фактическую temporal-доступность materialized proxy rows.

    Registry доказывает только допустимость *контракта*. Этот auditor проверяет
    конкретную материализацию: target cutoff/release, timestamp факта или год
    historical observation, provenance и явное missingness для каждой пары
    target×feature. Без успешного audit candidate ablation не считается валидным.
    """

    @classmethod
    def audit(
        cls,
        plan: dict[str, Any],
        materialization: dict[str, Any],
    ) -> dict[str, Any]:
        plan_fingerprint = verify_plan_fingerprint(plan)
        specs = _feature_specs(plan)
        if not isinstance(materialization, dict):
            raise ProxyHypothesisValidationError(
                "Proxy materialization должен быть JSON-объектом"
            )
        try:
            version = int(materialization.get("version", PROXY_AUDIT_VERSION))
        except (TypeError, ValueError) as exc:
            raise ProxyHypothesisValidationError(
                "materialization.version должен быть целым"
            ) from exc
        if version != PROXY_AUDIT_VERSION:
            raise ProxyHypothesisValidationError(
                f"Поддерживается materialization version={PROXY_AUDIT_VERSION}"
            )

        raw_targets = materialization.get("targets") or []
        raw_observations = materialization.get("observations") or []
        if not isinstance(raw_targets, list) or not raw_targets:
            raise ProxyHypothesisValidationError("materialization.targets не должен быть пустым")
        if not isinstance(raw_observations, list):
            raise ProxyHypothesisValidationError("materialization.observations должен быть массивом")

        targets: dict[str, dict[str, Any]] = {}
        normalized_targets: list[dict[str, Any]] = []
        violations: list[dict[str, Any]] = []
        for raw in raw_targets:
            if not isinstance(raw, dict):
                raise ProxyHypothesisValidationError("targets[] должен содержать JSON-объекты")
            target_id = _clean(
                raw.get("target_id"),
                field_name="target_id",
                limit=180,
                required=True,
            )
            if target_id in targets:
                raise ProxyHypothesisValidationError(f"Дублирующийся target_id: {target_id}")
            try:
                target_year = int(raw.get("target_year"))
            except (TypeError, ValueError) as exc:
                raise ProxyHypothesisValidationError(
                    f"target_year для {target_id} должен быть целым"
                ) from exc
            cutoff = _parse_timestamp(raw.get("cutoff_at"), field_name=f"{target_id}.cutoff_at")
            release = _parse_timestamp(raw.get("release_at"), field_name=f"{target_id}.release_at")
            if cutoff >= release:
                violations.append(
                    {
                        "code": "cutoff_not_pre_release",
                        "target_id": target_id,
                        "feature_name": None,
                        "detail": "cutoff_at должен быть строго раньше release_at",
                    }
                )
            normalized = {
                "target_id": target_id,
                "target_year": target_year,
                "cutoff_at": cutoff.isoformat(),
                "release_at": release.isoformat(),
            }
            targets[target_id] = {
                **normalized,
                "cutoff_dt": cutoff,
                "release_dt": release,
            }
            normalized_targets.append(normalized)

        seen_pairs: set[tuple[str, str]] = set()
        normalized_observations: list[dict[str, Any]] = []
        available_counts = {name: 0 for name in specs}
        missing_counts = {name: 0 for name in specs}

        for raw in raw_observations:
            if not isinstance(raw, dict):
                raise ProxyHypothesisValidationError(
                    "observations[] должен содержать JSON-объекты"
                )
            target_id = _clean(
                raw.get("target_id"),
                field_name="observation.target_id",
                limit=180,
                required=True,
            )
            feature_name = _clean(
                raw.get("feature_name"),
                field_name="observation.feature_name",
                limit=180,
                required=True,
            )
            pair = (target_id, feature_name)
            if pair in seen_pairs:
                raise ProxyHypothesisValidationError(
                    f"Дублирующая materialized row: {target_id}/{feature_name}"
                )
            seen_pairs.add(pair)

            target = targets.get(target_id)
            spec = specs.get(feature_name)
            if target is None:
                violations.append(
                    {
                        "code": "unknown_target",
                        "target_id": target_id,
                        "feature_name": feature_name,
                        "detail": "observation ссылается на target вне manifest",
                    }
                )
                continue
            if spec is None:
                violations.append(
                    {
                        "code": "feature_not_preregistered",
                        "target_id": target_id,
                        "feature_name": feature_name,
                        "detail": "feature отсутствует в preregistered plan",
                    }
                )
                continue

            source_layer = _clean(
                raw.get("source_layer"),
                field_name="observation.source_layer",
                limit=80,
                required=True,
            )
            temporal_contract = _clean(
                raw.get("temporal_contract"),
                field_name="observation.temporal_contract",
                limit=80,
                required=True,
            )
            if source_layer != spec.get("source_layer"):
                violations.append(
                    {
                        "code": "source_layer_mismatch",
                        "target_id": target_id,
                        "feature_name": feature_name,
                        "detail": f"ожидался {spec.get('source_layer')}, получен {source_layer}",
                    }
                )
            if temporal_contract != spec.get("temporal_contract"):
                violations.append(
                    {
                        "code": "temporal_contract_mismatch",
                        "target_id": target_id,
                        "feature_name": feature_name,
                        "detail": f"ожидался {spec.get('temporal_contract')}, получен {temporal_contract}",
                    }
                )

            available = raw.get("available")
            if not isinstance(available, bool):
                raise ProxyHypothesisValidationError(
                    f"available для {target_id}/{feature_name} должен быть boolean"
                )
            value = raw.get("value")
            normalized: dict[str, Any] = {
                "target_id": target_id,
                "feature_name": feature_name,
                "source_layer": source_layer,
                "temporal_contract": temporal_contract,
                "available": available,
                "value": value,
            }

            if not available:
                if value is not None:
                    violations.append(
                        {
                            "code": "missing_row_has_value",
                            "target_id": target_id,
                            "feature_name": feature_name,
                            "detail": "available=false требует value=null",
                        }
                    )
                missing_reason = _clean(
                    raw.get("missing_reason"),
                    field_name="missing_reason",
                    limit=500,
                    required=True,
                )
                normalized["missing_reason"] = missing_reason
                missing_counts[feature_name] += 1
                normalized_observations.append(normalized)
                continue

            if value is None:
                violations.append(
                    {
                        "code": "available_row_missing_value",
                        "target_id": target_id,
                        "feature_name": feature_name,
                        "detail": "available=true требует ненулевой materialized value",
                    }
                )
            provenance_id = _clean(
                raw.get("provenance_id"),
                field_name="provenance_id",
                limit=500,
                required=True,
            )
            normalized["provenance_id"] = provenance_id
            available_counts[feature_name] += 1

            if temporal_contract == "history_before_target_year":
                try:
                    history_year = int(raw.get("history_year"))
                except (TypeError, ValueError) as exc:
                    raise ProxyHypothesisValidationError(
                        f"history_year обязателен для {target_id}/{feature_name}"
                    ) from exc
                normalized["history_year"] = history_year
                if history_year >= int(target["target_year"]):
                    violations.append(
                        {
                            "code": "historical_leakage",
                            "target_id": target_id,
                            "feature_name": feature_name,
                            "detail": (
                                f"history_year={history_year} должен быть < "
                                f"target_year={target['target_year']}"
                            ),
                        }
                    )
            else:
                source_timestamp = _parse_timestamp(
                    raw.get("source_timestamp"),
                    field_name=f"{target_id}/{feature_name}.source_timestamp",
                )
                normalized["source_timestamp"] = source_timestamp.isoformat()
                if source_timestamp > target["cutoff_dt"]:
                    violations.append(
                        {
                            "code": "timestamp_after_cutoff",
                            "target_id": target_id,
                            "feature_name": feature_name,
                            "detail": (
                                f"source_timestamp={source_timestamp.isoformat()} позже "
                                f"cutoff_at={target['cutoff_at']}"
                            ),
                        }
                    )

            normalized_observations.append(normalized)

        for target_id in sorted(targets):
            for feature_name in sorted(specs):
                if (target_id, feature_name) not in seen_pairs:
                    violations.append(
                        {
                            "code": "observation_missing",
                            "target_id": target_id,
                            "feature_name": feature_name,
                            "detail": "для каждой пары target×feature нужна явная available/missing row",
                        }
                    )

        target_count = len(targets)
        coverage = {
            feature_name: {
                "available_count": available_counts[feature_name],
                "missing_count": missing_counts[feature_name],
                "target_count": target_count,
                "coverage_ratio": round(
                    available_counts[feature_name] / target_count if target_count else 0.0,
                    4,
                ),
            }
            for feature_name in sorted(specs)
        }
        normalized_materialization = {
            "version": version,
            "plan_fingerprint_sha256": plan_fingerprint,
            "targets": sorted(normalized_targets, key=lambda item: item["target_id"]),
            "observations": sorted(
                normalized_observations,
                key=lambda item: (item["target_id"], item["feature_name"]),
            ),
        }
        materialization_fingerprint = _fingerprint(normalized_materialization)
        report_without_fingerprint = {
            "version": PROXY_AUDIT_VERSION,
            "hypothesis_id": plan.get("hypothesis_id"),
            "plan_fingerprint_sha256": plan_fingerprint,
            "materialization_fingerprint_sha256": materialization_fingerprint,
            "target_count": target_count,
            "feature_count": len(specs),
            "coverage": coverage,
            "violation_count": len(violations),
            "violations": violations,
            "passed": not violations,
            "research_only": True,
        }
        return {
            **report_without_fingerprint,
            "audit_fingerprint_sha256": _fingerprint(report_without_fingerprint),
        }


class GenericProxyAblationGate:
    """Сравнивает baseline/candidate только при одном plan/audit/dataset/holdout.

    Тяжёлое обучение остаётся в существующем training pipeline. Gate намеренно
    принимает только его JSON metrics и не публикует модель.
    """

    @classmethod
    def compare(
        cls,
        plan: dict[str, Any],
        audit_report: dict[str, Any],
        baseline: dict[str, Any],
        candidate: dict[str, Any],
    ) -> dict[str, Any]:
        plan_fingerprint = verify_plan_fingerprint(plan)
        if not isinstance(audit_report, dict) or not audit_report.get("passed"):
            raise ProxyHypothesisValidationError(
                "Proxy temporal audit должен быть успешно пройден до ablation gate"
            )
        if audit_report.get("plan_fingerprint_sha256") != plan_fingerprint:
            raise ProxyHypothesisValidationError(
                "Audit report относится к другому preregistered plan"
            )
        audit_fingerprint = _clean(
            audit_report.get("audit_fingerprint_sha256"),
            field_name="audit_fingerprint_sha256",
            limit=64,
            required=True,
        )

        if not isinstance(baseline, dict) or not isinstance(candidate, dict):
            raise ProxyHypothesisValidationError(
                "baseline/candidate results должны быть JSON-объектами"
            )
        dataset_fingerprint = _clean(
            baseline.get("dataset_fingerprint_sha256"),
            field_name="baseline.dataset_fingerprint_sha256",
            limit=64,
            required=True,
        )
        if candidate.get("dataset_fingerprint_sha256") != dataset_fingerprint:
            raise ProxyHypothesisValidationError(
                "Baseline и candidate имеют разные dataset fingerprints"
            )
        if candidate.get("plan_fingerprint_sha256") != plan_fingerprint:
            raise ProxyHypothesisValidationError(
                "Candidate result не привязан к preregistered plan fingerprint"
            )
        if candidate.get("proxy_audit_fingerprint_sha256") != audit_fingerprint:
            raise ProxyHypothesisValidationError(
                "Candidate result не привязан к текущему temporal audit fingerprint"
            )

        mismatches = {
            key: [baseline.get(key), candidate.get(key)]
            for key in COMPARABLE_RESULT_KEYS
            if baseline.get(key) != candidate.get(key)
        }
        if mismatches:
            raise ProxyHypothesisValidationError(
                "Baseline и candidate получили разные temporal datasets: "
                + _canonical_json(mismatches)
            )

        ablation = plan.get("ablation") or {}
        if baseline.get("schema_label") != ablation.get("baseline_schema"):
            raise ProxyHypothesisValidationError(
                "Baseline schema_label не совпадает с preregistered baseline_schema"
            )
        if candidate.get("label") != ablation.get("candidate_label"):
            raise ProxyHypothesisValidationError(
                "Candidate label не совпадает с preregistered candidate_label"
            )

        specs = _feature_specs(plan)
        expected_added = set(specs)
        for spec in specs.values():
            coverage_name = spec.get("coverage_feature")
            if coverage_name:
                expected_added.add(str(coverage_name))
        baseline_features = set(baseline.get("feature_names") or [])
        candidate_features = set(candidate.get("feature_names") or [])
        if not baseline_features:
            raise ProxyHypothesisValidationError(
                "Baseline result должен содержать feature_names"
            )
        if not baseline_features.issubset(candidate_features):
            raise ProxyHypothesisValidationError(
                "Candidate удалил baseline features; такой experiment не является чистым proxy ablation"
            )
        actually_added = candidate_features - baseline_features
        if actually_added != expected_added:
            raise ProxyHypothesisValidationError(
                "Набор добавленных candidate features не совпадает с preregistered proxy set: "
                + _canonical_json(
                    {
                        "expected": sorted(expected_added),
                        "actual": sorted(actually_added),
                    }
                )
            )

        try:
            baseline_mae = float(baseline["test_mae"])
            candidate_mae = float(candidate["test_mae"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ProxyHypothesisValidationError(
                "baseline/candidate должны содержать числовой test_mae"
            ) from exc
        delta_mae = candidate_mae - baseline_mae
        max_regression = float(ablation.get("max_mae_regression", 0.0))
        require_improvement = ablation.get("require_improvement", True)
        if not isinstance(require_improvement, bool):
            raise ProxyHypothesisValidationError(
                "ablation.require_improvement должен быть boolean"
            )
        if require_improvement:
            passed = delta_mae < 0.0
            reason = "mae_improved" if passed else "mae_not_improved"
        else:
            passed = delta_mae <= max_regression
            reason = "non_regression_passed" if passed else "mae_regression"

        report_without_fingerprint = {
            "version": PROXY_ABLATION_GATE_VERSION,
            "hypothesis_id": plan.get("hypothesis_id"),
            "plan_fingerprint_sha256": plan_fingerprint,
            "proxy_audit_fingerprint_sha256": audit_fingerprint,
            "dataset_fingerprint_sha256": dataset_fingerprint,
            "holdout": {
                key: baseline.get(key) for key in COMPARABLE_RESULT_KEYS
            },
            "baseline": {
                "schema_label": baseline.get("schema_label"),
                "test_mae": baseline_mae,
                "test_rmse": baseline.get("test_rmse"),
                "test_r2": baseline.get("test_r2"),
                "feature_count": len(baseline_features),
            },
            "candidate": {
                "label": candidate.get("label"),
                "test_mae": candidate_mae,
                "test_rmse": candidate.get("test_rmse"),
                "test_r2": candidate.get("test_r2"),
                "feature_count": len(candidate_features),
                "added_features": sorted(actually_added),
            },
            "comparison": {
                "delta_mae": delta_mae,
                "candidate_improves_mae": delta_mae < 0.0,
                "require_improvement": require_improvement,
                "max_mae_regression": max_regression,
                "passed": passed,
                "reason": reason,
            },
            "published": False,
            "research_only": True,
        }
        return {
            **report_without_fingerprint,
            "gate_fingerprint_sha256": _fingerprint(report_without_fingerprint),
        }
