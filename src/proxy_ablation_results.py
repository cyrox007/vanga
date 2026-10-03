from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

import duckdb

from src.proxy_ablation import verify_plan_fingerprint
from src.proxy_hypotheses import ProxyHypothesisStore, ProxyHypothesisValidationError


RESULT_STORE_VERSION = 1


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _canonical_json(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


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


def _sha(value: Any, *, field_name: str) -> str:
    text = _clean(value, field_name=field_name, limit=64, required=True).lower()
    if len(text) != 64 or any(ch not in "0123456789abcdef" for ch in text):
        raise ProxyHypothesisValidationError(f"{field_name} должен быть SHA-256 hex")
    return text


class ProxyAblationResultStore:
    """Хранит только проверенные результаты GenericProxyAblationGate.

    Статус hypothesis меняется на accepted/rejected только после проверки:
    - hypothesis находится в ablation_ready;
    - preregistered plan fingerprint совпадает с gate;
    - gate fingerprint воспроизводим;
    - gate research-only и ничего не публиковал;
    - итоговое решение строго следует comparison.passed.
    """

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = duckdb.connect(str(self.db_path))
        self.conn.execute("SET threads = 1")
        self._ensure_schema()

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "ProxyAblationResultStore":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def _ensure_schema(self) -> None:
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS proxy_ablation_results (
                result_id VARCHAR PRIMARY KEY,
                hypothesis_id VARCHAR NOT NULL,
                plan_fingerprint_sha256 VARCHAR NOT NULL,
                proxy_audit_fingerprint_sha256 VARCHAR NOT NULL,
                gate_fingerprint_sha256 VARCHAR NOT NULL UNIQUE,
                dataset_fingerprint_sha256 VARCHAR NOT NULL,
                holdout_json VARCHAR NOT NULL,
                baseline_json VARCHAR NOT NULL,
                candidate_json VARCHAR NOT NULL,
                comparison_json VARCHAR NOT NULL,
                passed BOOLEAN NOT NULL,
                decision VARCHAR NOT NULL,
                rejection_reason VARCHAR,
                gate_json VARCHAR NOT NULL,
                recorded_at TIMESTAMPTZ NOT NULL
            )
            """
        )
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_proxy_ablation_results_hypothesis "
            "ON proxy_ablation_results(hypothesis_id, recorded_at)"
        )

    def _hypothesis_row(self, hypothesis_id: str) -> tuple[str, str | None]:
        row = self.conn.execute(
            "SELECT status, rejection_reason FROM proxy_hypotheses WHERE hypothesis_id = ?",
            [hypothesis_id],
        ).fetchone()
        if row is None:
            raise ProxyHypothesisValidationError(f"Неизвестная proxy hypothesis: {hypothesis_id}")
        return str(row[0]), row[1]

    @staticmethod
    def _verify_gate_fingerprint(gate: dict[str, Any]) -> str:
        if not isinstance(gate, dict):
            raise ProxyHypothesisValidationError("Gate result должен быть JSON-объектом")
        supplied = _sha(
            gate.get("gate_fingerprint_sha256"),
            field_name="gate_fingerprint_sha256",
        )
        body = dict(gate)
        body.pop("gate_fingerprint_sha256", None)
        actual = _fingerprint(body)
        if supplied != actual:
            raise ProxyHypothesisValidationError("Gate fingerprint mismatch")
        return supplied

    def record(self, plan: dict[str, Any], gate: dict[str, Any]) -> dict[str, Any]:
        plan_fingerprint = verify_plan_fingerprint(plan)
        gate_fingerprint = self._verify_gate_fingerprint(gate)
        existing = self.conn.execute(
            """
            SELECT result_id, hypothesis_id, decision, passed, recorded_at
            FROM proxy_ablation_results
            WHERE gate_fingerprint_sha256 = ?
            """,
            [gate_fingerprint],
        ).fetchone()
        if existing is not None:
            return {
                "version": RESULT_STORE_VERSION,
                "result_id": str(existing[0]),
                "hypothesis_id": str(existing[1]),
                "decision": str(existing[2]),
                "passed": bool(existing[3]),
                "recorded_at": existing[4].isoformat(),
                "idempotent": True,
                "gate_fingerprint_sha256": gate_fingerprint,
            }

        hypothesis_id = _clean(
            plan.get("hypothesis_id"),
            field_name="hypothesis_id",
            limit=180,
            required=True,
        )
        if gate.get("hypothesis_id") != hypothesis_id:
            raise ProxyHypothesisValidationError("Gate относится к другой hypothesis")
        status, _current_reason = self._hypothesis_row(hypothesis_id)
        if status != "ablation_ready":
            raise ProxyHypothesisValidationError(
                f"Записать новый ablation result можно только из status=ablation_ready, сейчас {status}"
            )

        # Важна проверка ДО смены status: export plan содержит preregistered
        # ablation_ready status, после accepted/rejected fingerprint уже изменится.
        registry = ProxyHypothesisStore(self.db_path)
        try:
            current_plan = registry.export_ablation_plan(hypothesis_id)
        finally:
            registry.close()
        if current_plan.get("plan_fingerprint_sha256") != plan_fingerprint:
            raise ProxyHypothesisValidationError(
                "Gate result относится не к текущему preregistered plan"
            )

        if gate.get("plan_fingerprint_sha256") != plan_fingerprint:
            raise ProxyHypothesisValidationError("Gate plan fingerprint mismatch")
        audit_fingerprint = _sha(
            gate.get("proxy_audit_fingerprint_sha256"),
            field_name="proxy_audit_fingerprint_sha256",
        )
        dataset_fingerprint = _sha(
            gate.get("dataset_fingerprint_sha256"),
            field_name="dataset_fingerprint_sha256",
        )
        if gate.get("published") is not False:
            raise ProxyHypothesisValidationError("P6 gate result обязан иметь published=false")
        if gate.get("research_only") is not True:
            raise ProxyHypothesisValidationError("P6 gate result обязан иметь research_only=true")

        comparison = gate.get("comparison")
        if not isinstance(comparison, dict) or not isinstance(comparison.get("passed"), bool):
            raise ProxyHypothesisValidationError("Gate comparison.passed должен быть boolean")
        passed = bool(comparison["passed"])
        decision = "accepted" if passed else "rejected"
        rejection_reason = None
        if not passed:
            reason = _clean(
                comparison.get("reason") or "ablation_rejected",
                field_name="comparison.reason",
                limit=180,
                required=True,
            )
            delta = comparison.get("delta_mae")
            rejection_reason = f"Proxy ablation rejected: {reason}; delta_mae={delta}"

        required_objects = {
            "holdout": gate.get("holdout"),
            "baseline": gate.get("baseline"),
            "candidate": gate.get("candidate"),
        }
        for field_name, value in required_objects.items():
            if not isinstance(value, dict):
                raise ProxyHypothesisValidationError(f"Gate {field_name} должен быть JSON-объектом")

        result_id = f"proxy-result-{uuid4().hex[:20]}"
        now = _now()
        gate_json = _canonical_json(gate)
        self.conn.execute("BEGIN TRANSACTION")
        try:
            self.conn.execute(
                """
                INSERT INTO proxy_ablation_results(
                    result_id, hypothesis_id, plan_fingerprint_sha256,
                    proxy_audit_fingerprint_sha256, gate_fingerprint_sha256,
                    dataset_fingerprint_sha256, holdout_json, baseline_json,
                    candidate_json, comparison_json, passed, decision,
                    rejection_reason, gate_json, recorded_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    result_id,
                    hypothesis_id,
                    plan_fingerprint,
                    audit_fingerprint,
                    gate_fingerprint,
                    dataset_fingerprint,
                    _canonical_json(gate["holdout"]),
                    _canonical_json(gate["baseline"]),
                    _canonical_json(gate["candidate"]),
                    _canonical_json(comparison),
                    passed,
                    decision,
                    rejection_reason,
                    gate_json,
                    now,
                ],
            )
            self.conn.execute(
                """
                UPDATE proxy_hypotheses
                SET status = ?, rejection_reason = ?, updated_at = ?
                WHERE hypothesis_id = ? AND status = 'ablation_ready'
                """,
                [decision, rejection_reason, now, hypothesis_id],
            )
            changed = self.conn.execute("SELECT changes()").fetchone()
            if changed is not None and int(changed[0]) != 1:
                raise ProxyHypothesisValidationError(
                    "Hypothesis status изменился конкурентно; result не сохранён"
                )
            self.conn.execute("COMMIT")
        except Exception:
            self.conn.execute("ROLLBACK")
            raise

        return {
            "version": RESULT_STORE_VERSION,
            "result_id": result_id,
            "hypothesis_id": hypothesis_id,
            "decision": decision,
            "passed": passed,
            "rejection_reason": rejection_reason,
            "plan_fingerprint_sha256": plan_fingerprint,
            "proxy_audit_fingerprint_sha256": audit_fingerprint,
            "gate_fingerprint_sha256": gate_fingerprint,
            "dataset_fingerprint_sha256": dataset_fingerprint,
            "recorded_at": now.isoformat(),
            "idempotent": False,
            "automatic_catboost_publication": False,
        }

    def latest(self, hypothesis_id: str) -> dict[str, Any] | None:
        hypothesis_id = _clean(
            hypothesis_id,
            field_name="hypothesis_id",
            limit=180,
            required=True,
        )
        row = self.conn.execute(
            """
            SELECT result_id, plan_fingerprint_sha256,
                   proxy_audit_fingerprint_sha256, gate_fingerprint_sha256,
                   dataset_fingerprint_sha256, passed, decision,
                   rejection_reason, gate_json, recorded_at
            FROM proxy_ablation_results
            WHERE hypothesis_id = ?
            ORDER BY recorded_at DESC
            LIMIT 1
            """,
            [hypothesis_id],
        ).fetchone()
        if row is None:
            return None
        return {
            "version": RESULT_STORE_VERSION,
            "result_id": str(row[0]),
            "hypothesis_id": hypothesis_id,
            "plan_fingerprint_sha256": str(row[1]),
            "proxy_audit_fingerprint_sha256": str(row[2]),
            "gate_fingerprint_sha256": str(row[3]),
            "dataset_fingerprint_sha256": str(row[4]),
            "passed": bool(row[5]),
            "decision": str(row[6]),
            "rejection_reason": row[7],
            "gate": json.loads(str(row[8])),
            "recorded_at": row[9].isoformat(),
            "automatic_catboost_publication": False,
        }
