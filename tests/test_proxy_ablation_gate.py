from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from src.proxy_ablation_gate import ProxyAblationResultGate
from src.proxy_hypotheses import ProxyHypothesisStore, ProxyHypothesisValidationError


class ProxyAblationResultGateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = ProxyHypothesisStore(Path(self.tmp.name) / "proxy.duckdb")
        self.hypothesis_id = "proxy-source-pressure"
        self.store.upsert_hypothesis(
            {
                "hypothesis_id": self.hypothesis_id,
                "title": "Source pressure как pre-release proxy",
                "dimension": "plot",
                "change_type": "compressed",
                "rationale": "Проверить заранее известный структурный proxy.",
            }
        )
        self.store.add_evidence(
            {
                "hypothesis_id": self.hypothesis_id,
                "evidence_id": "evidence-1",
                "evidence_kind": "story_transform",
                "reference_id": "transform:case-1:compressed",
                "confidence": 0.9,
            }
        )
        self.store.add_proxy_feature(
            {
                "hypothesis_id": self.hypothesis_id,
                "proxy_id": "feature-1",
                "feature_name": "source_linked_work_count",
                "source_layer": "source_context",
                "temporal_contract": "known_at_lte_cutoff",
                "proxy_role": "primary",
                "available_before_release": True,
            }
        )
        self.store.add_proxy_feature(
            {
                "hypothesis_id": self.hypothesis_id,
                "proxy_id": "feature-2",
                "feature_name": "source_publication_date_known_ratio",
                "source_layer": "source_context",
                "temporal_contract": "known_at_lte_cutoff",
                "proxy_role": "coverage",
                "available_before_release": True,
            }
        )
        self.store.set_ablation_spec(
            {
                "hypothesis_id": self.hypothesis_id,
                "baseline_schema": "v15",
                "candidate_label": "v15+source-pressure",
                "holdout_policy": "stable_temporal_last_two_years",
                "primary_metric": "mae",
                "max_mae_regression": 0.01,
                "require_improvement": True,
            }
        )
        self.store.mark_ablation_ready(self.hypothesis_id)
        self.gate = ProxyAblationResultGate(self.store)
        self.plan = self.store.export_ablation_plan(self.hypothesis_id)

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def payload(self, *, baseline_mae: float = 1.0, candidate_mae: float = 0.97):
        dataset = "a" * 64
        return {
            "version": 1,
            "result_id": "result-1",
            "hypothesis_id": self.hypothesis_id,
            "plan_fingerprint_sha256": self.plan["plan_fingerprint_sha256"],
            "dataset_fingerprint_sha256": dataset,
            "holdout": {
                "policy": "stable_temporal_last_two_years",
                "start_year": 2024,
                "end_year": 2025,
                "row_count": 321,
                "baseline_dataset_fingerprint_sha256": dataset,
                "candidate_dataset_fingerprint_sha256": dataset,
            },
            "candidate_features": [
                "source_publication_date_known_ratio",
                "source_linked_work_count",
            ],
            "baseline_metrics": {"mae": baseline_mae, "rmse": 1.3, "r2": 0.28},
            "candidate_metrics": {"mae": candidate_mae, "rmse": 1.27, "r2": 0.30},
            "runner_id": "proxy-temporal-ablation",
            "runner_version": "1",
            "executed_at": "2026-10-03T20:30:00+00:00",
        }

    def test_accepts_exact_preregistered_improvement_without_publication(self):
        report = self.gate.evaluate(self.payload())

        self.assertTrue(report["passed"])
        self.assertEqual(report["verdict"], "accepted_ablation")
        self.assertAlmostEqual(report["mae_improvement"], 0.03)
        self.assertFalse(report["automatic_catboost_inclusion"])
        self.assertFalse(report["production_publication_allowed"])
        self.assertTrue(report["research_only"])
        self.assertEqual(len(report["result_fingerprint_sha256"]), 64)

    def test_rejects_plan_dataset_holdout_and_feature_drift(self):
        payload = self.payload()
        payload["plan_fingerprint_sha256"] = "b" * 64
        with self.assertRaises(ProxyHypothesisValidationError):
            self.gate.evaluate(payload)

        payload = self.payload()
        payload["holdout"]["candidate_dataset_fingerprint_sha256"] = "c" * 64
        with self.assertRaises(ProxyHypothesisValidationError):
            self.gate.evaluate(payload)

        payload = self.payload()
        payload["holdout"]["policy"] = "random_split"
        with self.assertRaises(ProxyHypothesisValidationError):
            self.gate.evaluate(payload)

        payload = self.payload()
        payload["candidate_features"] = ["source_linked_work_count"]
        with self.assertRaises(ProxyHypothesisValidationError):
            self.gate.evaluate(payload)

    def test_require_improvement_rejects_equal_or_worse_candidate(self):
        equal = self.gate.evaluate(self.payload(candidate_mae=1.0))
        self.assertFalse(equal["passed"])
        self.assertEqual(equal["verdict"], "rejected_no_improvement")

        worse = self.gate.evaluate(self.payload(candidate_mae=1.02))
        self.assertFalse(worse["passed"])
        self.assertEqual(worse["verdict"], "rejected_mae_regression")

    def test_recorded_success_marks_hypothesis_accepted_and_is_idempotent(self):
        report = self.gate.evaluate(self.payload(), record=True)
        self.assertTrue(report["passed"])
        status = self.store.conn.execute(
            "SELECT status FROM proxy_hypotheses WHERE hypothesis_id = ?",
            [self.hypothesis_id],
        ).fetchone()[0]
        self.assertEqual(status, "accepted")
        history = self.gate.result_history(self.hypothesis_id)
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["result_id"], "result-1")
        self.assertTrue(history[0]["passed"])

        # Повторная запись идентичного результата не создаёт дубль.
        self.gate._record(report)
        self.assertEqual(len(self.gate.result_history(self.hypothesis_id)), 1)

    def test_recorded_failure_marks_hypothesis_rejected(self):
        payload = self.payload(candidate_mae=1.0)
        report = self.gate.evaluate(payload, record=True)
        self.assertFalse(report["passed"])
        status, reason = self.store.conn.execute(
            "SELECT status, rejection_reason FROM proxy_hypotheses WHERE hypothesis_id = ?",
            [self.hypothesis_id],
        ).fetchone()
        self.assertEqual(status, "rejected")
        self.assertIn("rejected_no_improvement", reason)

    def test_duplicate_result_id_with_different_fingerprint_is_rejected(self):
        report = self.gate.evaluate(self.payload(), record=True)
        changed = dict(report)
        changed["result_fingerprint_sha256"] = "f" * 64
        with self.assertRaises(ProxyHypothesisValidationError):
            self.gate._record(changed)

    def test_unknown_fields_and_nonfinite_metrics_are_rejected(self):
        payload = self.payload()
        payload["expert_interpretation"] = "leak"
        with self.assertRaises(ProxyHypothesisValidationError):
            self.gate.evaluate(payload)

        payload = self.payload()
        payload["candidate_metrics"]["mae"] = float("nan")
        with self.assertRaises(ProxyHypothesisValidationError):
            self.gate.evaluate(payload)


if __name__ == "__main__":
    unittest.main()
