from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from src.proxy_ablation import GenericProxyAblationGate, verify_plan_fingerprint
from src.proxy_ablation_gate import ProxyAblationResultGate
from src.proxy_hypotheses import ProxyHypothesisStore, ProxyHypothesisValidationError


PIPELINE_VERSION = 1


class ProxyAblationPipeline:
    """Канонический P6 путь: #70 gate -> #71 result history/status.

    Решение о качестве принимает только ``GenericProxyAblationGate``. Этот слой
    не пересчитывает MAE policy вторым независимым алгоритмом, а сохраняет уже
    проверенный gate report и атомарно обновляет status hypothesis.
    """

    def __init__(self, store: ProxyHypothesisStore) -> None:
        self.store = store
        # Переиспользуем уже слитую schema proxy_ablation_results из #71.
        ProxyAblationResultGate(store)

    def run_and_record(
        self,
        *,
        plan: dict[str, Any],
        audit_report: dict[str, Any],
        baseline: dict[str, Any],
        candidate: dict[str, Any],
        result_id: str | None = None,
        runner_id: str = "generic-proxy-ablation-gate",
        runner_version: str = "1",
        executed_at: datetime | None = None,
    ) -> dict[str, Any]:
        plan_fingerprint = verify_plan_fingerprint(plan)
        gate = GenericProxyAblationGate.compare(
            plan,
            audit_report,
            baseline,
            candidate,
        )
        gate_fingerprint = str(gate["gate_fingerprint_sha256"])
        hypothesis_id = str(gate.get("hypothesis_id") or "").strip()
        if not hypothesis_id:
            raise ProxyHypothesisValidationError("Generic gate не содержит hypothesis_id")

        existing = self.store.conn.execute(
            """
            SELECT result_id, passed, verdict, recorded_at
            FROM proxy_ablation_results
            WHERE hypothesis_id = ? AND result_fingerprint_sha256 = ?
            LIMIT 1
            """,
            [hypothesis_id, gate_fingerprint],
        ).fetchone()
        if existing is not None:
            return {
                "version": PIPELINE_VERSION,
                "hypothesis_id": hypothesis_id,
                "result_id": str(existing[0]),
                "gate_fingerprint_sha256": gate_fingerprint,
                "passed": bool(existing[1]),
                "verdict": str(existing[2]),
                "recorded_at": existing[3].isoformat(),
                "idempotent": True,
                "published": False,
                "research_only": True,
            }

        current = self.store.conn.execute(
            "SELECT status FROM proxy_hypotheses WHERE hypothesis_id = ?",
            [hypothesis_id],
        ).fetchone()
        if current is None:
            raise ProxyHypothesisValidationError(f"Неизвестная proxy hypothesis: {hypothesis_id}")
        if str(current[0]) != "ablation_ready":
            raise ProxyHypothesisValidationError(
                f"Новый P6 result можно записать только из ablation_ready, сейчас {current[0]}"
            )

        current_plan = self.store.export_ablation_plan(hypothesis_id)
        if current_plan["plan_fingerprint_sha256"] != plan_fingerprint:
            raise ProxyHypothesisValidationError(
                "Generic gate относится не к текущему preregistered plan"
            )

        holdout = gate.get("holdout") or {}
        comparison = gate.get("comparison") or {}
        baseline_report = gate.get("baseline") or {}
        candidate_report = gate.get("candidate") or {}
        try:
            start_year = int(holdout["test_year_from"])
            end_year = int(holdout["test_year_to"])
            row_count = int(holdout["test_rows"])
            baseline_mae = float(baseline_report["test_mae"])
            candidate_mae = float(candidate_report["test_mae"])
            delta_mae = float(comparison["delta_mae"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ProxyHypothesisValidationError(
                "Generic gate report не содержит нормализованный temporal holdout/MAE"
            ) from exc

        passed = comparison.get("passed")
        if not isinstance(passed, bool):
            raise ProxyHypothesisValidationError("Generic gate comparison.passed должен быть boolean")
        reason = str(comparison.get("reason") or "unknown")
        verdict = "accepted_ablation" if passed else f"rejected_{reason}"
        decision = "accepted" if passed else "rejected"
        rejection_reason = None if passed else f"Ablation gate: {reason}; MAE delta={delta_mae}"

        result_id = str(result_id or f"proxy-gate-{uuid4().hex[:20]}").strip()
        if not result_id:
            raise ProxyHypothesisValidationError("result_id не должен быть пустым")
        runner_id = str(runner_id or "").strip()
        runner_version = str(runner_version or "").strip()
        if not runner_id or not runner_version:
            raise ProxyHypothesisValidationError("runner_id/runner_version обязательны")
        executed_at = (executed_at or datetime.now(timezone.utc)).astimezone(timezone.utc)
        recorded_at = datetime.now(timezone.utc)

        self.store.conn.execute("BEGIN TRANSACTION")
        try:
            self.store.conn.execute(
                """
                INSERT INTO proxy_ablation_results(
                    result_id, hypothesis_id, plan_fingerprint_sha256,
                    result_fingerprint_sha256, dataset_fingerprint_sha256,
                    holdout_policy, holdout_start_year, holdout_end_year,
                    holdout_row_count, baseline_mae, candidate_mae, mae_delta,
                    passed, verdict, runner_id, runner_version, executed_at, recorded_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    result_id,
                    hypothesis_id,
                    plan_fingerprint,
                    gate_fingerprint,
                    gate["dataset_fingerprint_sha256"],
                    current_plan["ablation"]["holdout_policy"],
                    start_year,
                    end_year,
                    row_count,
                    baseline_mae,
                    candidate_mae,
                    delta_mae,
                    passed,
                    verdict,
                    runner_id,
                    runner_version,
                    executed_at,
                    recorded_at,
                ],
            )
            self.store.conn.execute(
                """
                UPDATE proxy_hypotheses
                SET status = ?, rejection_reason = ?, updated_at = ?
                WHERE hypothesis_id = ?
                """,
                [decision, rejection_reason, recorded_at, hypothesis_id],
            )
            self.store.conn.execute("COMMIT")
        except Exception:
            self.store.conn.execute("ROLLBACK")
            raise

        return {
            "version": PIPELINE_VERSION,
            "hypothesis_id": hypothesis_id,
            "result_id": result_id,
            "plan_fingerprint_sha256": plan_fingerprint,
            "proxy_audit_fingerprint_sha256": gate["proxy_audit_fingerprint_sha256"],
            "gate_fingerprint_sha256": gate_fingerprint,
            "dataset_fingerprint_sha256": gate["dataset_fingerprint_sha256"],
            "passed": passed,
            "verdict": verdict,
            "decision": decision,
            "rejection_reason": rejection_reason,
            "recorded_at": recorded_at.isoformat(),
            "idempotent": False,
            "published": False,
            "research_only": True,
            "automatic_catboost_inclusion": False,
        }
