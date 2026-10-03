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
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class ProxySourceContextMaterializerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = SourceContextStore(Path(self.tmp.name) / "source.duckdb")
        self.complexity = SourceComplexityContext(self.store)
        self.materializer = ProxySourceContextMaterializer(self.store)
        self._seed()

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def _seed(self):
        for source_id in ("src-a", "src-b"):
            self.store.upsert_source(
                {
                    "source_id": source_id,
                    "url": f"https://example.test/{source_id}",
                    "retrieved_at": "2025-01-01T00:00:00Z",
                }
            )
        for work_id, year, position in (("book-1", 2000, 1), ("book-2", 2002, 2)):
            self.store.upsert_work(
                {
                    "work_id": work_id,
                    "title": work_id,
                    "source_type": "novel_series",
                    "first_publication_at": f"{year}-01-01T00:00:00Z",
                    "series_id": "series-x",
                    "series_position": position,
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
                "source_id": "src-a",
            }
        )
        # Та же logical связь вторым provenance не должна менять count.
        self.store.link_source(
            {
                "link_id": "link-1-dup",
                "project_id": "film-1",
                "work_id": "book-1",
                "relation_type": "based_on",
                "is_primary": True,
                "known_at": "2025-01-20T00:00:00Z",
                "source_id": "src-b",
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
                "source_id": "src-a",
            }
        )
        for version, count in (("1", 40), ("2", 400)):
            self.complexity.add_snapshot(
                {
                    "snapshot_id": f"snap-v{version}",
                    "work_id": "book-1",
                    "method": "story-structure",
                    "method_version": version,
                    "measured_at": f"2025-01-2{4 + int(version)}T00:00:00Z",
                    "known_at": f"2025-01-2{4 + int(version)}T00:00:00Z",
                    "source_id": "src-a",
                    "coverage_fraction": 0.9,
                    "metrics": {
                        "source_length_words": 100000,
                        "worldbuilding_entity_count": count,
                    },
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
        result = dict(body)
        result["plan_fingerprint_sha256"] = _fingerprint(body)
        return result

    @staticmethod
    def _spec(name, contract="known_at_lte_cutoff", role="primary", layer="source_context"):
        return {
            "feature_name": name,
            "source_layer": layer,
            "temporal_contract": contract,
            "proxy_role": role,
            "coverage_feature": None,
            "notes": None,
        }

    @staticmethod
    def _target(project_id="film-1", cutoff="2025-03-01T00:00:00Z"):
        return {
            "target_id": f"target-{project_id}",
            "project_id": project_id,
            "target_year": 2025,
            "cutoff_at": cutoff,
            "release_at": "2025-06-01T00:00:00Z",
        }

    @staticmethod
    def _rows(payload):
        return {row["feature_name"]: row for row in payload["observations"]}

    def test_output_passes_canonical_temporal_auditor(self):
        plan = self._plan(
            [
                self._spec("planned_runtime_minutes", "planned_before_release", "context"),
                self._spec("source_work_count"),
            ]
        )
        payload = self.materializer.materialize(plan, [self._target()])
        rows = self._rows(payload)
        self.assertEqual(rows["planned_runtime_minutes"]["value"], 120.0)
        self.assertEqual(rows["source_work_count"]["value"], 1.0)
        self.assertTrue(rows["source_work_count"]["provenance_id"])
        audit = TemporalProxyAvailabilityAuditor.audit(
            plan,
            {"version": 1, "targets": payload["targets"], "observations": payload["observations"]},
        )
        self.assertTrue(audit["passed"])

    def test_complexity_protocol_is_explicit_and_exact(self):
        plan = self._plan(
            [
                self._spec("source_worldbuilding_entity_count"),
                self._spec("source_complexity_coverage", role="coverage"),
            ]
        )
        with self.assertRaises(ProxyHypothesisValidationError):
            self.materializer.materialize(plan, [self._target()])

        v1 = self.materializer.materialize(
            plan,
            [self._target()],
            complexity_method="story-structure",
            complexity_method_version="1",
        )
        v2 = self.materializer.materialize(
            plan,
            [self._target()],
            complexity_method="story-structure",
            complexity_method_version="2",
        )
        row_v1 = self._rows(v1)["source_worldbuilding_entity_count"]
        row_v2 = self._rows(v2)["source_worldbuilding_entity_count"]
        self.assertEqual(row_v1["value"], 40.0)
        self.assertEqual(row_v2["value"], 400.0)
        self.assertEqual(row_v1["source_timestamp"], "2025-01-25T00:00:00+00:00")
        self.assertNotEqual(row_v1["provenance_id"], row_v2["provenance_id"])

    def test_late_facts_are_missing_not_fake_zero(self):
        self.store.upsert_project(
            {
                "project_id": "late",
                "title": "Late",
                "release_at": "2025-06-01T00:00:00Z",
                "adaptation_format": "film",
                "planned_runtime_minutes": 999,
                "format_known_at": "2025-04-01T00:00:00Z",
            }
        )
        self.store.link_source(
            {
                "project_id": "late",
                "work_id": "book-1",
                "relation_type": "based_on",
                "is_primary": True,
                "known_at": "2025-04-01T00:00:00Z",
                "source_id": "src-a",
            }
        )
        plan = self._plan(
            [
                self._spec("planned_runtime_minutes", "planned_before_release"),
                self._spec("source_work_count"),
            ]
        )
        payload = self.materializer.materialize(plan, [self._target("late")])
        rows = self._rows(payload)
        self.assertFalse(rows["planned_runtime_minutes"]["available"])
        self.assertIsNone(rows["planned_runtime_minutes"]["value"])
        self.assertEqual(rows["planned_runtime_minutes"]["missing_reason"], "format_not_known_by_cutoff")
        self.assertFalse(rows["source_work_count"]["available"])
        self.assertEqual(rows["source_work_count"]["missing_reason"], "source_links_missing")

    def test_multiple_works_and_duplicate_provenance_are_deterministic(self):
        plan = self._plan([self._spec("source_work_count"), self._spec("source_series_size_mean")])
        target = self._target(cutoff="2025-05-01T00:00:00Z")
        first = self.materializer.materialize(plan, [target])
        second = self.materializer.materialize(plan, [target])
        rows = self._rows(first)
        self.assertEqual(rows["source_work_count"]["value"], 2.0)
        self.assertEqual(rows["source_series_size_mean"]["value"], 3.0)
        self.assertEqual(first["materializer_fingerprint_sha256"], second["materializer_fingerprint_sha256"])

    def test_unknown_project_emits_explicit_missing_rows(self):
        plan = self._plan(
            [self._spec("source_work_count"), self._spec("planned_runtime_minutes", "planned_before_release")]
        )
        payload = self.materializer.materialize(plan, [self._target("missing-project")])
        self.assertEqual(len(payload["observations"]), 2)
        self.assertTrue(all(not row["available"] for row in payload["observations"]))
        self.assertTrue(all(row["missing_reason"] == "source_project_not_found" for row in payload["observations"]))

    def test_adapter_fails_closed_on_mixed_source_or_unsupported_feature(self):
        mixed = self._plan(
            [self._spec("source_work_count"), self._spec("writer_prior_count", "history_before_target_year", layer="imdb_history")]
        )
        with self.assertRaises(ProxyHypothesisValidationError):
            self.materializer.materialize(mixed, [self._target()])

        unsupported = self._plan([self._spec("source_magic_quality_score")])
        with self.assertRaises(ProxyHypothesisValidationError):
            self.materializer.materialize(unsupported, [self._target()])

    def test_published_at_contract_is_not_faked_with_known_at(self):
        plan = self._plan([self._spec("source_work_count", "published_at_lte_cutoff")])
        with self.assertRaises(ProxyHypothesisValidationError):
            self.materializer.materialize(plan, [self._target()])


if __name__ == "__main__":
    unittest.main()
