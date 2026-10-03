from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from src.proxy_ablation import GenericProxyAblationGate, verify_plan_fingerprint
from src.proxy_candidate_schema import ProxyCandidateSchemaRegistry
from src.proxy_hypotheses import ProxyHypothesisValidationError


CANDIDATE_SCHEMA_GATE_VERSION = 1
FINAL_SCHEMA_STATUSES = {"combined_validated", "combined_rejected"}


def _canonical_json(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _fingerprint(payload: Any) -> str:
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _clean(value: Any, *, field_name: str, limit: int = 300, required: bool = False) -> str:
    text = " ".join(str(value or "").strip().split())
    if required and not text:
        raise ProxyHypothesisValidationError(f"{field_name} не должен быть пустым")
    if len(text) > limit:
        raise ProxyHypothesisValidationError(
            f"{field_name} длиннее допустимых {limit} символов"
        )
    return text


class ProxyCandidateSchemaGate:
    """Combined ablation gate для frozen candidate schema.

    Quality decision по-прежнему принимает только ``GenericProxyAblationGate``.
    Этот класс проверяет связь gate с immutable schema и сохраняет один итоговый
    result на schema version. Исходные accepted hypotheses не изменяются.
    """

    def __init__(self, registry: ProxyCandidateSchemaRegistry) -> None:
        self.registry = registry
        self.conn = registry.conn
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS proxy_candidate_schema_results (
                result_id VARCHAR PRIMARY KEY,
                schema_id VARCHAR NOT NULL UNIQUE,
                schema_fingerprint_sha256 VARCHAR NOT NULL,
                plan_fingerprint_sha256 VARCHAR NOT NULL,
                proxy_audit_fingerprint_sha256 VARCHAR NOT NULL,
                gate_fingerprint_sha256 VARCHAR NOT NULL UNIQUE,
                dataset_fingerprint_sha256 VARCHAR NOT NULL,
                holdout_json VARCHAR NOT NULL,
                baseline_json VARCHAR NOT NULL,
                candidate_json VARCHAR NOT NULL,
                comparison_json VARCHAR NOT NULL,
                passed BOOLEAN NOT NULL,
                verdict VARCHAR NOT NULL,
                gate_json VARCHAR NOT NULL,
                recorded_at TIMESTAMPTZ NOT NULL
            )
            """
        )

    def _lifecycle_status(self, schema_id: str) -> str:
        row = self.conn.execute(
            "SELECT status FROM proxy_candidate_schemas WHERE schema_id = ?",
            [schema_id],
        ).fetchone()
        if row is None:
            raise ProxyHypothesisValidationError(f"Неизвестный candidate schema: {schema_id}")
        return str(row[0])

    @staticmethod
    def _verify_gate_fingerprint(gate: dict[str, Any]) -> str:
        supplied = _clean(
            gate.get("gate_fingerprint_sha256"),
            field_name="gate_fingerprint_sha256",
            limit=64,
            required=True,
        ).lower()
        if len(supplied) != 64 or any(ch not in "0123456789abcdef" for ch in supplied):
            raise ProxyHypothesisValidationError(
                "gate_fingerprint_sha256 должен быть SHA-256 hex"
            )
        body = dict(gate)
        body.pop("gate_fingerprint_sha256", None)
        if _fingerprint(body) != supplied:
            raise ProxyHypothesisValidationError("Generic gate fingerprint mismatch")
        return supplied

    def evaluate_and_record(
        self,
        *,
        schema_id: str,
        plan: dict[str, Any],
        audit_report: dict[str, Any],
        baseline: dict[str, Any],
        candidate: dict[str, Any],
        result_id: str | None = None,
    ) -> dict[str, Any]:
        schema_id = _clean(schema_id, field_name="schema_id", limit=180, required=True)
        schema = self.registry.get(schema_id)
        plan_fingerprint = verify_plan_fingerprint(plan)

        if plan.get("candidate_schema_id") != schema_id:
            raise ProxyHypothesisValidationError(
                "Combined plan относится к другому candidate schema"
            )
        if plan.get("candidate_schema_fingerprint_sha256") != schema[
            "schema_fingerprint_sha256"
        ]:
            raise ProxyHypothesisValidationError(
                "Combined plan schema fingerprint не совпадает с frozen schema"
            )
        if plan.get("expected_dataset_fingerprint_sha256") != schema[
            "dataset_fingerprint_sha256"
        ]:
            raise ProxyHypothesisValidationError(
                "Combined plan ожидает другой dataset fingerprint"
            )

        gate = GenericProxyAblationGate.compare(
            plan,
            audit_report,
            baseline,
            candidate,
        )
        gate_fingerprint = self._verify_gate_fingerprint(gate)
        expected_hypothesis_id = f"candidate-schema:{schema_id}"
        if gate.get("hypothesis_id") != expected_hypothesis_id:
            raise ProxyHypothesisValidationError(
                "Generic gate hypothesis_id не соответствует candidate schema"
            )
        if gate.get("plan_fingerprint_sha256") != plan_fingerprint:
            raise ProxyHypothesisValidationError("Generic gate относится к другому combined plan")
        if gate.get("dataset_fingerprint_sha256") != schema[
            "dataset_fingerprint_sha256"
        ]:
            raise ProxyHypothesisValidationError(
                "Combined ablation выполнен не на dataset, зафиксированном candidate schema"
            )
        if gate.get("published") is not False or gate.get("research_only") is not True:
            raise ProxyHypothesisValidationError(
                "Combined candidate gate обязан оставаться research-only и published=false"
            )

        existing = self.conn.execute(
            """
            SELECT result_id, passed, verdict, recorded_at
            FROM proxy_candidate_schema_results
            WHERE schema_id = ? AND gate_fingerprint_sha256 = ?
            """,
            [schema_id, gate_fingerprint],
        ).fetchone()
        if existing is not None:
            return {
                "version": CANDIDATE_SCHEMA_GATE_VERSION,
                "schema_id": schema_id,
                "result_id": str(existing[0]),
                "passed": bool(existing[1]),
                "verdict": str(existing[2]),
                "gate_fingerprint_sha256": gate_fingerprint,
                "recorded_at": existing[3].isoformat(),
                "idempotent": True,
                "production_publication_allowed": False,
            }

        lifecycle_status = self._lifecycle_status(schema_id)
        if lifecycle_status != "frozen_research_candidate":
            raise ProxyHypothesisValidationError(
                f"Candidate schema {schema_id} уже имеет lifecycle status={lifecycle_status}; "
                "для нового combined experiment нужна новая schema version"
            )
        any_existing = self.conn.execute(
            "SELECT result_id FROM proxy_candidate_schema_results WHERE schema_id = ? LIMIT 1",
            [schema_id],
        ).fetchone()
        if any_existing is not None:
            raise ProxyHypothesisValidationError(
                f"Candidate schema {schema_id} уже имеет result={any_existing[0]}; "
                "история immutable"
            )

        comparison = gate.get("comparison")
        if not isinstance(comparison, dict) or not isinstance(comparison.get("passed"), bool):
            raise ProxyHypothesisValidationError(
                "Generic gate comparison.passed должен быть boolean"
            )
        passed = bool(comparison["passed"])
        verdict = "combined_validated" if passed else "combined_rejected"
        result_id = _clean(
            result_id or f"candidate-schema-result-{uuid4().hex[:18]}",
            field_name="result_id",
            limit=180,
            required=True,
        )
        required_objects = {
            "holdout": gate.get("holdout"),
            "baseline": gate.get("baseline"),
            "candidate": gate.get("candidate"),
        }
        for field_name, value in required_objects.items():
            if not isinstance(value, dict):
                raise ProxyHypothesisValidationError(
                    f"Generic gate {field_name} должен быть JSON-объектом"
                )
        audit_fingerprint = _clean(
            gate.get("proxy_audit_fingerprint_sha256"),
            field_name="proxy_audit_fingerprint_sha256",
            limit=64,
            required=True,
        )
        recorded_at = _now()

        self.conn.execute("BEGIN TRANSACTION")
        try:
            self.conn.execute(
                """
                INSERT INTO proxy_candidate_schema_results(
                    result_id, schema_id, schema_fingerprint_sha256,
                    plan_fingerprint_sha256, proxy_audit_fingerprint_sha256,
                    gate_fingerprint_sha256, dataset_fingerprint_sha256,
                    holdout_json, baseline_json, candidate_json, comparison_json,
                    passed, verdict, gate_json, recorded_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    result_id,
                    schema_id,
                    schema["schema_fingerprint_sha256"],
                    plan_fingerprint,
                    audit_fingerprint,
                    gate_fingerprint,
                    schema["dataset_fingerprint_sha256"],
                    _canonical_json(gate["holdout"]),
                    _canonical_json(gate["baseline"]),
                    _canonical_json(gate["candidate"]),
                    _canonical_json(comparison),
                    passed,
                    verdict,
                    _canonical_json(gate),
                    recorded_at,
                ],
            )
            self.conn.execute(
                """
                UPDATE proxy_candidate_schemas
                SET status = ?
                WHERE schema_id = ? AND status = 'frozen_research_candidate'
                """,
                [verdict, schema_id],
            )
            self.conn.execute("COMMIT")
        except Exception:
            self.conn.execute("ROLLBACK")
            raise

        return {
            "version": CANDIDATE_SCHEMA_GATE_VERSION,
            "schema_id": schema_id,
            "schema_fingerprint_sha256": schema["schema_fingerprint_sha256"],
            "result_id": result_id,
            "plan_fingerprint_sha256": plan_fingerprint,
            "proxy_audit_fingerprint_sha256": audit_fingerprint,
            "gate_fingerprint_sha256": gate_fingerprint,
            "dataset_fingerprint_sha256": schema["dataset_fingerprint_sha256"],
            "passed": passed,
            "verdict": verdict,
            "recorded_at": recorded_at.isoformat(),
            "idempotent": False,
            "research_only": True,
            "automatic_catboost_inclusion": False,
            "production_publication_allowed": False,
        }

    def result(self, schema_id: str) -> dict[str, Any] | None:
        schema_id = _clean(schema_id, field_name="schema_id", limit=180, required=True)
        row = self.conn.execute(
            """
            SELECT result_id, schema_fingerprint_sha256,
                   plan_fingerprint_sha256, proxy_audit_fingerprint_sha256,
                   gate_fingerprint_sha256, dataset_fingerprint_sha256,
                   passed, verdict, gate_json, recorded_at
            FROM proxy_candidate_schema_results
            WHERE schema_id = ?
            """,
            [schema_id],
        ).fetchone()
        if row is None:
            return None
        return {
            "version": CANDIDATE_SCHEMA_GATE_VERSION,
            "schema_id": schema_id,
            "lifecycle_status": self._lifecycle_status(schema_id),
            "result_id": str(row[0]),
            "schema_fingerprint_sha256": str(row[1]),
            "plan_fingerprint_sha256": str(row[2]),
            "proxy_audit_fingerprint_sha256": str(row[3]),
            "gate_fingerprint_sha256": str(row[4]),
            "dataset_fingerprint_sha256": str(row[5]),
            "passed": bool(row[6]),
            "verdict": str(row[7]),
            "gate": json.loads(str(row[8])),
            "recorded_at": row[9].isoformat(),
            "production_publication_allowed": False,
        }
