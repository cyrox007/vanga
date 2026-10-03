from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any


AUDIT_VERSION = 1
SUPPORTED_CONTRACTS = {
    "history_before_target_year",
    "known_at_lte_cutoff",
    "published_at_lte_cutoff",
    "planned_before_release",
}


class ProxyAblationValidationError(ValueError):
    pass


def _canonical_json(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _fingerprint(payload: Any) -> str:
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def _parse_dt(value: Any, *, field_name: str) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value or "").strip()
        if not text:
            raise ProxyAblationValidationError(f"{field_name} обязателен")
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError as exc:
            raise ProxyAblationValidationError(f"{field_name} должен быть ISO datetime") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _sha256(value: Any, *, field_name: str) -> str:
    text = str(value or "").strip().lower()
    if len(text) != 64 or any(ch not in "0123456789abcdef" for ch in text):
        raise ProxyAblationValidationError(f"{field_name} должен быть SHA-256 hex")
    return text


def _number(value: Any, *, field_name: str) -> float:
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ProxyAblationValidationError(f"{field_name} должен быть числом") from exc


def verify_plan_fingerprint(plan: dict[str, Any]) -> str:
    if not isinstance(plan, dict):
        raise ProxyAblationValidationError("Ablation plan должен быть JSON-объектом")
    expected = _sha256(plan.get("plan_fingerprint_sha256"), field_name="plan_fingerprint_sha256")
    body = dict(plan)
    body.pop("plan_fingerprint_sha256", None)
    actual = _fingerprint(body)
    if actual != expected:
        raise ProxyAblationValidationError("Ablation plan fingerprint mismatch")
    return expected


