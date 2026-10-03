from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from src.proxy_candidate_schema import ProxyCandidateSchemaRegistry
from src.proxy_hypotheses import ProxyHypothesisStore, ProxyHypothesisValidationError


class ProxyCandidateSchemaTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "proxy.duckdb"
        self.store = ProxyHypothesisStore(self.db)
        self.schemas = ProxyCandidateSchemaRegistry(self.store)

    def tearDown(self):
        self.schemas.close()
        self.store.close()
        self.tmp.cleanup()

    def _accept(
        self,
        hypothesis_id: str,
        *,
        feature_name: str,
        source_layer: str = "source_context",
        temporal_contract: str = "known_at_lte_cutoff",
        proxy_role: str = "primary",
        coverage_feature: str | None = None,
        baseline_schema: str = "v15",
        dataset_fp: str = "a" * 64,
        holdout_start: int = 2024,
        holdout_end: int = 2025,
        holdout_rows: int = 200,
        candidate_mae: float = 0.95,
        passed: bool = True,
    ) -> dict:
        self.store.upsert_hypothesis(
            {
                "hypothesis_id": hypothesis_id,
                "title": hypothesis_id,
                "dimension": "worldbuilding",
                "change_type": "compressed",
                "rationale": "test",
            }
        )
        self.store.add_evidence(
            {
                "hypothesis_id": hypothesis_id,
                "evidence_kind": "story_transform",
                "reference_id": f"evidence:{hypothesis_id}",
                "confidence": 0.9,
            }
        )
        self.store.add_proxy_feature(
            {
                "hypothesis_id": hypothesis_id,
                "feature_name": feature_name,
                "source_layer": source_layer,
                "temporal_contract": temporal_contract,
                "proxy_role": proxy_role,
                "coverage_feature": coverage_feature,
            }
        )
        self.store.set_ablation_spec(
            {
                "hypothesis_id": hypothesis_id,
                "baseline_schema": baseline_schema,
                "candidate_label": f"candidate-{hypothesis_id}",
                "holdout_policy": "stable_temporal_last_two_years",
                "primary_metric": "mae",
                "max_mae_regression": 0.0,
                "require_improvement": True,
            }
        )
        self.store.mark_ablation_ready(hypothesis_id)
        plan = self.store.export_ablation_plan(hypothesis_id)
        now = datetime.now(timezone.utc)
        result_fp = (hypothesis_id.encode("utf-8").hex() + "b" * 64)[:64].ljust(64, "b")
        self.schemas.conn.execute(
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
                f"result-{hypothesis_id}",
                hypothesis_id,
                plan["plan_fingerprint_sha256"],
                result_fp,
                dataset_fp,
                "stable_temporal_last_two_years",
                holdout_start,
                holdout_end,
                holdout_rows,
                1.0,
                candidate_mae,
                candidate_mae - 1.0,
                passed,
                "accepted_ablation" if passed else "rejected_mae_not_improved",
                "test-runner",
                "1",
                now,
                now,
            ],
        )
        if passed:
            self.store.conn.execute(
                "UPDATE proxy_hypotheses SET status='accepted', rejection_reason=NULL WHERE hypothesis_id=?",
                [hypothesis_id],
            )
        return plan

    def test_freeze_single_accepted_hypothesis(self):
        self._accept(
            "h1",
            feature_name="planned_runtime_minutes",
            temporal_contract="planned_before_release",
            coverage_feature="source_format_known",
        )
        schema = self.schemas.freeze(
            {
                "schema_id": "schema-1",
                "schema_label": "p6-candidate-1",
                "schema_version": 1,
                "hypothesis_ids": ["h1"],
            }
        )
        self.assertEqual(schema["status"], "frozen_research_candidate")
        self.assertEqual(schema["base_schema"], "v15")
        self.assertEqual(
            schema["model_feature_names"],
            ["planned_runtime_minutes", "source_format_known"],
        )
        self.assertFalse(schema["combined_ablation_required"])
        self.assertTrue(schema["production_quality_gate_required"])
        self.assertFalse(schema["automatic_catboost_inclusion"])
        self.assertFalse(schema["production_publication_allowed"])
        self.assertEqual(len(schema["schema_fingerprint_sha256"]), 64)

    def test_identical_feature_contract_from_two_hypotheses_is_deduplicated(self):
        self._accept("h1", feature_name="source_work_count")
        self._accept("h2", feature_name="source_work_count")
        schema = self.schemas.freeze(
            {
                "schema_id": "schema-shared",
                "schema_label": "p6-shared",
                "schema_version": 2,
                "hypothesis_ids": ["h2", "h1"],
            }
        )
        self.assertEqual(schema["model_feature_names"], ["source_work_count"])
        contract = schema["feature_contracts"][0]
        self.assertEqual(contract["supporting_hypothesis_ids"], ["h1", "h2"])
        self.assertEqual(len(contract["acceptance_result_fingerprints"]), 2)
        self.assertTrue(schema["combined_ablation_required"])

    def test_conflicting_feature_contract_is_rejected(self):
        self._accept("h1", feature_name="shared_feature", source_layer="source_context")
        self._accept(
            "h2",
            feature_name="shared_feature",
            source_layer="production_context",
            temporal_contract="known_at_lte_cutoff",
        )
        with self.assertRaises(ProxyHypothesisValidationError):
            self.schemas.freeze(
                {
                    "schema_id": "schema-conflict",
                    "schema_label": "p6-conflict",
                    "schema_version": 3,
                    "hypothesis_ids": ["h1", "h2"],
                }
            )

    def test_different_dataset_or_holdout_cannot_be_combined(self):
        self._accept("h1", feature_name="f1", dataset_fp="a" * 64)
        self._accept("h2", feature_name="f2", dataset_fp="b" * 64)
        with self.assertRaises(ProxyHypothesisValidationError):
            self.schemas.freeze(
                {
                    "schema_id": "schema-dataset",
                    "schema_label": "p6-dataset",
                    "schema_version": 4,
                    "hypothesis_ids": ["h1", "h2"],
                }
            )

        # Новый независимый registry для проверки holdout mismatch без version collision.
        self.store.conn.execute("DELETE FROM proxy_candidate_schemas")
        self.store.conn.execute("DELETE FROM proxy_ablation_results")
        self.store.conn.execute("DELETE FROM proxy_feature_specs")
        self.store.conn.execute("DELETE FROM proxy_ablation_specs")
        self.store.conn.execute("DELETE FROM proxy_hypothesis_evidence")
        self.store.conn.execute("DELETE FROM proxy_hypotheses")
        self._accept("x1", feature_name="f1")
        self._accept("x2", feature_name="f2", holdout_start=2023)
        with self.assertRaises(ProxyHypothesisValidationError):
            self.schemas.freeze(
                {
                    "schema_id": "schema-holdout",
                    "schema_label": "p6-holdout",
                    "schema_version": 5,
                    "hypothesis_ids": ["x1", "x2"],
                }
            )

    def test_different_baseline_schema_is_rejected(self):
        self._accept("h1", feature_name="f1", baseline_schema="v15")
        self._accept("h2", feature_name="f2", baseline_schema="v16")
        with self.assertRaises(ProxyHypothesisValidationError):
            self.schemas.freeze(
                {
                    "schema_id": "schema-base",
                    "schema_label": "p6-base",
                    "schema_version": 6,
                    "hypothesis_ids": ["h1", "h2"],
                }
            )

    def test_status_accepted_without_passing_result_is_not_enough(self):
        self.store.upsert_hypothesis(
            {
                "hypothesis_id": "manual",
                "title": "manual",
                "dimension": "worldbuilding",
                "change_type": "compressed",
                "rationale": "manual",
                "status": "accepted",
            }
        )
        with self.assertRaises(ProxyHypothesisValidationError):
            self.schemas.freeze(
                {
                    "schema_id": "schema-manual",
                    "schema_label": "p6-manual",
                    "schema_version": 7,
                    "hypothesis_ids": ["manual"],
                }
            )

    def test_contract_mutation_after_acceptance_invalidates_result(self):
        self._accept("h1", feature_name="source_work_count")
        self.store.add_proxy_feature(
            {
                "hypothesis_id": "h1",
                "feature_name": "source_primary_work_count",
                "source_layer": "source_context",
                "temporal_contract": "known_at_lte_cutoff",
                "proxy_role": "context",
            }
        )
        with self.assertRaises(ProxyHypothesisValidationError):
            self.schemas.freeze(
                {
                    "schema_id": "schema-stale",
                    "schema_label": "p6-stale",
                    "schema_version": 8,
                    "hypothesis_ids": ["h1"],
                }
            )

    def test_same_schema_freeze_is_idempotent_and_version_collision_is_blocked(self):
        self._accept("h1", feature_name="source_work_count")
        payload = {
            "schema_id": "schema-stable",
            "schema_label": "p6-stable",
            "schema_version": 9,
            "hypothesis_ids": ["h1"],
        }
        first = self.schemas.freeze(payload)
        second = self.schemas.freeze(payload)
        self.assertFalse(first["idempotent"])
        self.assertTrue(second["idempotent"])
        self.assertEqual(first["schema_fingerprint_sha256"], second["schema_fingerprint_sha256"])
        with self.assertRaises(ProxyHypothesisValidationError):
            self.schemas.freeze(
                {
                    "schema_id": "other-id",
                    "schema_label": "other-label",
                    "schema_version": 9,
                    "hypothesis_ids": ["h1"],
                }
            )

    def test_list_and_get_are_stable(self):
        self._accept("h1", feature_name="source_work_count")
        schema = self.schemas.freeze(
            {
                "schema_id": "schema-list",
                "schema_label": "p6-list",
                "schema_version": 10,
                "hypothesis_ids": ["h1"],
            }
        )
        loaded = self.schemas.get("schema-list")
        rows = self.schemas.list_schemas()
        self.assertEqual(loaded["schema_fingerprint_sha256"], schema["schema_fingerprint_sha256"])
        self.assertEqual(rows[0]["schema_id"], "schema-list")


if __name__ == "__main__":
    unittest.main()
