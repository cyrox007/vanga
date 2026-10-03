from __future__ import annotations

import unittest

from src.proxy_ablation import verify_plan_fingerprint
from src.proxy_candidate_schema_plan import ProxyCandidateSchemaPlanBuilder
from src.proxy_hypotheses import ProxyHypothesisValidationError


class _RegistryStub:
    def get(self, schema_id):
        if schema_id != "schema-1":
            raise AssertionError(schema_id)
        return {
            "schema_id": "schema-1",
            "schema_label": "p6-candidate-1",
            "schema_version": 1,
            "schema_fingerprint_sha256": "c" * 64,
            "base_schema": "v15",
            "dataset_fingerprint_sha256": "a" * 64,
            "holdout": {
                "policy": "stable_temporal_last_two_years",
                "start_year": 2024,
                "end_year": 2025,
                "row_count": 200,
            },
            "hypothesis_ids": ["h1", "h2"],
            "feature_contracts": [
                {
                    "feature_name": "planned_runtime_minutes",
                    "source_layer": "source_context",
                    "temporal_contract": "planned_before_release",
                    "proxy_roles": ["primary"],
                    "supporting_hypothesis_ids": ["h1"],
                    "acceptance_result_fingerprints": ["1" * 64],
                    "coverage_for_features": [],
                },
                {
                    "feature_name": "source_format_known",
                    "source_layer": "source_context",
                    "temporal_contract": "planned_before_release",
                    "proxy_roles": ["coverage"],
                    "supporting_hypothesis_ids": ["h1"],
                    "acceptance_result_fingerprints": ["1" * 64],
                    "coverage_for_features": ["planned_runtime_minutes"],
                },
                {
                    "feature_name": "production_rewrite_count",
                    "source_layer": "production_context",
                    "temporal_contract": "known_at_lte_cutoff",
                    "proxy_roles": ["context"],
                    "supporting_hypothesis_ids": ["h2"],
                    "acceptance_result_fingerprints": ["2" * 64],
                    "coverage_for_features": [],
                },
            ],
            "created_at": "2026-10-03T20:00:00+00:00",
        }


class ProxyCandidateSchemaPlanTests(unittest.TestCase):
    def setUp(self):
        self.builder = ProxyCandidateSchemaPlanBuilder(_RegistryStub())

    def test_builds_valid_preregistered_plan_for_existing_materializers(self):
        plan = self.builder.build("schema-1")
        self.assertEqual(verify_plan_fingerprint(plan), plan["plan_fingerprint_sha256"])
        self.assertEqual(plan["ablation"]["baseline_schema"], "v15")
        self.assertEqual(
            plan["expected_dataset_fingerprint_sha256"], "a" * 64
        )
        self.assertFalse(plan["production_publication_allowed"])
        specs = {item["feature_name"]: item for item in plan["candidate_pre_release_features"]}
        self.assertEqual(set(specs), {
            "planned_runtime_minutes",
            "source_format_known",
            "production_rewrite_count",
        })
        self.assertEqual(specs["source_format_known"]["proxy_role"], "coverage")
        self.assertIsNone(specs["planned_runtime_minutes"]["coverage_feature"])

    def test_combined_acceptance_policy_is_strict_by_default(self):
        plan = self.builder.build("schema-1")
        self.assertTrue(plan["ablation"]["require_improvement"])
        self.assertEqual(plan["ablation"]["max_mae_regression"], 0.0)
        self.assertEqual(plan["ablation"]["primary_metric"], "mae")

    def test_tolerance_can_be_preregistered_but_is_bounded(self):
        plan = self.builder.build(
            "schema-1",
            require_improvement=False,
            max_mae_regression=0.01,
        )
        self.assertFalse(plan["ablation"]["require_improvement"])
        self.assertEqual(plan["ablation"]["max_mae_regression"], 0.01)
        with self.assertRaises(ProxyHypothesisValidationError):
            self.builder.build("schema-1", max_mae_regression=-0.1)


if __name__ == "__main__":
    unittest.main()
