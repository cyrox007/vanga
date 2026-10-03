from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from src.proxy_hypotheses import (
    ProxyHypothesisStore,
    ProxyHypothesisValidationError,
)


class ProxyHypothesisTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = ProxyHypothesisStore(Path(self.tmp.name) / "proxy.duckdb")

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def _create_hypothesis(self, hypothesis_id: str = "worldbuilding-compression") -> str:
        return self.store.upsert_hypothesis(
            {
                "hypothesis_id": hypothesis_id,
                "title": "Сложный worldbuilding и ограниченный формат повышают риск compression",
                "dimension": "worldbuilding",
                "change_type": "compressed",
                "rationale": (
                    "Retrospective StoryDiff показывает compression; проверяем только заранее "
                    "доступные source complexity/runtime/team-history proxies."
                ),
            }
        )

    def _complete_valid_hypothesis(self) -> str:
        hypothesis_id = self._create_hypothesis()
        self.store.add_evidence(
            {
                "hypothesis_id": hypothesis_id,
                "evidence_kind": "story_transform",
                "reference_id": "story-transform:case-001:compression",
                "observation_summary": "Подтверждённый many-to-one structural compression.",
                "confidence": 0.9,
            }
        )
        self.store.add_evidence(
            {
                "hypothesis_id": hypothesis_id,
                "evidence_kind": "expert_corpus",
                "reference_id": "expert-claim:red-cynic:case-001:42",
                "observation_summary": "Эксперт независимо отметил потерю контекста.",
                "confidence": 0.8,
            }
        )
        self.store.add_proxy_feature(
            {
                "hypothesis_id": hypothesis_id,
                "feature_name": "source_worldbuilding_entity_count",
                "source_layer": "source_context",
                "temporal_contract": "known_at_lte_cutoff",
                "proxy_role": "primary",
                "coverage_feature": "source_complexity_coverage",
            }
        )
        self.store.add_proxy_feature(
            {
                "hypothesis_id": hypothesis_id,
                "feature_name": "planned_runtime_minutes",
                "source_layer": "source_context",
                "temporal_contract": "planned_before_release",
                "proxy_role": "context",
            }
        )
        self.store.add_proxy_feature(
            {
                "hypothesis_id": hypothesis_id,
                "feature_name": "writer_adaptation_count",
                "source_layer": "imdb_history",
                "temporal_contract": "history_before_target_year",
                "proxy_role": "context",
                "coverage_feature": "writer_adaptation_history_known",
            }
        )
        self.store.set_ablation_spec(
            {
                "hypothesis_id": hypothesis_id,
                "baseline_schema": "v15",
                "candidate_label": "p6-worldbuilding-compression-v1",
                "holdout_policy": "stable_temporal_last_two_years",
                "primary_metric": "mae",
                "max_mae_regression": 0.0,
                "require_improvement": True,
            }
        )
        return hypothesis_id

    def test_valid_hypothesis_requires_evidence_proxy_and_ablation_spec(self):
        hypothesis_id = self._create_hypothesis()
        initial = self.store.validation_report(hypothesis_id)
        self.assertFalse(initial["ready_for_ablation"])
        self.assertEqual(
            set(initial["issues"]),
            {
                "retrospective_evidence_missing",
                "pre_release_proxy_missing",
                "ablation_spec_missing",
            },
        )

        completed = self._complete_valid_hypothesis_for_existing(hypothesis_id)
        self.assertTrue(completed["ready_for_ablation"])
        self.assertEqual(completed["issues"], [])

    def _complete_valid_hypothesis_for_existing(self, hypothesis_id: str) -> dict:
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
                "candidate_label": "candidate-demo",
            }
        )
        return self.store.validation_report(hypothesis_id)

    def test_mark_ready_and_export_plan_keep_retro_evidence_out_of_features(self):
        hypothesis_id = self._complete_valid_hypothesis()
        ready = self.store.mark_ablation_ready(hypothesis_id)
        plan = self.store.export_ablation_plan(hypothesis_id)
        serialized = json.dumps(plan, ensure_ascii=False, default=str)

        self.assertTrue(ready["ready_for_ablation"])
        self.assertEqual(ready["status"], "ablation_ready")
        self.assertFalse(plan["post_release_features_allowed"])
        self.assertFalse(plan["expert_interpretation_as_feature"])
        self.assertFalse(plan["automatic_catboost_inclusion"])
        self.assertIn("expert-claim:red-cynic", serialized)
        self.assertNotIn("expert_interpretation", serialized)
        feature_names = {
            item["feature_name"] for item in plan["candidate_pre_release_features"]
        }
        self.assertEqual(
            feature_names,
            {
                "source_worldbuilding_entity_count",
                "planned_runtime_minutes",
                "writer_adaptation_count",
            },
        )
        self.assertEqual(len(plan["plan_fingerprint_sha256"]), 64)
        self.assertEqual(
            plan["plan_fingerprint_sha256"],
            self.store.export_ablation_plan(hypothesis_id)["plan_fingerprint_sha256"],
        )

    def test_retrospective_and_target_features_are_rejected_as_proxies(self):
        hypothesis_id = self._create_hypothesis()
        forbidden = [
            "retro_adapt_worldbuilding_severity",
            "storydiff_removed_count",
            "story_transform_compression_count",
            "expert_red_cynic_signal",
            "current_imdb_rating",
            "actual_rating",
        ]
        for index, feature_name in enumerate(forbidden):
            with self.subTest(feature_name=feature_name):
                with self.assertRaises(ProxyHypothesisValidationError):
                    self.store.add_proxy_feature(
                        {
                            "proxy_id": f"forbidden-{index}",
                            "hypothesis_id": hypothesis_id,
                            "feature_name": feature_name,
                            "source_layer": "source_context",
                            "temporal_contract": "known_at_lte_cutoff",
                        }
                    )

    def test_proxy_must_be_explicitly_available_before_release(self):
        hypothesis_id = self._create_hypothesis()
        with self.assertRaises(ProxyHypothesisValidationError):
            self.store.add_proxy_feature(
                {
                    "hypothesis_id": hypothesis_id,
                    "feature_name": "future_signal",
                    "source_layer": "pre_release_public_signal",
                    "temporal_contract": "published_at_lte_cutoff",
                    "available_before_release": False,
                }
            )

    def test_temporal_contract_must_match_source_layer(self):
        hypothesis_id = self._create_hypothesis()
        with self.assertRaises(ProxyHypothesisValidationError):
            self.store.add_proxy_feature(
                {
                    "hypothesis_id": hypothesis_id,
                    "feature_name": "director_prior_count",
                    "source_layer": "imdb_history",
                    "temporal_contract": "known_at_lte_cutoff",
                }
            )

    def test_expert_interpretation_or_full_text_cannot_be_stored_in_registry(self):
        hypothesis_id = self._create_hypothesis()
        with self.assertRaises(ProxyHypothesisValidationError):
            self.store.add_evidence(
                {
                    "hypothesis_id": hypothesis_id,
                    "evidence_kind": "expert_corpus",
                    "reference_id": "claim:1",
                    "expert_interpretation": "Не должно храниться здесь",
                }
            )
        with self.assertRaises(ProxyHypothesisValidationError):
            self.store.upsert_hypothesis(
                {
                    "hypothesis_id": "bad-payload",
                    "title": "Bad",
                    "dimension": "plot",
                    "change_type": "removed",
                    "rationale": "Bad",
                    "full_text": "Сторонний полный текст",
                }
            )

    def test_rejected_hypothesis_requires_reason_and_cannot_be_ready(self):
        with self.assertRaises(ProxyHypothesisValidationError):
            self.store.upsert_hypothesis(
                {
                    "hypothesis_id": "rejected-no-reason",
                    "title": "Rejected",
                    "dimension": "plot",
                    "change_type": "removed",
                    "rationale": "Проверка",
                    "status": "rejected",
                }
            )

        hypothesis_id = self.store.upsert_hypothesis(
            {
                "hypothesis_id": "rejected-with-reason",
                "title": "Rejected",
                "dimension": "plot",
                "change_type": "removed",
                "rationale": "Проверка",
                "status": "rejected",
                "rejection_reason": "Proxy нельзя получить до премьеры.",
            }
        )
        report = self.store.validation_report(hypothesis_id)
        self.assertIn("hypothesis_rejected", report["issues"])
        with self.assertRaises(ProxyHypothesisValidationError):
            self.store.mark_ablation_ready(hypothesis_id)

    def test_duplicate_proxy_feature_is_rejected(self):
        hypothesis_id = self._create_hypothesis()
        payload = {
            "hypothesis_id": hypothesis_id,
            "feature_name": "planned_runtime_minutes",
            "source_layer": "source_context",
            "temporal_contract": "planned_before_release",
        }
        self.store.add_proxy_feature(payload)
        with self.assertRaises(ProxyHypothesisValidationError):
            self.store.add_proxy_feature(payload)

    def test_ablation_contract_is_preregistered_and_restrictive(self):
        hypothesis_id = self._create_hypothesis()
        with self.assertRaises(ProxyHypothesisValidationError):
            self.store.set_ablation_spec(
                {
                    "hypothesis_id": hypothesis_id,
                    "baseline_schema": "v15",
                    "candidate_label": "bad-random-split",
                    "holdout_policy": "random_split",
                }
            )
        with self.assertRaises(ProxyHypothesisValidationError):
            self.store.set_ablation_spec(
                {
                    "hypothesis_id": hypothesis_id,
                    "baseline_schema": "v15",
                    "candidate_label": "bad-metric",
                    "primary_metric": "training_rmse",
                }
            )


if __name__ == "__main__":
    unittest.main()
