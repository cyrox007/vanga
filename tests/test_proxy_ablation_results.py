from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from src.proxy_ablation import GenericProxyAblationGate, TemporalProxyAvailabilityAuditor
from src.proxy_ablation_results import ProxyAblationResultStore
from src.proxy_hypotheses import ProxyHypothesisStore, ProxyHypothesisValidationError


def _fingerprint(payload):
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class ProxyAblationResultTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "proxy.duckdb"
        self.registry = ProxyHypothesisStore(self.db)

    def tearDown(self):
        self.registry.close()
        self.tmp.cleanup()

    def _ready_plan(self, hypothesis_id="demo"):
        self.registry.upsert_hypothesis(
            {
                "hypothesis_id": hypothesis_id,
                "title": "Demo proxy",
                "dimension": "worldbuilding",
                "change_type": "compressed",
                "rationale": "Проверяем заранее доступный proxy.",
            }
        )
        self.registry.add_evidence(
            {
                "hypothesis_id": hypothesis_id,
                "evidence_kind": "story_transform",
                "reference_id": f"story-transform:{hypothesis_id}",
                "confidence": 0.9,
            }
        )
        self.registry.add_proxy_feature(
            {
                "hypothesis_id": hypothesis_id,
                "feature_name": "planned_runtime_minutes",
                "source_layer": "source_context",
                "temporal_contract": "planned_before_release",
                "proxy_role": "primary",
            }
        )
        self.registry.set_ablation_spec(
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
        self.registry.mark_ablation_ready(hypothesis_id)
        return self.registry.export_ablation_plan(hypothesis_id)

    def _audit(self, plan):
        materialization = {
            "version": 1,
            "targets": [
                {
                    "target_id": "tt001",
                    "target_year": 2025,
                    "cutoff_at": "2025-03-01T00:00:00+00:00",
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
                    "value": 125,
                    "provenance_id": "source-context:tt001:runtime",
                    "source_timestamp": "2025-02-01T00:00:00+00:00",
                }
            ],
        }
        audit = TemporalProxyAvailabilityAuditor.audit(plan, materialization)
        self.assertTrue(audit["passed"])
        return audit

    def _gate(self, plan, *, candidate_mae=0.95):
        audit = self._audit(plan)
        dataset_fp = "a" * 64
        common = {
            "dataset_fingerprint_sha256": dataset_fp,
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
            "feature_names": [
                "startYear",
                "genres_combined",
                "planned_runtime_minutes",
            ],
            "test_mae": candidate_mae,
            "test_rmse": 1.2,
            "test_r2": 0.25,
        }
        gate = GenericProxyAblationGate.compare(plan, audit, baseline, candidate)
        return audit, gate

    def test_passed_gate_is_persisted_and_accepts_hypothesis(self):
        plan = self._ready_plan()
        _audit, gate = self._gate(plan, candidate_mae=0.95)
        with ProxyAblationResultStore(self.db) as results:
            saved = results.record(plan, gate)
            latest = results.latest("demo")
        status = self.registry.conn.execute(
            "SELECT status, rejection_reason FROM proxy_hypotheses WHERE hypothesis_id='demo'"
        ).fetchone()
        self.assertEqual(status[0], "accepted")
        self.assertIsNone(status[1])
        self.assertEqual(saved["decision"], "accepted")
        self.assertTrue(saved["passed"])
        self.assertFalse(saved["automatic_catboost_publication"])
        self.assertEqual(latest["gate_fingerprint_sha256"], gate["gate_fingerprint_sha256"])
        self.assertEqual(latest["decision"], "accepted")

    def test_failed_gate_is_persisted_and_rejects_hypothesis(self):
        plan = self._ready_plan("bad")
        _audit, gate = self._gate(plan, candidate_mae=1.02)
        with ProxyAblationResultStore(self.db) as results:
            saved = results.record(plan, gate)
        status = self.registry.conn.execute(
            "SELECT status, rejection_reason FROM proxy_hypotheses WHERE hypothesis_id='bad'"
        ).fetchone()
        self.assertEqual(status[0], "rejected")
        self.assertIn("mae_not_improved", status[1])
        self.assertEqual(saved["decision"], "rejected")
        self.assertFalse(saved["passed"])

    def test_same_gate_is_idempotent_after_status_transition(self):
        plan = self._ready_plan()
        _audit, gate = self._gate(plan)
        with ProxyAblationResultStore(self.db) as results:
            first = results.record(plan, gate)
            second = results.record(plan, gate)
            count = results.conn.execute(
                "SELECT COUNT(*) FROM proxy_ablation_results WHERE hypothesis_id='demo'"
            ).fetchone()[0]
        self.assertFalse(first["idempotent"])
        self.assertTrue(second["idempotent"])
        self.assertEqual(first["result_id"], second["result_id"])
        self.assertEqual(count, 1)

    def test_new_result_requires_ablation_ready(self):
        plan = self._ready_plan()
        _audit, gate = self._gate(plan)
        self.registry.conn.execute(
            "UPDATE proxy_hypotheses SET status='proposed' WHERE hypothesis_id='demo'"
        )
        with ProxyAblationResultStore(self.db) as results:
            with self.assertRaises(ProxyHypothesisValidationError):
                results.record(plan, gate)

    def test_gate_fingerprint_tampering_is_rejected(self):
        plan = self._ready_plan()
        _audit, gate = self._gate(plan)
        gate["comparison"]["passed"] = False
        with ProxyAblationResultStore(self.db) as results:
            with self.assertRaises(ProxyHypothesisValidationError):
                results.record(plan, gate)

    def test_gate_must_be_research_only_and_unpublished(self):
        plan = self._ready_plan()
        _audit, gate = self._gate(plan)
        gate["published"] = True
        body = dict(gate)
        body.pop("gate_fingerprint_sha256")
        gate["gate_fingerprint_sha256"] = _fingerprint(body)
        with ProxyAblationResultStore(self.db) as results:
            with self.assertRaises(ProxyHypothesisValidationError):
                results.record(plan, gate)

    def test_stale_preregistered_plan_is_rejected(self):
        plan = self._ready_plan()
        _audit, gate = self._gate(plan)
        self.registry.set_ablation_spec(
            {
                "hypothesis_id": "demo",
                "baseline_schema": "v15",
                "candidate_label": "candidate-changed",
                "holdout_policy": "stable_temporal_last_two_years",
                "primary_metric": "mae",
                "max_mae_regression": 0.0,
                "require_improvement": True,
            }
        )
        with ProxyAblationResultStore(self.db) as results:
            with self.assertRaises(ProxyHypothesisValidationError):
                results.record(plan, gate)


if __name__ == "__main__":
    unittest.main()