class TemporalAvailabilityAuditor:
    """Проверяет только доказательство доступности candidate feature на cutoff.

    Auditor намеренно не знает предметный смысл признака и не извлекает данные
    из retrospective layers. Каждое значение приходит вместе с temporal proof.
    Это не позволяет post-release observation незаметно превратиться в feature.
    """

    def audit(self, plan: dict[str, Any], materialized: dict[str, Any]) -> dict[str, Any]:
        plan_fingerprint = verify_plan_fingerprint(plan)
        if not isinstance(materialized, dict):
            raise ProxyAblationValidationError("Materialized dataset должен быть JSON-объектом")
        dataset_fingerprint = _sha256(
            materialized.get("dataset_fingerprint_sha256"),
            field_name="dataset_fingerprint_sha256",
        )
        rows = materialized.get("rows")
        if not isinstance(rows, list) or not rows:
            raise ProxyAblationValidationError("Materialized dataset.rows должен быть непустым массивом")

        feature_specs = plan.get("candidate_pre_release_features") or []
        if not isinstance(feature_specs, list) or not feature_specs:
            raise ProxyAblationValidationError("Ablation plan не содержит candidate features")
        specs: dict[str, dict[str, Any]] = {}
        for spec in feature_specs:
            if not isinstance(spec, dict):
                raise ProxyAblationValidationError("candidate feature spec должен быть JSON-объектом")
            name = str(spec.get("feature_name") or "").strip()
            contract = str(spec.get("temporal_contract") or "").strip()
            if not name:
                raise ProxyAblationValidationError("candidate feature_name обязателен")
            if contract not in SUPPORTED_CONTRACTS:
                raise ProxyAblationValidationError(f"Неподдерживаемый temporal_contract: {contract!r}")
            if name in specs:
                raise ProxyAblationValidationError(f"Дублирующий candidate feature: {name}")
            specs[name] = spec

        issues: list[dict[str, Any]] = []
        feature_stats = {
            name: {"row_count": 0, "value_present_count": 0, "proof_valid_count": 0}
            for name in specs
        }
        target_ids: set[str] = set()

        for row_index, row in enumerate(rows):
            if not isinstance(row, dict):
                raise ProxyAblationValidationError(f"rows[{row_index}] должен быть JSON-объектом")
            target_id = str(row.get("target_id") or "").strip()
            if not target_id:
                raise ProxyAblationValidationError(f"rows[{row_index}].target_id обязателен")
            if target_id in target_ids:
                raise ProxyAblationValidationError(f"Повтор target_id: {target_id}")
            target_ids.add(target_id)
            try:
                target_year = int(row.get("target_year"))
            except (TypeError, ValueError) as exc:
                raise ProxyAblationValidationError(f"{target_id}: target_year должен быть целым") from exc
            cutoff = _parse_dt(row.get("cutoff_at"), field_name=f"{target_id}.cutoff_at")
            release_at = None
            if row.get("release_at") not in (None, ""):
                release_at = _parse_dt(row.get("release_at"), field_name=f"{target_id}.release_at")
                if cutoff > release_at:
                    issues.append({
                        "target_id": target_id,
                        "feature_name": None,
                        "code": "cutoff_after_release",
                    })

            features = row.get("features") or {}
            if not isinstance(features, dict):
                raise ProxyAblationValidationError(f"{target_id}.features должен быть JSON-объектом")
            unexpected = sorted(set(features) - set(specs))
            if unexpected:
                raise ProxyAblationValidationError(
                    f"{target_id}: materialized содержит незарегистрированные features: " + ", ".join(unexpected)
                )

            for name, spec in specs.items():
                feature_stats[name]["row_count"] += 1
                payload = features.get(name)
                if payload is None:
                    issues.append({"target_id": target_id, "feature_name": name, "code": "feature_missing"})
                    continue
                if not isinstance(payload, dict):
                    raise ProxyAblationValidationError(
                        f"{target_id}.{name} должен содержать value + temporal proof"
                    )
                if payload.get("value") is not None:
                    feature_stats[name]["value_present_count"] += 1
                contract = str(spec.get("temporal_contract"))
                valid, code = self._validate_proof(
                    contract=contract,
                    payload=payload,
                    target_year=target_year,
                    cutoff=cutoff,
                    release_at=release_at,
                )
                if valid:
                    feature_stats[name]["proof_valid_count"] += 1
                else:
                    issues.append({"target_id": target_id, "feature_name": name, "code": code})

        feature_coverage: dict[str, Any] = {}
        for name, stat in feature_stats.items():
            total = stat["row_count"]
            feature_coverage[name] = {
                **stat,
                "value_coverage": round(stat["value_present_count"] / total, 6) if total else 0.0,
                "proof_coverage": round(stat["proof_valid_count"] / total, 6) if total else 0.0,
            }

        report_body = {
            "version": AUDIT_VERSION,
            "plan_fingerprint_sha256": plan_fingerprint,
            "dataset_fingerprint_sha256": dataset_fingerprint,
            "row_count": len(rows),
            "feature_count": len(specs),
            "feature_coverage": feature_coverage,
            "issues": issues,
            "passed": not issues,
            "retrospective_values_allowed": False,
        }
        report = dict(report_body)
        report["audit_fingerprint_sha256"] = _fingerprint(report_body)
        return report

    @staticmethod
    def _validate_proof(
        *,
        contract: str,
        payload: dict[str, Any],
        target_year: int,
        cutoff: datetime,
        release_at: datetime | None,
    ) -> tuple[bool, str | None]:
        if payload.get("available_before_release") is not True:
            return False, "not_marked_pre_release"

        if contract == "history_before_target_year":
            raw_year = payload.get("history_max_year")
            if raw_year in (None, ""):
                return False, "history_proof_missing"
            try:
                history_max_year = int(raw_year)
            except (TypeError, ValueError):
                return False, "history_year_invalid"
            return (
                (True, None)
                if history_max_year < target_year
                else (False, "history_not_before_target_year")
            )

        if contract == "known_at_lte_cutoff":
            try:
                known_at = _parse_dt(payload.get("known_at"), field_name="known_at")
            except ProxyAblationValidationError:
                return False, "known_at_missing_or_invalid"
            return (True, None) if known_at <= cutoff else (False, "known_after_cutoff")

        if contract == "published_at_lte_cutoff":
            try:
                published_at = _parse_dt(payload.get("published_at"), field_name="published_at")
            except ProxyAblationValidationError:
                return False, "published_at_missing_or_invalid"
            return (
                (True, None)
                if published_at <= cutoff
                else (False, "published_after_cutoff")
            )

        if contract == "planned_before_release":
            try:
                known_at = _parse_dt(payload.get("known_at"), field_name="known_at")
            except ProxyAblationValidationError:
                return False, "known_at_missing_or_invalid"
            if known_at > cutoff:
                return False, "planned_fact_known_after_cutoff"
            if release_at is not None and known_at > release_at:
                return False, "planned_fact_known_after_release"
            return True, None

        return False, "unsupported_contract"


