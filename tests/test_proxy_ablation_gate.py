from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from src.proxy_ablation import GenericProxyAblationGate, TemporalProxyAvailabilityAuditor
from src.proxy_ablation_gate import ProxyAblationResultRegistry, verify_generic_gate_report
from src.proxy_hypotheses import ProxyHypothesisStore, ProxyHypothesisValidationError


class ProxyAblationResultRegistryTests(unittest.TestCase):
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
                "rationale": "Проверить заранее известный proxy.",
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
        self.plan = self.store.export_ablation_plan(self.hypothesis_id)
        self.audit = TemporalProxyAvailabilityAuditor.audit(
            self.plan,
            {
                "version": 1,
                "targets": [
                    {
                        "target_id": "tt1234567",
                        "target_year": 2025,
                        "cutoff_at": "2025-02-01T00:00:00+00:00",
                        "release_at": "2025-06-01T00:00:00+00:00",
                    }
                ],
                "observations": [
                    {
                        "target_id": "tt1234567",
                        "feature_name": "source_linked_work_count",
                        "source_layer": "source_context",
                        "temporal_contract": "known_at_lte_cutoff",
                        "available": True,
                        "value": 2,
                        "provenance_id": "source-context:snapshot-1",
                        "source_timestamp": "2025-01-10T00:00:00+00:00",
                    }
                ],
            },
        )
        self.registry = ProxyAblationResultRegistry(self.store)

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def gate_report(self, *, candidate_mae: float = 0.97):
        dataset = "a" * 64
        common = {
            "dataset_fingerprint_sha256": dataset,
            "train_year_from": 1900,
            "train_year_to": 2023,
            "test_year_from": 2024,
            "test_year_to": 2025,
            "train_rows": 1000,
            "test_rows": 300,
            "total_rows": 1300,
        }
        baseline = {
            **common,
            "schema_label": "v15",
            "feature_names": ["base_a"],
            "test_mae": 1.0,
            "test_rmse": 1.3,
            "test_r2": 0.28,
        }
        candidate = {
            **common,
            "label": "v15+source-pressure",
            "feature_names": ["base_a", "source_linked_work_count"],
            "plan_fingerprint_sha256": self.plan["plan_fingerprint_sha256"],
            "proxy_audit_fingerprint_sha256": self.audit["audit_fingerprint_sha256"],
            "test_mae": candidate_mae,
            "test_rmse": 1.27,
            "test_r2": 0.30,
        }
        return GenericProxyAblationGate.compare(
            self.plan, self.audit, baseline, candidate
        )

    def envelope(self, gate_report=None):
        return {
            "version": 1,
            "result_id": "result-1",
            "hypothesis_id": self.hypothesis_id,
            "plan_fingerprint_sha256": self.plan["plan_fingerprint_sha256"],
            "gate_report": gate_report or self.gate_report(),
            "runner_id": "temporal-proxy-ablation",
            "runner_version": "1",
            "executed_at": "2026-10-03T20:30:00+00:00",
        }

    def test_records_only_verified_generic_gate_report(self):
        prepared = self.registry.prepare(self.envelope())
        self.assertTrue(prepared["passed"])
        self.assertEqual(prepared["reason"], "mae_improved")
        self.assertFalse(prepared["status_transition_applied"])
        self.assertEqual(prepared["hypothesis_status"], "ablation_ready")
        self.assertFalse(prepared["automatic_catboost_inclusion"])
        self.assertFalse(prepared["production_publication_allowed"])

        stored = self.registry.record(self.envelope())
        self.assertEqual(stored["result_id"], "result-1")
        self.assertEqual(len(self.registry.history(self.hypothesis_id)), 1)

        status = self.store.conn.execute(
            "SELECT status FROM proxy_hypotheses WHERE hypothesis_id = ?",
            [self.hypothesis_id],
        ).fetchone()[0]
        self.assertEqual(status, "ablation_ready")

    def test_does_not_recompute_or_override_generic_gate_failure(self):
        gate_report = self.gate_report(candidate_mae=1.0)
        self.assertFalse(gate_report["comparison"]["passed"])
        prepared = self.registry.prepare(self.envelope(gate_report))

        self.assertFalse(prepared["passed"])
        self.assertEqual(prepared["reason"], "mae_not_improved")
        self.registry.record(self.envelope(gate_report))
        status = self.store.conn.execute(
            "SELECT status FROM proxy_hypotheses WHERE hypothesis_id = ?",
            [self.hypothesis_id],
        ).fetchone()[0]
        self.assertEqual(status, "ablation_ready")

    def test_tampered_gate_report_is_rejected(self):
        report = self.gate_report()
        report["candidate"]["test_mae"] = 0.1
        with self.assertRaises(ProxyHypothesisValidationError):
            verify_generic_gate_report(report)
        with self.assertRaises(ProxyHypothesisValidationError):
            self.registry.prepare(self.envelope(report))

    def test_wrong_plan_or_hypothesis_is_rejected(self):
        payload = self.envelope()
        payload["plan_fingerprint_sha256"] = "b" * 64
        with self.assertRaises(ProxyHypothesisValidationError):
            self.registry.prepare(payload)

        report = self.gate_report()
        # Изменение hypothesis ломает и gate fingerprint — это тоже обязательная защита.
        report["hypothesis_id"] = "another"
        with self.assertRaises(ProxyHypothesisValidationError):
            self.registry.prepare(self.envelope(report))

    def test_record_is_idempotent_but_same_gate_cannot_get_second_result_id(self):
        first = self.registry.record(self.envelope())
        second = self.registry.record(self.envelope())
        self.assertEqual(first["result_fingerprint_sha256"], second["result_fingerprint_sha256"])
        self.assertEqual(len(self.registry.history(self.hypothesis_id)), 1)

        payload = self.envelope()
        payload["result_id"] = "result-2"
        with self.assertRaises(ProxyHypothesisValidationError):
            self.registry.record(payload)

    def test_unknown_fields_are_rejected(self):
        payload = self.envelope()
        payload["expert_interpretation"] = "leak"
        with self.assertRaises(ProxyHypothesisValidationError):
            self.registry.prepare(payload)


if __name__ == "__main__":
    unittest.main()
