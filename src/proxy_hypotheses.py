from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

import duckdb

from src.expert_corpus import EXPERT_CHANGE_TYPES, EXPERT_DIMENSIONS


REGISTRY_VERSION = 1

HYPOTHESIS_STATUSES = {
    "proposed",
    "data_ready",
    "ablation_ready",
    "accepted",
    "rejected",
}

RETROSPECTIVE_EVIDENCE_KINDS = {
    "storydiff",
    "story_transform",
    "expert_corpus",
    "adaptation_analysis",
    "production_context_outcome",
}

PROXY_SOURCE_LAYERS = {
    "imdb_history",
    "source_context",
    "production_context",
    "pre_release_public_signal",
}

TEMPORAL_CONTRACTS = {
    "history_before_target_year",
    "known_at_lte_cutoff",
    "published_at_lte_cutoff",
    "planned_before_release",
}

PROXY_ROLES = {"primary", "context", "coverage", "missingness"}

ALLOWED_TEMPORAL_CONTRACTS_BY_SOURCE = {
    "imdb_history": {"history_before_target_year"},
    "source_context": {
        "known_at_lte_cutoff",
        "published_at_lte_cutoff",
        "planned_before_release",
    },
    "production_context": {"known_at_lte_cutoff"},
    "pre_release_public_signal": {
        "known_at_lte_cutoff",
        "published_at_lte_cutoff",
    },
}

FORBIDDEN_FEATURE_PREFIXES = (
    "retro_adapt_",
    "storydiff_",
    "story_diff_",
    "story_transform_",
    "expert_",
    "post_release_",
)

FORBIDDEN_EXACT_FEATURE_NAMES = {
    "averagerating",
    "imdb_average_rating",
    "current_imdb_rating",
    "rating_current",
    "actual_rating",
    "future_rating",
    "expert_score",
    "critic_score",
}

FORBIDDEN_PAYLOAD_FIELDS = {
    "full_text",
    "transcript",
    "review_text",
    "expert_interpretation",
}


class ProxyHypothesisValidationError(ValueError):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _clean(
    value: Any,
    *,
    field_name: str,
    limit: int = 1000,
    required: bool = False,
) -> str:
    text = " ".join(str(value or "").strip().split())
    if required and not text:
        raise ProxyHypothesisValidationError(f"{field_name} не должен быть пустым")
    if len(text) > limit:
        raise ProxyHypothesisValidationError(
            f"{field_name} длиннее допустимых {limit} символов"
        )
    return text


