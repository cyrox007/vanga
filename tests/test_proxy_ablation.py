from __future__ import annotations

import copy
import tempfile
import unittest
from pathlib import Path

from src.proxy_ablation import GenericProxyAblationGate, TemporalProxyAvailabilityAuditor
from src.proxy_hypotheses import (
    ProxyHypothesisStore,
    ProxyHypothesisValidationError,
)


class ProxyAblationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = ProxyHypothesisStore(Path(self.tmp.name) / "proxy.duckdb")
        hypothesis_id = self.store.upsert_hypothesis(
            {
                "hypothesis_id": "p6-demo",
                "title": "Pre-release proxy demo",
                "dimension": "worldbuilding",
                "change_type": "compressed",
                "rationale": "Проверяем temporal materialization до CatBoost ablation.",
            }
        )
        self.store.add_evidence(
            {
                "hypothesis_id": hypothesis_id,
                "evidence_kind": "story_transform",
                "reference_id": "story-transform:demo",
                "confidence": 0.9,
            }
        )
        self.store.add_proxy_feature(
            {
                "hypothesis_id": hypothesis_id,
                "feature_name": "source_worldbuilding_entity_count",
                "source_layer": "source_context",
                "temporal_contract": "known_at_lte_cutoff",
                "coverage_feature": "source_complexity_known",
            }
        )
        self.store.add_proxy_feature(
            {
                "hypothesis_id": hypothesis_id,
                "feature_name": "writer_adaptation_count",
                "source_layer": "imdb_history",
                "temporal_contract": "history_before_target_year",
                "coverage_feature": "writer_adaptation_known",
            }
        )
        self.store.set_ablation_spec(
            {
                "hypothesis_id": hypothesis_id,
                "baseline_schema": "v15",
                "candidate_label": "p6-demo-candidate",
                "holdout_policy": "stable_temporal_last_two_years",
                "primary_metric": "mae",
                "max_mae_regression": 0.0,
                "require_improvement": True,
            }
        )
        self.plan = self.store.export_ablation_plan(hypothesis_id)

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def _materialization(self) -> dict:
        return {
            "version": 1,
            "targets": [
                {
                    "target_id": "tt-a",
                    "target_year": 2025,
                    "cutoff_at": "2025-02-01T00:00:00+00:00",
                    "release_at": "2025-06-01T00:00:00+00:00",
                },
                {
                    "target_id": "tt-b",
                    "target_year": 2026,
                    "cutoff_at": "2026-01-15T00:00:00+00:00",
                    "release_at": "2026-05-01T00:00:00+00:00",
                },
            ],
            "observations": [
                {
                    "target_id": "tt-a",
                    "feature_name": "source_worldbuilding_entity_count",
                    "source_layer": "source_context",
                    "temporal_contract": "known_at_lte_cutoff",
                    "available": True,
                    "value": 42,
                    "provenance_id": "source-snapshot:a",
                    "source_timestamp": "2025-01-10T00:00:00+00:00",
                },
                {
                    "target_id": "tt-a",
                    "feature_name": "writer_adaptation_count",
                    "source_layer": "imdb_history",
                    "temporal_contract": "history_before_target_year",
                    "available": True,
                    "value": 3,
                    "provenance_id": "imdb-history:a",
                    "history_year": 2024,
                },
                {
                    "target_id": "tt-b",
                    "feature_name": "source_worldbuilding_entity_count",
                    "source_layer": "source_context",
                    "temporal_contract": "known_at_lte_cutoff",
                    "available": False,
                    "value": None,
                    "missing_reason": "source complexity snapshot ещё не опубликован",
                },
                {
                    "target_id": "tt-b",
                    "feature_name": "writer_adaptation_count",
                    "source_layer": "imdb_history",
                    "temporal_contract": "history_before_target_year",
                    "available": True,
                    "value": 5,
                    "provenance_id": "imdb-history:b",
                    "history_year": 2025,
                },
            ],
        }

    def test_valid_materialization_passes_and_keeps_missingness_explicit(self):
        report = TemporalProxyAvailabilityAuditor.audit(
            self.plan,
            self._materialization(),
        )

        self.assertTrue(report["passed"])
        self.assertEqual(report["violation_count"], 0)
        self.assertEqual(report["coverage"]["source_worldbuilding_entity_count"]["coverage_ratio"], 0.5)
        self.assertEqual(report["coverage"]["writer_adaptation_count"]["coverage_ratio"], 1.0)
        self.assertEqual(len(report["materialization_fingerprint_sha256"]), 64)
        self.assertEqual(len(report["audit_fingerprint_sha256"]), 64)

    def test_late_source_fact_is_temporal_leakage(self):
        payload = self._materialization()
        payload["observations"][0]["source_timestamp"] = "2025-03-01T00:00:00+00:00"

        report = TemporalProxyAvailabilityAuditor.audit(self.plan, payload)

        self.assertFalse(report["passed"])
        self.assertTrue(
            any(item["code"] == "timestamp_after_cutoff" for item in report["violations"])
        )

    def test_same_year_history_is_rejected(self):
        payload = self._materialization()
        payload["observations"][1]["history_year"] = 2025

        report = TemporalProxyAvailabilityAuditor.audit(self.plan, payload)

        self.assertFalse(report["passed"])
        self.assertTrue(
            any(item["code"] == "historical_leakage" for item in report["violations"])
        )

    def test_cutoff_must_be_strictly_pre_release(self):
        payload = self._materialization()
        payload["targets"][0]["cutoff_at"] = "2025-06-01T00:00:00+00:00"

        report = TemporalProxyAvailabilityAuditor.audit(self.plan, payload)

        self.assertFalse(report["passed"])
        self.assertTrue(
            any(item["code"] == "cutoff_not_pre_release" for item in report["violations"])
        )

    def test_omitted_target_feature_row_is_violation_not_implicit_zero(self):
        payload = self._materialization()
        payload["observations"] = [
            item
            for item in payload["observations"]
            if not (
                item["target_id"] == "tt-b"
                and item["feature_name"] == "source_worldbuilding_entity_count"
            )
        ]

        report = TemporalProxyAvailabilityAuditor.audit(self.plan, payload)

        self.assertFalse(report["passed"])
        missing = [item for item in report["violations"] if item["code"] == "observation_missing"]
        self.assertEqual(len(missing), 1)
        self.assertEqual(missing[0]["target_id"], "tt-b")

    def test_missing_row_requires_null_value_and_reason(self):
        payload = self._materialization()
        row = payload["observations"][2]
        row["value"] = 0

        report = TemporalProxyAvailabilityAuditor.audit(self.plan, payload)
        self.assertTrue(
            any(item["code"] == "missing_row_has_value" for item in report["violations"])
        )

        payload = self._materialization()
        payload["observations"][2]["missing_reason"] = ""
        with self.assertRaises(ProxyHypothesisValidationError):
            TemporalProxyAvailabilityAuditor.audit(self.plan, payload)

    def _baseline_candidate(self, audit: dict) -> tuple[dict, dict]:
        common = {
            "dataset_fingerprint_sha256": "d" * 64,
            "train_year_from": 2000,
            "train_year_to": 2023,
            "test_year_from": 2024,
            "test_year_to": 2025,
            "train_rows": 1000,
            "test_rows": 200,
            "total_rows": 1200,
            "test_rmse": 1.20,
            "test_r2": 0.30,
        }
        baseline = {
            **common,
            "schema_label": "v15",
            "test_mae": 1.00,
            "feature_names": ["base_numeric", "director_id"],
        }
        candidate = {
            **common,
            "label": "p6-demo-candidate",
            "test_mae": 0.97,
            "test_rmse": 1.17,
            "test_r2": 0.33,
            "plan_fingerprint_sha256": self.plan["plan_fingerprint_sha256"],
            "proxy_audit_fingerprint_sha256": audit["audit_fingerprint_sha256"],
            "feature_names": [
                "base_numeric",
                "director_id",
                "source_worldbuilding_entity_count",
                "source_complexity_known",
                "writer_adaptation_count",
                "writer_adaptation_known",
            ],
        }
        return baseline, candidate

    def test_gate_accepts_improving_clean_ablation(self):
        audit = TemporalProxyAvailabilityAuditor.audit(self.plan, self._materialization())
        baseline, candidate = self._baseline_candidate(audit)

        result = GenericProxyAblationGate.compare(
            self.plan,
            audit,
            baseline,
            candidate,
        )

        self.assertTrue(result["comparison"]["passed"])
        self.assertTrue(result["comparison"]["candidate_improves_mae"])
        self.assertEqual(result["candidate"]["added_features"], [
            "source_complexity_known",
            "source_worldbuilding_entity_count",
            "writer_adaptation_count",
            "writer_adaptation_known",
        ])
        self.assertFalse(result["published"])
        self.assertEqual(len(result["gate_fingerprint_sha256"]), 64)

    def test_gate_rejects_different_dataset_or_holdout(self):
        audit = TemporalProxyAvailabilityAuditor.audit(self.plan, self._materialization())
        baseline, candidate = self._baseline_candidate(audit)
        candidate["dataset_fingerprint_sha256"] = "e" * 64
        with self.assertRaises(ProxyHypothesisValidationError):
            GenericProxyAblationGate.compare(self.plan, audit, baseline, candidate)

        baseline, candidate = self._baseline_candidate(audit)
        candidate["test_rows"] = 201
        with self.assertRaises(ProxyHypothesisValidationError):
            GenericProxyAblationGate.compare(self.plan, audit, baseline, candidate)

    def test_gate_rejects_unregistered_feature_changes(self):
        audit = TemporalProxyAvailabilityAuditor.audit(self.plan, self._materialization())
        baseline, candidate = self._baseline_candidate(audit)
        candidate["feature_names"].append("unregistered_magic_proxy")

        with self.assertRaises(ProxyHypothesisValidationError):
            GenericProxyAblationGate.compare(self.plan, audit, baseline, candidate)

    def test_gate_rejects_candidate_not_bound_to_plan_or_audit(self):
        audit = TemporalProxyAvailabilityAuditor.audit(self.plan, self._materialization())
        baseline, candidate = self._baseline_candidate(audit)
        candidate["plan_fingerprint_sha256"] = "0" * 64
        with self.assertRaises(ProxyHypothesisValidationError):
            GenericProxyAblationGate.compare(self.plan, audit, baseline, candidate)

        baseline, candidate = self._baseline_candidate(audit)
        candidate["proxy_audit_fingerprint_sha256"] = "0" * 64
        with self.assertRaises(ProxyHypothesisValidationError):
            GenericProxyAblationGate.compare(self.plan, audit, baseline, candidate)

    def test_require_improvement_rejects_flat_or_worse_mae(self):
        audit = TemporalProxyAvailabilityAuditor.audit(self.plan, self._materialization())
        baseline, candidate = self._baseline_candidate(audit)
        candidate["test_mae"] = baseline["test_mae"]

        result = GenericProxyAblationGate.compare(self.plan, audit, baseline, candidate)

        self.assertFalse(result["comparison"]["passed"])
        self.assertEqual(result["comparison"]["reason"], "mae_not_improved")

    def test_failed_temporal_audit_cannot_enter_gate(self):
        materialization = self._materialization()
        materialization["observations"][0]["source_timestamp"] = "2025-04-01T00:00:00+00:00"
        audit = TemporalProxyAvailabilityAuditor.audit(self.plan, materialization)
        baseline, candidate = self._baseline_candidate(audit)

        with self.assertRaises(ProxyHypothesisValidationError):
            GenericProxyAblationGate.compare(self.plan, audit, baseline, candidate)

    def test_modified_preregistered_plan_is_rejected(self):
        modified = copy.deepcopy(self.plan)
        modified["ablation"]["candidate_label"] = "changed-after-looking-at-results"

        with self.assertRaises(ProxyHypothesisValidationError):
            TemporalProxyAvailabilityAuditor.audit(modified, self._materialization())


if __name__ == "__main__":
    unittest.main()