class ProxyAblationEvaluator:
    """Сравнивает уже посчитанные baseline/candidate на одном честном holdout."""

    def evaluate(
        self,
        plan: dict[str, Any],
        audit_report: dict[str, Any],
        baseline: dict[str, Any],
        candidate: dict[str, Any],
    ) -> dict[str, Any]:
        plan_fingerprint = verify_plan_fingerprint(plan)
        if not audit_report.get("passed"):
            raise ProxyAblationValidationError("Temporal availability audit не пройден")
        if audit_report.get("plan_fingerprint_sha256") != plan_fingerprint:
            raise ProxyAblationValidationError("Audit относится к другому ablation plan")
        dataset_fp = _sha256(
            audit_report.get("dataset_fingerprint_sha256"),
            field_name="audit.dataset_fingerprint_sha256",
        )

        baseline_norm = self._normalize_result(baseline, label="baseline")
        candidate_norm = self._normalize_result(candidate, label="candidate")
        for result in (baseline_norm, candidate_norm):
            if result["dataset_fingerprint_sha256"] != dataset_fp:
                raise ProxyAblationValidationError("Dataset fingerprint не совпадает с temporal audit")

        comparable_keys = ("holdout_start_year", "holdout_end_year", "test_rows")
        for key in comparable_keys:
            if baseline_norm[key] != candidate_norm[key]:
                raise ProxyAblationValidationError(f"Baseline/candidate имеют разный {key}")

        spec = plan.get("ablation") or {}
        if str(spec.get("primary_metric") or "").casefold() != "mae":
            raise ProxyAblationValidationError("Foundation поддерживает только MAE ablation")
        max_regression = _number(spec.get("max_mae_regression", 0.0), field_name="max_mae_regression")
        require_improvement = bool(spec.get("require_improvement", True))
        delta = candidate_norm["mae"] - baseline_norm["mae"]
        passed = delta < 0.0 if require_improvement else delta <= max_regression
        reason = "mae_improved" if delta < 0 else ("within_tolerance" if passed else "mae_regression")

        body = {
            "version": AUDIT_VERSION,
            "hypothesis_id": plan.get("hypothesis_id"),
            "plan_fingerprint_sha256": plan_fingerprint,
            "audit_fingerprint_sha256": audit_report.get("audit_fingerprint_sha256"),
            "dataset_fingerprint_sha256": dataset_fp,
            "holdout_start_year": baseline_norm["holdout_start_year"],
            "holdout_end_year": baseline_norm["holdout_end_year"],
            "test_rows": baseline_norm["test_rows"],
            "baseline_mae": baseline_norm["mae"],
            "candidate_mae": candidate_norm["mae"],
            "mae_delta": round(delta, 10),
            "require_improvement": require_improvement,
            "max_mae_regression": max_regression,
            "passed": passed,
            "reason": reason,
            "automatic_catboost_publication": False,
        }
        report = dict(body)
        report["result_fingerprint_sha256"] = _fingerprint(body)
        return report

    @staticmethod
    def _normalize_result(payload: dict[str, Any], *, label: str) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise ProxyAblationValidationError(f"{label} result должен быть JSON-объектом")
        try:
            start_year = int(payload.get("holdout_start_year"))
            end_year = int(payload.get("holdout_end_year"))
            test_rows = int(payload.get("test_rows"))
        except (TypeError, ValueError) as exc:
            raise ProxyAblationValidationError(f"{label}: holdout years/test_rows должны быть целыми") from exc
        if test_rows <= 0 or end_year < start_year:
            raise ProxyAblationValidationError(f"{label}: некорректный holdout")
        return {
            "dataset_fingerprint_sha256": _sha256(
                payload.get("dataset_fingerprint_sha256"),
                field_name=f"{label}.dataset_fingerprint_sha256",
            ),
            "holdout_start_year": start_year,
            "holdout_end_year": end_year,
            "test_rows": test_rows,
            "mae": _number(payload.get("mae"), field_name=f"{label}.mae"),
        }