def _bounded(value: Any, *, field_name: str, low: float, high: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ProxyHypothesisValidationError(f"{field_name} должен быть числом") from exc
    if not low <= parsed <= high:
        raise ProxyHypothesisValidationError(
            f"{field_name} должен быть в диапазоне {low}..{high}"
        )
    return parsed


def _reject_forbidden_payload(payload: dict[str, Any]) -> None:
    forbidden = sorted(FORBIDDEN_PAYLOAD_FIELDS & set(payload))
    if forbidden:
        raise ProxyHypothesisValidationError(
            "Registry не хранит полный экспертный текст/интерпретацию: "
            + ", ".join(forbidden)
        )


def _canonical_json(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _fingerprint(payload: Any) -> str:
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def validate_proxy_feature_name(feature_name: str) -> str:
    feature_name = _clean(
        feature_name,
        field_name="feature_name",
        limit=180,
        required=True,
    )
    folded = feature_name.casefold()
    if folded in FORBIDDEN_EXACT_FEATURE_NAMES:
        raise ProxyHypothesisValidationError(
            f"Post-release/target feature запрещён как pre-release proxy: {feature_name}"
        )
    if any(folded.startswith(prefix) for prefix in FORBIDDEN_FEATURE_PREFIXES):
        raise ProxyHypothesisValidationError(
            f"Retrospective feature namespace запрещён как pre-release proxy: {feature_name}"
        )
    return feature_name


class ProxyHypothesisStore:
    """P6 registry: retrospective finding -> pre-release proxy hypothesis.

    Registry не добавляет признаки в CatBoost автоматически. Он хранит только
    preregistered контракт: откуда взялась гипотеза, какие pre-release features
    предлагаются и какой temporal contract обязан выполнять каждый feature до
    отдельного temporal ablation.
    """

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = duckdb.connect(str(self.db_path))
        self.conn.execute("SET threads = 1")
        self._init_schema()

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "ProxyHypothesisStore":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def _init_schema(self) -> None:
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS proxy_hypotheses (
                hypothesis_id VARCHAR PRIMARY KEY,
                title VARCHAR NOT NULL,
                dimension VARCHAR NOT NULL,
                change_type VARCHAR NOT NULL,
                rationale VARCHAR NOT NULL,
                status VARCHAR NOT NULL,
                rejection_reason VARCHAR,
                created_at TIMESTAMPTZ NOT NULL,
                updated_at TIMESTAMPTZ NOT NULL
            )
            """
        )
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS proxy_hypothesis_evidence (
                evidence_id VARCHAR PRIMARY KEY,
                hypothesis_id VARCHAR NOT NULL,
                evidence_kind VARCHAR NOT NULL,
                reference_id VARCHAR NOT NULL,
                observation_summary VARCHAR,
                confidence DOUBLE NOT NULL,
                created_at TIMESTAMPTZ NOT NULL
            )
            """
        )
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS proxy_feature_specs (
                proxy_id VARCHAR PRIMARY KEY,
                hypothesis_id VARCHAR NOT NULL,
                feature_name VARCHAR NOT NULL,
                source_layer VARCHAR NOT NULL,
                temporal_contract VARCHAR NOT NULL,
                proxy_role VARCHAR NOT NULL,
                available_before_release BOOLEAN NOT NULL,
                coverage_feature VARCHAR,
                notes VARCHAR,
                created_at TIMESTAMPTZ NOT NULL,
                UNIQUE(hypothesis_id, feature_name)
            )
            """
        )
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS proxy_ablation_specs (
                hypothesis_id VARCHAR PRIMARY KEY,
                baseline_schema VARCHAR NOT NULL,
                candidate_label VARCHAR NOT NULL,
                holdout_policy VARCHAR NOT NULL,
                primary_metric VARCHAR NOT NULL,
                max_mae_regression DOUBLE NOT NULL,
                require_improvement BOOLEAN NOT NULL,
                dataset_fingerprint_required BOOLEAN NOT NULL,
                preregistered_at TIMESTAMPTZ NOT NULL
            )
            """
        )

    def _require_hypothesis(self, hypothesis_id: str) -> str:
        hypothesis_id = _clean(
            hypothesis_id,
            field_name="hypothesis_id",
            limit=180,
            required=True,
        )
        row = self.conn.execute(
            "SELECT 1 FROM proxy_hypotheses WHERE hypothesis_id = ? LIMIT 1",
            [hypothesis_id],
        ).fetchone()
        if row is None:
            raise ProxyHypothesisValidationError(
                f"Неизвестная proxy hypothesis: {hypothesis_id}"
            )
        return hypothesis_id

    def upsert_hypothesis(self, payload: dict[str, Any]) -> str:
        if not isinstance(payload, dict):
            raise ProxyHypothesisValidationError("hypothesis должен быть JSON-объектом")
        _reject_forbidden_payload(payload)

        hypothesis_id = _clean(
            payload.get("hypothesis_id") or f"proxy-{uuid4().hex[:16]}",
            field_name="hypothesis_id",
            limit=180,
            required=True,
        )
        dimension = str(payload.get("dimension") or "").strip()
        change_type = str(payload.get("change_type") or "").strip()
        status = str(payload.get("status") or "proposed").strip()
        if dimension not in EXPERT_DIMENSIONS:
            raise ProxyHypothesisValidationError(f"Неизвестный dimension: {dimension!r}")
        if change_type not in EXPERT_CHANGE_TYPES:
            raise ProxyHypothesisValidationError(
                f"Неизвестный change_type: {change_type!r}"
            )
        if status not in HYPOTHESIS_STATUSES:
            raise ProxyHypothesisValidationError(f"Неизвестный status: {status!r}")
        rejection_reason = _clean(
            payload.get("rejection_reason"),
            field_name="rejection_reason",
            limit=1500,
        ) or None
        if status == "rejected" and not rejection_reason:
            raise ProxyHypothesisValidationError(
                "Для rejected hypothesis обязателен rejection_reason"
            )
        if status != "rejected" and rejection_reason:
            raise ProxyHypothesisValidationError(
                "rejection_reason допустим только для status=rejected"
            )

        now = _now()
        existing = self.conn.execute(
            "SELECT created_at FROM proxy_hypotheses WHERE hypothesis_id = ?",
            [hypothesis_id],
        ).fetchone()
        created_at = existing[0] if existing else now
        self.conn.execute(
            """
            INSERT OR REPLACE INTO proxy_hypotheses
            (hypothesis_id, title, dimension, change_type, rationale, status,
             rejection_reason, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                hypothesis_id,
                _clean(payload.get("title"), field_name="title", limit=500, required=True),
                dimension,
                change_type,
                _clean(
                    payload.get("rationale"),
                    field_name="rationale",
                    limit=3000,
                    required=True,
                ),
                status,
                rejection_reason,
                created_at,
                now,
            ],
        )
        return hypothesis_id

    def add_evidence(self, payload: dict[str, Any]) -> str:
        if not isinstance(payload, dict):
            raise ProxyHypothesisValidationError("evidence должен быть JSON-объектом")
        _reject_forbidden_payload(payload)
        hypothesis_id = self._require_hypothesis(payload.get("hypothesis_id"))
        evidence_kind = str(payload.get("evidence_kind") or "").strip()
        if evidence_kind not in RETROSPECTIVE_EVIDENCE_KINDS:
            raise ProxyHypothesisValidationError(
                f"Неизвестный evidence_kind: {evidence_kind!r}"
            )
        evidence_id = _clean(
            payload.get("evidence_id") or f"evidence-{uuid4().hex[:16]}",
            field_name="evidence_id",
            limit=180,
            required=True,
        )
        self.conn.execute(
            """
            INSERT OR REPLACE INTO proxy_hypothesis_evidence
            (evidence_id, hypothesis_id, evidence_kind, reference_id,
             observation_summary, confidence, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                evidence_id,
                hypothesis_id,
                evidence_kind,
                _clean(
                    payload.get("reference_id"),
                    field_name="reference_id",
                    limit=500,
                    required=True,
                ),
                _clean(
                    payload.get("observation_summary"),
                    field_name="observation_summary",
                    limit=1500,
                ) or None,
                _bounded(
                    payload.get("confidence", 0.5),
                    field_name="confidence",
                    low=0.0,
                    high=1.0,
                ),
                _now(),
            ],
        )
        return evidence_id

    def add_proxy_feature(self, payload: dict[str, Any]) -> str:
        if not isinstance(payload, dict):
            raise ProxyHypothesisValidationError("proxy feature должен быть JSON-объектом")
        _reject_forbidden_payload(payload)
        hypothesis_id = self._require_hypothesis(payload.get("hypothesis_id"))
        feature_name = validate_proxy_feature_name(payload.get("feature_name"))
        source_layer = str(payload.get("source_layer") or "").strip()
        temporal_contract = str(payload.get("temporal_contract") or "").strip()
        proxy_role = str(payload.get("proxy_role") or "primary").strip()
        if source_layer not in PROXY_SOURCE_LAYERS:
            raise ProxyHypothesisValidationError(
                f"Неизвестный source_layer: {source_layer!r}"
            )
        if temporal_contract not in TEMPORAL_CONTRACTS:
            raise ProxyHypothesisValidationError(
                f"Неизвестный temporal_contract: {temporal_contract!r}"
            )
        if temporal_contract not in ALLOWED_TEMPORAL_CONTRACTS_BY_SOURCE[source_layer]:
            raise ProxyHypothesisValidationError(
                f"temporal_contract={temporal_contract!r} несовместим с source_layer={source_layer!r}"
            )
        if proxy_role not in PROXY_ROLES:
            raise ProxyHypothesisValidationError(
                f"Неизвестный proxy_role: {proxy_role!r}"
            )
        available_before_release = payload.get("available_before_release", True)
        if available_before_release is not True:
            raise ProxyHypothesisValidationError(
                "Proxy feature допускается только если available_before_release=true"
            )
        coverage_feature = _clean(
            payload.get("coverage_feature"),
            field_name="coverage_feature",
            limit=180,
        ) or None
        if coverage_feature:
            coverage_feature = validate_proxy_feature_name(coverage_feature)

        proxy_id = _clean(
            payload.get("proxy_id") or f"feature-{uuid4().hex[:16]}",
            field_name="proxy_id",
            limit=180,
            required=True,
        )
        try:
            self.conn.execute(
                """
                INSERT INTO proxy_feature_specs
                (proxy_id, hypothesis_id, feature_name, source_layer,
                 temporal_contract, proxy_role, available_before_release,
                 coverage_feature, notes, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    proxy_id,
                    hypothesis_id,
                    feature_name,
                    source_layer,
                    temporal_contract,
                    proxy_role,
                    True,
                    coverage_feature,
                    _clean(
                        payload.get("notes"),
                        field_name="notes",
                        limit=1500,
                    ) or None,
                    _now(),
                ],
            )
        except duckdb.ConstraintException as exc:
            raise ProxyHypothesisValidationError(
                f"Proxy feature уже зарегистрирован для hypothesis: {feature_name}"
            ) from exc
        return proxy_id

    def set_ablation_spec(self, payload: dict[str, Any]) -> None:
        if not isinstance(payload, dict):
            raise ProxyHypothesisValidationError("ablation spec должен быть JSON-объектом")
        _reject_forbidden_payload(payload)
        hypothesis_id = self._require_hypothesis(payload.get("hypothesis_id"))
        holdout_policy = _clean(
            payload.get("holdout_policy") or "stable_temporal_last_two_years",
            field_name="holdout_policy",
            limit=180,
            required=True,
        )
        if holdout_policy != "stable_temporal_last_two_years":
            raise ProxyHypothesisValidationError(
                "P6 foundation допускает только holdout_policy=stable_temporal_last_two_years"
            )
        primary_metric = _clean(
            payload.get("primary_metric") or "mae",
            field_name="primary_metric",
            limit=40,
            required=True,
        ).casefold()
        if primary_metric != "mae":
            raise ProxyHypothesisValidationError(
                "P6 foundation использует MAE как primary ablation metric"
            )
        self.conn.execute(
            """
            INSERT OR REPLACE INTO proxy_ablation_specs
            (hypothesis_id, baseline_schema, candidate_label, holdout_policy,
             primary_metric, max_mae_regression, require_improvement,
             dataset_fingerprint_required, preregistered_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                hypothesis_id,
                _clean(
                    payload.get("baseline_schema"),
                    field_name="baseline_schema",
                    limit=100,
                    required=True,
                ),
                _clean(
                    payload.get("candidate_label"),
                    field_name="candidate_label",
                    limit=180,
                    required=True,
                ),
                holdout_policy,
                primary_metric,
                _bounded(
                    payload.get("max_mae_regression", 0.0),
                    field_name="max_mae_regression",
                    low=0.0,
                    high=1.0,
                ),
                bool(payload.get("require_improvement", True)),
                True,
                _now(),
            ],
        )

    def validation_report(self, hypothesis_id: str) -> dict[str, Any]:
        hypothesis_id = self._require_hypothesis(hypothesis_id)
        hypothesis = self.conn.execute(
            """
            SELECT title, dimension, change_type, status
            FROM proxy_hypotheses WHERE hypothesis_id = ?
            """,
            [hypothesis_id],
        ).fetchone()
        evidence_count = int(
            self.conn.execute(
                "SELECT COUNT(*) FROM proxy_hypothesis_evidence WHERE hypothesis_id = ?",
                [hypothesis_id],
            ).fetchone()[0]
        )
        proxy_count = int(
            self.conn.execute(
                "SELECT COUNT(*) FROM proxy_feature_specs WHERE hypothesis_id = ?",
                [hypothesis_id],
            ).fetchone()[0]
        )
        ablation_spec = self.conn.execute(
            """
            SELECT baseline_schema, candidate_label, holdout_policy, primary_metric,
                   max_mae_regression, require_improvement,
                   dataset_fingerprint_required, preregistered_at
            FROM proxy_ablation_specs WHERE hypothesis_id = ?
            """,
            [hypothesis_id],
        ).fetchone()
        issues: list[str] = []
        if hypothesis[3] == "rejected":
            issues.append("hypothesis_rejected")
        if evidence_count < 1:
            issues.append("retrospective_evidence_missing")
        if proxy_count < 1:
            issues.append("pre_release_proxy_missing")
        if ablation_spec is None:
            issues.append("ablation_spec_missing")

        return {
            "registry_version": REGISTRY_VERSION,
            "hypothesis_id": hypothesis_id,
            "title": hypothesis[0],
            "dimension": hypothesis[1],
            "change_type": hypothesis[2],
            "status": hypothesis[3],
            "retrospective_evidence_count": evidence_count,
            "pre_release_proxy_count": proxy_count,
            "ablation_spec_present": ablation_spec is not None,
            "issues": issues,
            "ready_for_ablation": not issues,
        }

    def mark_ablation_ready(self, hypothesis_id: str) -> dict[str, Any]:
        report = self.validation_report(hypothesis_id)
        if not report["ready_for_ablation"]:
            raise ProxyHypothesisValidationError(
                "Hypothesis не готова к ablation: " + ", ".join(report["issues"])
            )
        self.conn.execute(
            """
            UPDATE proxy_hypotheses
            SET status = 'ablation_ready', updated_at = ?
            WHERE hypothesis_id = ?
            """,
            [_now(), hypothesis_id],
        )
        return self.validation_report(hypothesis_id)

    def export_ablation_plan(self, hypothesis_id: str) -> dict[str, Any]:
        report = self.validation_report(hypothesis_id)
        if not report["ready_for_ablation"]:
            raise ProxyHypothesisValidationError(
                "Нельзя экспортировать ablation plan: " + ", ".join(report["issues"])
            )
        hypothesis = self.conn.execute(
            """
            SELECT title, dimension, change_type, rationale, status
            FROM proxy_hypotheses WHERE hypothesis_id = ?
            """,
            [hypothesis_id],
        ).fetchone()
        evidence_rows = self.conn.execute(
            """
            SELECT evidence_kind, reference_id, confidence
            FROM proxy_hypothesis_evidence
            WHERE hypothesis_id = ?
            ORDER BY evidence_kind, reference_id
            """,
            [hypothesis_id],
        ).fetchall()
        feature_rows = self.conn.execute(
            """
            SELECT feature_name, source_layer, temporal_contract, proxy_role,
                   coverage_feature, notes
            FROM proxy_feature_specs
            WHERE hypothesis_id = ?
            ORDER BY feature_name
            """,
            [hypothesis_id],
        ).fetchall()
        ablation = self.conn.execute(
            """
            SELECT baseline_schema, candidate_label, holdout_policy, primary_metric,
                   max_mae_regression, require_improvement,
                   dataset_fingerprint_required, preregistered_at
            FROM proxy_ablation_specs WHERE hypothesis_id = ?
            """,
            [hypothesis_id],
        ).fetchone()
        payload = {
            "registry_version": REGISTRY_VERSION,
            "hypothesis_id": hypothesis_id,
            "title": hypothesis[0],
            "retrospective_signal": {
                "dimension": hypothesis[1],
                "change_type": hypothesis[2],
                "rationale": hypothesis[3],
                "evidence": [
                    {
                        "evidence_kind": row[0],
                        "reference_id": row[1],
                        "confidence": row[2],
                    }
                    for row in evidence_rows
                ],
            },
            "candidate_pre_release_features": [
                {
                    "feature_name": row[0],
                    "source_layer": row[1],
                    "temporal_contract": row[2],
                    "proxy_role": row[3],
                    "coverage_feature": row[4],
                    "notes": row[5],
                }
                for row in feature_rows
            ],
            "ablation": {
                "baseline_schema": ablation[0],
                "candidate_label": ablation[1],
                "holdout_policy": ablation[2],
                "primary_metric": ablation[3],
                "max_mae_regression": ablation[4],
                "require_improvement": ablation[5],
                "dataset_fingerprint_required": ablation[6],
                "preregistered_at": ablation[7].isoformat(),
            },
            "status": hypothesis[4],
            "post_release_features_allowed": False,
            "expert_interpretation_as_feature": False,
            "automatic_catboost_inclusion": False,
        }
        payload["plan_fingerprint_sha256"] = _fingerprint(payload)
        return payload
