from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from src.proxy_ablation import TemporalProxyAvailabilityAuditor
from src.proxy_ablation_pipeline import ProxyAblationPipeline
from src.proxy_hypotheses import ProxyHypothesisStore, ProxyHypothesisValidationError


class ProxyAblationPipelineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "proxy.duckdb"
        self.store = ProxyHypothesisStore(self.db)

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def _plan(self, hypothesis_id="demo"):
        self.store.upsert_hypothesis(
            {
                "hypothesis_id": hypothesis_id,
                "title": "Demo",
                "dimension": "worldbuilding",
                "change_type": "compressed",
                "rationale": "Demo proxy",
            }
        )
        self.store.add_evidence(
            {
                "hypothesis_id": hypothesis_id,
                "evidence_kind": "story_transform",
                "reference_id": f"transform:{hypothesis_id}",
                "confidence": 0.9,
            }
        )
        self.store.add_proxy_feature(
            {
                "hypothesis_id": hypothesis_id,
                "feature_name": "planned_runtime_minutes",
                "source_layer": "source_context",
                "temporal_contract": "planned_before_release",
                "proxy_role": "primary",
            }
        )
        self.store.set_ablation_spec(
            {
                "hypothesis_id": hypothesis_id,
                "baseline_schema": "v15",
                "candidate_label": f"candidate-{hypothesis_id}",
                "holdout_policy": "stable_temporal_last_two_years",
                "primary_metric": "mae",
                "max_mae_regression": 0.0,
                "require_improvement": True,
            }
        )
        self.store.mark_ablation_ready(hypothesis_id)
        return self.store.export_ablation_plan(hypothesis_id)

    def _audit(self, plan):
        return TemporalProxyAvailabilityAuditor.audit(
            plan,
            {
                "version": 1,
                "targets": [
                    {
                        "target_id": "tt001",
                        "target_year": 2025,
                        "cutoff_at": "2025-02-01T00:00:00+00:00",
                        "release_at": "2025-06-01T00:00:00+00:00",
                    }
                ],
                "observations": [
                    {
                        "target_id": "tt001",
                        "feature_name": "planned_runtime_minutes",
                        "source_layer": "source_context",
                        "temporal_contract": "planned_before_release",
                        "available": True,
                        "value": 120,
                        "provenance_id": "source:runtime",
                        "source_timestamp": "2025-01-01T00:00:00+00:00",
                    }
                ],
            },
        )

    def _results(self, plan, audit, candidate_mae=0.95):
        common = {
            "dataset_fingerprint_sha256": "a" * 64,
            "train_year_from": 1910,
            "train_year_to": 2023,
            "test_year_from": 2024,
            "test_year_to": 2025,
            "train_rows": 1000,
            "test_rows": 200,
            "total_rows": 1200,
        }
        baseline = {
            **common,
            "schema_label": "v15",
            "feature_names": ["startYear", "genres_combined"],
            "test_mae": 1.0,
            "test_rmse": 1.3,
            "test_r2": 0.2,
        }
        candidate = {
            **common,
            "label": plan["ablation"]["candidate_label"],
            "plan_fingerprint_sha256": plan["plan_fingerprint_sha256"],
            "proxy_audit_fingerprint_sha256": audit["audit_fingerprint_sha256"],
            "feature_names": ["startYear", "genres_combined", "planned_runtime_minutes"],
            "test_mae": candidate_mae,
            "test_rmse": 1.2,
            "test_r2": 0.25,
        }
        return baseline, candidate

    def test_pipeline_accepts_using_generic_gate_and_persists_same_fingerprint(self):
        plan = self._plan()
        audit = self._audit(plan)
        baseline, candidate = self._results(plan, audit, candidate_mae=0.95)
        result = ProxyAblationPipeline(self.store).run_and_record(
            plan=plan,
            audit_report=audit,
            baseline=baseline,
            candidate=candidate,
            result_id="result-accepted",
        )
        row = self.store.conn.execute(
            """
            SELECT result_fingerprint_sha256, passed, verdict
            FROM proxy_ablation_results WHERE result_id='result-accepted'
            """
        ).fetchone()
        status = self.store.conn.execute(
            "SELECT status FROM proxy_hypotheses WHERE hypothesis_id='demo'"
        ).fetchone()[0]
        self.assertEqual(status, "accepted")
        self.assertTrue(result["passed"])
        self.assertEqual(row[0], result["gate_fingerprint_sha256"])
        self.assertTrue(row[1])
        self.assertEqual(row[2], "accepted_ablation")
        self.assertFalse(result["published"])

    def test_pipeline_rejects_when_generic_gate_rejects(self):
        plan = self._plan("bad")
        audit = self._audit(plan)
        baseline, candidate = self._results(plan, audit, candidate_mae=1.01)
        result = ProxyAblationPipeline(self.store).run_and_record(
            plan=plan,
            audit_report=audit,
            baseline=baseline,
            candidate=candidate,
        )
        status, reason = self.store.conn.execute(
            "SELECT status, rejection_reason FROM proxy_hypotheses WHERE hypothesis_id='bad'"
        ).fetchone()
        self.assertEqual(status, "rejected")
        self.assertFalse(result["passed"])
        self.assertIn("mae_not_improved", reason)

    def test_same_generic_gate_is_idempotent_after_final_status(self):
        plan = self._plan()
        audit = self._audit(plan)
        baseline, candidate = self._results(plan, audit)
        pipeline = ProxyAblationPipeline(self.store)
        first = pipeline.run_and_record(
            plan=plan,
            audit_report=audit,
            baseline=baseline,
            candidate=candidate,
            result_id="stable-result",
        )
        second = pipeline.run_and_record(
            plan=plan,
            audit_report=audit,
            baseline=baseline,
            candidate=candidate,
            result_id="ignored-second-id",
        )
        count = self.store.conn.execute(
            "SELECT COUNT(*) FROM proxy_ablation_results WHERE hypothesis_id='demo'"
        ).fetchone()[0]
        self.assertFalse(first["idempotent"])
        self.assertTrue(second["idempotent"])
        self.assertEqual(second["result_id"], "stable-result")
        self.assertEqual(count, 1)

    def test_failed_temporal_audit_never_reaches_persistence(self):
        plan = self._plan()
        audit = self._audit(plan)
        audit["passed"] = False
        baseline, candidate = self._results(plan, audit)
        with self.assertRaises(ProxyHypothesisValidationError):
            ProxyAblationPipeline(self.store).run_and_record(
                plan=plan,
                audit_report=audit,
                baseline=baseline,
                candidate=candidate,
            )
        count = self.store.conn.execute(
            "SELECT COUNT(*) FROM proxy_ablation_results"
        ).fetchone()[0]
        self.assertEqual(count, 0)

    def test_feature_purity_failure_never_changes_status(self):
        plan = self._plan()
        audit = self._audit(plan)
        baseline, candidate = self._results(plan, audit)
        candidate["feature_names"].append("storydiff_removed_count")
        with self.assertRaises(ProxyHypothesisValidationError):
            ProxyAblationPipeline(self.store).run_and_record(
                plan=plan,
                audit_report=audit,
                baseline=baseline,
                candidate=candidate,
            )
        status = self.store.conn.execute(
            "SELECT status FROM proxy_hypotheses WHERE hypothesis_id='demo'"
        ).fetchone()[0]
        self.assertEqual(status, "ablation_ready")


if __name__ == "__main__":
    unittest.main()
