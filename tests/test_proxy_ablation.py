from __future__ import annotations

import hashlib
import json
import unittest

from src.proxy_ablation import (
    ProxyAblationEvaluator,
    ProxyAblationValidationError,
    TemporalAvailabilityAuditor,
)


def _fingerprint(payload):
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class ProxyAblationTests(unittest.TestCase):
    def setUp(self):
        self.audit = TemporalAvailabilityAuditor()
        self.evaluator = ProxyAblationEvaluator()
        body = {
            "registry_version": 1,
            "hypothesis_id": "worldbuilding-compression",
            "title": "demo",
            "retrospective_signal": {
                "dimension": "worldbuilding",
                "change_type": "compressed",
                "rationale": "demo",
                "evidence": [{"evidence_kind": "story_transform", "reference_id": "x", "confidence": 0.9}],
            },
            "candidate_pre_release_features": [
                {
                    "feature_name": "director_prior_count",
                    "source_layer": "imdb_history",
                    "temporal_contract": "history_before_target_year",
                    "proxy_role": "context",
                    "coverage_feature": None,
                    "notes": None,
                },
                {
                    "feature_name": "source_worldbuilding_entity_count",
                    "source_layer": "source_context",
                    "temporal_contract": "known_at_lte_cutoff",
                    "proxy_role": "primary",
                    "coverage_feature": "source_complexity_coverage",
                    "notes": None,
                },
                {
                    "feature_name": "public_signal_count",
                    "source_layer": "pre_release_public_signal",
                    "temporal_contract": "published_at_lte_cutoff",
                    "proxy_role": "context",
                    "coverage_feature": None,
                    "notes": None,
                },
                {
                    "feature_name": "planned_runtime_minutes",
                    "source_layer": "source_context",
                    "temporal_contract": "planned_before_release",
                    "proxy_role": "context",
                    "coverage_feature": None,
                    "notes": None,
                },
            ],
            "ablation": {
                "baseline_schema": "v15",
                "candidate_label": "p6-demo",
                "holdout_policy": "stable_temporal_last_two_years",
                "primary_metric": "mae",
                "max_mae_regression": 0.0,
                "require_improvement": True,
                "dataset_fingerprint_required": True,
                "preregistered_at": "2026-10-03T20:00:00+00:00",
            },
            "status": "ablation_ready",
            "post_release_features_allowed": False,
            "expert_interpretation_as_feature": False,
            "automatic_catboost_inclusion": False,
        }
        self.plan = dict(body)
        self.plan["plan_fingerprint_sha256"] = _fingerprint(body)
        self.dataset_fp = "a" * 64

    def _row(self):
        return {
            "target_id": "tt-demo",
            "target_year": 2025,
            "cutoff_at": "2025-03-01T00:00:00+00:00",
            "release_at": "2025-06-01T00:00:00+00:00",
            "features": {
                "director_prior_count": {
                    "value": 7,
                    "available_before_release": True,
                    "history_max_year": 2024,
                },
                "source_worldbuilding_entity_count": {
                    "value": 42,
                    "available_before_release": True,
                    "known_at": "2025-02-01T00:00:00+00:00",
                },
                "public_signal_count": {
                    "value": 15,
                    "available_before_release": True,
                    "published_at": "2025-02-15T00:00:00+00:00",
                },
                "planned_runtime_minutes": {
                    "value": 125,
                    "available_before_release": True,
                    "known_at": "2025-01-15T00:00:00+00:00",
                },
            },
        }

    def _materialized(self, row=None):
        return {
            "dataset_fingerprint_sha256": self.dataset_fp,
            "rows": [row or self._row()],
        }

    def test_valid_temporal_proofs_pass(self):
        report = self.audit.audit(self.plan, self._materialized())
        self.assertTrue(report["passed"])
        self.assertEqual(report["issues"], [])
        self.assertEqual(report["feature_coverage"]["director_prior_count"]["proof_coverage"], 1.0)
        self.assertFalse(report["retrospective_values_allowed"])

    def test_history_same_year_is_rejected(self):
        row = self._row()
        row["features"]["director_prior_count"]["history_max_year"] = 2025
        report = self.audit.audit(self.plan, self._materialized(row))
        self.assertFalse(report["passed"])
        self.assertIn("history_not_before_target_year", {item["code"] for item in report["issues"]})

    def test_known_and_published_after_cutoff_are_rejected(self):
        row = self._row()
        row["features"]["source_worldbuilding_entity_count"]["known_at"] = "2025-03-02T00:00:00Z"
        row["features"]["public_signal_count"]["published_at"] = "2025-04-01T00:00:00Z"
        report = self.audit.audit(self.plan, self._materialized(row))
        codes = {item["code"] for item in report["issues"]}
        self.assertIn("known_after_cutoff", codes)
        self.assertIn("published_after_cutoff", codes)

    def test_planned_fact_requires_pre_release_visibility(self):
        row = self._row()
        row["features"]["planned_runtime_minutes"]["known_at"] = "2025-07-01T00:00:00Z"
        report = self.audit.audit(self.plan, self._materialized(row))
        self.assertFalse(report["passed"])
        self.assertIn("planned_fact_known_after_cutoff", {item["code"] for item in report["issues"]})

    def test_unregistered_feature_is_rejected(self):
        row = self._row()
        row["features"]["storydiff_removed_count"] = {
            "value": 9,
            "available_before_release": True,
            "known_at": "2025-01-01T00:00:00Z",
        }
        with self.assertRaises(ProxyAblationValidationError):
            self.audit.audit(self.plan, self._materialized(row))

    def test_plan_fingerprint_drift_is_rejected(self):
        changed = dict(self.plan)
        changed["title"] = "changed after preregistration"
        with self.assertRaises(ProxyAblationValidationError):
            self.audit.audit(changed, self._materialized())

    def _result(self, *, mae, dataset_fp=None, start=2024, end=2025, rows=100):
        return {
            "dataset_fingerprint_sha256": dataset_fp or self.dataset_fp,
            "holdout_start_year": start,
            "holdout_end_year": end,
            "test_rows": rows,
            "mae": mae,
        }

    def test_ablation_accepts_improvement_only_on_same_dataset_and_holdout(self):
        audit = self.audit.audit(self.plan, self._materialized())
        result = self.evaluator.evaluate(
            self.plan,
            audit,
            self._result(mae=1.05),
            self._result(mae=1.01),
        )
        self.assertTrue(result["passed"])
        self.assertEqual(result["reason"], "mae_improved")
        self.assertAlmostEqual(result["mae_delta"], -0.04)
        self.assertFalse(result["automatic_catboost_publication"])

    def test_ablation_rejects_regression_when_improvement_required(self):
        audit = self.audit.audit(self.plan, self._materialized())
        result = self.evaluator.evaluate(
            self.plan,
            audit,
            self._result(mae=1.0),
            self._result(mae=1.001),
        )
        self.assertFalse(result["passed"])
        self.assertEqual(result["reason"], "mae_regression")

    def test_ablation_rejects_dataset_or_holdout_mismatch(self):
        audit = self.audit.audit(self.plan, self._materialized())
        with self.assertRaises(ProxyAblationValidationError):
            self.evaluator.evaluate(
                self.plan,
                audit,
                self._result(mae=1.0),
                self._result(mae=0.9, dataset_fp="b" * 64),
            )
        with self.assertRaises(ProxyAblationValidationError):
            self.evaluator.evaluate(
                self.plan,
                audit,
                self._result(mae=1.0),
                self._result(mae=0.9, start=2023),
            )

    def test_failed_temporal_audit_blocks_ablation(self):
        row = self._row()
        row["features"]["director_prior_count"]["history_max_year"] = 2025
        audit = self.audit.audit(self.plan, self._materialized(row))
        with self.assertRaises(ProxyAblationValidationError):
            self.evaluator.evaluate(
                self.plan,
                audit,
                self._result(mae=1.0),
                self._result(mae=0.8),
            )


if __name__ == "__main__":
    unittest.main()
