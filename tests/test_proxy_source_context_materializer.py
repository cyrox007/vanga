from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from src.proxy_ablation import TemporalProxyAvailabilityAuditor
from src.proxy_hypotheses import ProxyHypothesisValidationError
from src.proxy_source_context_materializer import ProxySourceContextMaterializer
from src.source_complexity import SourceComplexityContext
from src.source_context import SourceContextStore


def _fingerprint(payload):
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class ProxySourceContextMaterializerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "source.duckdb"
        self.store = SourceContextStore(self.db)
        self.complexity = SourceComplexityContext(self.store)
        self.materializer = ProxySourceContextMaterializer(self.store)
        self._seed()

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def _seed(self):
        self.store.upsert_source(
            {
                "source_id": "source-a",
                "url": "https://example.test/a",
                "published_at": "2024-01-01T00:00:00Z",
                "retrieved_at": "2025-01-01T00:00:00Z",
            }
        )
        self.store.upsert_source(
            {
                "source_id": "source-b",
                "url": "https://example.test/b",
                "published_at": "2024-01-02T00:00:00Z",
                "retrieved_at": "2025-01-02T00:00:00Z",
            }
        )
        self.store.upsert_work(
            {
                "work_id": "book-1",
                "title": "Book One",
                "source_type": "novel_series",
                "first_publication_at": "2000-01-01T00:00:00Z",
                "series_id": "series-x",
                "series_position": 1,
                "series_size": 3,
            }
        )
        self.store.upsert_work(
            {
                "work_id": "book-2",
                "title": "Book Two",
                "source_type": "novel_series",
                "first_publication_at": "2002-01-01T00:00:00Z",
                "series_id": "series-x",
                "series_position": 2,
                "series_size": 3,
            }
        )
        self.store.upsert_project(
            {
                "project_id": "film-1",
                "imdb_id": "tt0000001",
                "title": "Film One",
                "release_at": "2025-06-01T00:00:00Z",
                "adaptation_format": "film",
                "planned_runtime_minutes": 120,
                "format_known_at": "2025-01-10T00:00:00Z",
            }
        )
        self.store.link_source(
            {
                "link_id": "link-1",
                "project_id": "film-1",
                "work_id": "book-1",
                "relation_type": "based_on",
                "is_primary": True,
                "known_at": "2025-01-15T00:00:00Z",
                "source_id": "source-a",
            }
        )
        # Дублирующее evidence той же logical связи не должно удвоить aggregate.
        self.store.link_source(
            {
                "link_id": "link-1-duplicate-evidence",
                "project_id": "film-1",
                "work_id": "book-1",
                "relation_type": "based_on",
                "is_primary": True,
                "known_at": "2025-01-20T00:00:00Z",
                "source_id": "source-b",
            }
        )
        self.store.link_source(
            {
                "link_id": "link-2-late",
                "project_id": "film-1",
                "work_id": "book-2",
                "relation_type": "based_on",
                "is_primary": False,
                "known_at": "2025-04-01T00:00:00Z",
                "source_id": "source-a",
            }
        )
        self.complexity.add_snapshot(
            {
                "snapshot_id": "complexity-v1-book1",
                "work_id": "book-1",
                "method": "story-structure",
                "method_version": "1",
                "measured_at": "2025-01-25T00:00:00Z",
                "known_at": "2025-01-25T00:00:00Z",
                "source_id": "source-a",
                "coverage_fraction": 0.9,
                "metrics": {
                    "source_length_words": 100000,
                    "worldbuilding_entity_count": 40,
                },
            }
        )
        self.complexity.add_snapshot(
            {
                "snapshot_id": "complexity-v2-book1",
                "work_id": "book-1",
                "method": "story-structure",
                "method_version": "2",
                "measured_at": "2025-01-26T00:00:00Z",
                "known_at": "2025-01-26T00:00:00Z",
                "source_id": "source-a",
                "coverage_fraction": 1.0,
                "metrics": {
                    "source_length_words": 100000,
                    "worldbuilding_entity_count": 400,
                },
            }
        )
        self.complexity.add_snapshot(
            {
                "snapshot_id": "complexity-v1-book2",
                "work_id": "book-2",
                "method": "story-structure",
                "method_version": "1",
                "measured_at": "2025-04-05T00:00:00Z",
                "known_at": "2025-04-05T00:00:00Z",
                "source_id": "source-a",
                "coverage_fraction": 0.8,
                "metrics": {"worldbuilding_entity_count": 20},
            }
        )

    def _plan(self, features):
        body = {
            "registry_version": 1,
            "hypothesis_id": "source-demo",
            "title": "Demo",
            "retrospective_signal": {
                "dimension": "worldbuilding",
                "change_type": "compressed",
                "rationale": "Demo",
                "evidence": [],
            },
            "candidate_pre_release_features": features,
            "ablation": {
                "baseline_schema": "v15",
                "candidate_label": "source-demo-v1",
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
        plan = dict(body)
        plan["plan_fingerprint_sha256"] = _fingerprint(body)
        return plan

    def _target(self, cutoff="2025-03-01T00:00:00Z"):
        return {
            "target_id": "tt0000001",
            "project_id": "film-1",
            "target_year": 2025,
            "cutoff_at": cutoff,
            "release_at": "2025-06-01T00:00:00Z",
        }

    def _by_name(self, payload):
        return {item["feature_name"]: item for item in payload["observations"]}

    def test_runtime_alias_and_source_count_materialize_with_temporal_proof(self):
        plan = self._plan(
            [
                {
                    "feature_name": "planned_runtime_minutes",
                    "source_layer": "source_context",
                    "temporal_contract": "planned_before_release",
                    "proxy_role": "context",
                    "coverage_feature": None,
                    "notes": None,
                },
                {
                    "feature_name": "source_work_count",
                    "source_layer": "source_context",
                    "temporal_contract": "known_at_lte_cutoff",
                    "proxy_role": "primary",
                    "coverage_feature": None,
                    "notes": None,
                },
            ]
        )
        payload = self.materializer.materialize(plan, [self._target()])
        rows = self._by_name(payload)
        self.assertEqual(rows["planned_runtime_minutes"]["value"], 120.0)
        # book-2 раскрыт только в апреле, duplicate link book-1 не удваивает work count.
        self.assertEqual(rows["source_work_count"]["value"], 1.0)
        self.assertTrue(rows["planned_runtime_minutes"]["available"])
        self.assertIn("provenance_id", rows["source_work_count"])

        # Payload напрямую совместим с уже слитым P6 auditor (#70).
        audit_input = {
            "version": payload["version"],
            "targets": payload["targets"],
            "observations": payload["observations"],
        }
        audit = TemporalProxyAvailabilityAuditor.audit(plan, audit_input)
        self.assertTrue(audit["passed"])
        self.assertEqual(audit["violation_count"], 0)

    def test_complexity_alias_requires_exact_method_version(self):
        plan = self._plan(
            [
                {
                    "feature_name": "source_worldbuilding_entity_count",
                    "source_layer": "source_context",
                    "temporal_contract": "known_at_lte_cutoff",
                    "proxy_role": "primary",
                    "coverage_feature": None,
                    "notes": None,
                },
                {
                    "feature_name": "source_complexity_coverage",
                    "source_layer": "source_context",
                    "temporal_contract": "known_at_lte_cutoff",
                    "proxy_role": "coverage",
                    "coverage_feature": None,
                    "notes": None,
                },
            ]
        )
        payload = self.materializer.materialize(
            plan,
            [self._target()],
            complexity_method="story-structure",
            complexity_method_version="1",
        )
        rows = self._by_name(payload)
        self.assertEqual(rows["source_worldbuilding_entity_count"]["value"], 40.0)
        self.assertEqual(rows["source_complexity_coverage"]["value"], 1.0)
        self.assertIn("complexity-v1-book1", rows["source_worldbuilding_entity_count"]["provenance_id"] or "")
        # provenance_id — hash, protocol всё равно участвует в нём; проверяем temporal timestamp snapshot.
        self.assertEqual(
            rows["source_worldbuilding_entity_count"]["source_timestamp"],
            "2025-01-25T00:00:00+00:00",
        )

        payload_v2 = self.materializer.materialize(
            plan,
            [self._target()],
            complexity_method="story-structure",
            complexity_method_version="2",
        )
        self.assertEqual(
            self._by_name(payload_v2)["source_worldbuilding_entity_count"]["value"],
            400.0,
        )

    def test_complexity_protocol_is_mandatory_and_never_auto_selected(self):
        plan = self._plan(
            [
                {
                    "feature_name": "source_worldbuilding_entity_count",
                    "source_layer": "source_context",
                    "temporal_contract": "known_at_lte_cutoff",
                    "proxy_role": "primary",
                    "coverage_feature": None,
                    "notes": None,
                }
            ]
        )
        with self.assertRaises(ProxyHypothesisValidationError):
            self.materializer.materialize(plan, [self._target()])

    def test_late_source_link_is_explicit_missing_before_cutoff(self):
        # Отдельный проект: source link становится известен после cutoff.
        self.store.upsert_project(
            {
                "project_id": "film-late",
                "title": "Late",
                "release_at": "2025-06-01T00:00:00Z",
                "adaptation_format": "film",
                "planned_runtime_minutes": 100,
                "format_known_at": "2025-01-01T00:00:00Z",
            }
        )
        self.store.link_source(
            {
                "project_id": "film-late",
                "work_id": "book-1",
                "relation_type": "based_on",
                "is_primary": True,
                "known_at": "2025-04-01T00:00:00Z",
                "source_id": "source-a",
            }
        )
        plan = self._plan(
            [
                {
                    "feature_name": "source_work_count",
                    "source_layer": "source_context",
                    "temporal_contract": "known_at_lte_cutoff",
                    "proxy_role": "primary",
                    "coverage_feature": None,
                    "notes": None,
                }
            ]
        )
        target = self._target()
        target["target_id"] = "tt-late"
        target["project_id"] = "film-late"
        payload = self.materializer.materialize(plan, [target])
        row = payload["observations"][0]
        self.assertFalse(row["available"])
        self.assertIsNone(row["value"])
        self.assertEqual(row["missing_reason"], "source_links_missing")
        audit = TemporalProxyAvailabilityAuditor.audit(
            plan,
            {"version": 1, "targets": payload["targets"], "observations": payload["observations"]},
        )
        self.assertTrue(audit["passed"])

    def test_format_known_after_cutoff_is_missing_not_zero(self):
        self.store.upsert_project(
            {
                "project_id": "film-format-late",
                "title": "Format Late",
                "release_at": "2025-06-01T00:00:00Z",
                "adaptation_format": "film",
                "planned_runtime_minutes": 999,
                "format_known_at": "2025-04-01T00:00:00Z",
            }
        )
        plan = self._plan(
            [
                {
                    "feature_name": "planned_runtime_minutes",
                    "source_layer": "source_context",
                    "temporal_contract": "planned_before_release",
                    "proxy_role": "primary",
                    "coverage_feature": None,
                    "notes": None,
                }
            ]
        )
        target = self._target()
        target["target_id"] = "tt-format-late"
        target["project_id"] = "film-format-late"
        row = self.materializer.materialize(plan, [target])["observations"][0]
        self.assertFalse(row["available"])
        self.assertIsNone(row["value"])
        self.assertEqual(row["missing_reason"], "format_not_known_by_cutoff")

    def test_multiple_visible_works_aggregate_deterministically(self):
        plan = self._plan(
            [
                {
                    "feature_name": "source_work_count",
                    "source_layer": "source_context",
                    "temporal_contract": "known_at_lte_cutoff",
                    "proxy_role": "primary",
                    "coverage_feature": None,
                    "notes": None,
                },
                {
                    "feature_name": "source_series_size_mean",
                    "source_layer": "source_context",
                    "temporal_contract": "known_at_lte_cutoff",
                    "proxy_role": "context",
                    "coverage_feature": None,
                    "notes": None,
                },
            ]
        )
        target = self._target(cutoff="2025-05-01T00:00:00Z")
        first = self.materializer.materialize(plan, [target])
        second = self.materializer.materialize(plan, [target])
        rows = self._by_name(first)
        self.assertEqual(rows["source_work_count"]["value"], 2.0)
        self.assertEqual(rows["source_series_size_mean"]["value"], 3.0)
        self.assertEqual(first["materializer_fingerprint_sha256"], second["materializer_fingerprint_sha256"])

    def test_unknown_project_emits_explicit_missing_for_every_feature(self):
        plan = self._plan(
            [
                {
                    "feature_name": "planned_runtime_minutes",
                    "source_layer": "source_context",
                    "temporal_contract": "planned_before_release",
                    "proxy_role": "primary",
                    "coverage_feature": None,
                    "notes": None,
                },
                {
                    "feature_name": "source_work_count",
                    "source_layer": "source_context",
                    "temporal_contract": "known_at_lte_cutoff",
                    "proxy_role": "context",
                    "coverage_feature": None,
                    "notes": None,
                },
            ]
        )
        target = self._target()
        target["target_id"] = "tt-unknown"
        target["project_id"] = "project-does-not-exist"
        payload = self.materializer.materialize(plan, [target])
        self.assertEqual(len(payload["observations"]), 2)
        self.assertTrue(all(not row["available"] for row in payload["observations"]))
        self.assertTrue(all(row["missing_reason"] == "source_project_not_found" for row in payload["observations"]))

    def test_mixed_source_plan_is_rejected_until_composer_exists(self):
        plan = self._plan(
            [
                {
                    "feature_name": "source_work_count",
                    "source_layer": "source_context",
                    "temporal_contract": "known_at_lte_cutoff",
                    "proxy_role": "primary",
                    "coverage_feature": None,
                    "notes": None,
                },
                {
                    "feature_name": "writer_prior_count",
                    "source_layer": "imdb_history",
                    "temporal_contract": "history_before_target_year",
                    "proxy_role": "context",
                    "coverage_feature": None,
                    "notes": None,
                },
            ]
        )
        with self.assertRaises(ProxyHypothesisValidationError):
            self.materializer.materialize(plan, [self._target()])

    def test_published_contract_is_rejected_by_this_adapter(self):
        plan = self._plan(
            [
                {
                    "feature_name": "source_work_count",
                    "source_layer": "source_context",
                    "temporal_contract": "published_at_lte_cutoff",
                    "proxy_role": "primary",
                    "coverage_feature": None,
                    "notes": None,
                }
            ]
        )
        with self.assertRaises(ProxyHypothesisValidationError):
            self.materializer.materialize(plan, [self._target()])

    def test_unsupported_feature_fails_closed(self):
        plan = self._plan(
            [
                {
                    "feature_name": "source_magic_quality_score",
                    "source_layer": "source_context",
                    "temporal_contract": "known_at_lte_cutoff",
                    "proxy_role": "primary",
                    "coverage_feature": None,
                    "notes": None,
                }
            ]
        )
        with self.assertRaises(ProxyHypothesisValidationError):
            self.materializer.materialize(plan, [self._target()])


if __name__ == "__main__":
    unittest.main()
