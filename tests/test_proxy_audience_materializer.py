from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from src.audience_signals import AudienceSignalStore
from src.proxy_ablation import TemporalProxyAvailabilityAuditor
from src.proxy_hypotheses import ProxyHypothesisValidationError
from src.proxy_unified_materializer import (
    ProxyUnifiedMaterializer,
    audience_feature_name,
)
from src.source_context import SourceContextStore


def _fp(payload):
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()


class ProxyAudienceMaterializerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.source_db = root / "source.duckdb"
        self.production_db = root / "production.duckdb"
        self.imdb_db = root / "imdb.duckdb"
        self.audience_db = root / "audience.duckdb"
        self.audience = AudienceSignalStore(self.audience_db)
        self.audience.upsert_source(
            {
                "source_id": "yt",
                "provider": "YT aggregate",
                "usage_basis": "public_aggregate",
                "retrieved_at": "2026-06-01T12:00:00Z",
            }
        )
        self.audience.upsert_project(
            {"project_id": "aud-1", "imdb_id": "tt0000001", "title": "Future"}
        )
        self.materializer = ProxyUnifiedMaterializer(
            source_db_path=self.source_db,
            production_db_path=self.production_db,
            imdb_db_path=self.imdb_db,
            audience_db_path=self.audience_db,
        )

    def tearDown(self):
        self.audience.close()
        self.tmp.cleanup()

    def _feature(self, field="value", *, version="1"):
        return audience_feature_name(
            "trailer_views", "youtube_public_stats", version, "count", field
        )

    def _plan(self, features):
        body = {
            "registry_version": 1,
            "hypothesis_id": "audience-demo",
            "title": "Audience demo",
            "retrospective_signal": {
                "dimension": "tone",
                "change_type": "rewritten",
                "rationale": "test",
                "evidence": [],
            },
            "candidate_pre_release_features": features,
            "ablation": {
                "baseline_schema": "v15",
                "candidate_label": "audience-v1",
                "holdout_policy": "stable_temporal_last_two_years",
                "primary_metric": "mae",
                "max_mae_regression": 0.0,
                "require_improvement": True,
                "dataset_fingerprint_required": True,
                "preregistered_at": "2026-10-04T00:00:00+00:00",
            },
            "status": "ablation_ready",
            "post_release_features_allowed": False,
            "expert_interpretation_as_feature": False,
            "automatic_catboost_inclusion": False,
        }
        result = dict(body)
        result["plan_fingerprint_sha256"] = _fp(body)
        return result

    def _spec(self, name):
        return {
            "feature_name": name,
            "source_layer": "pre_release_public_signal",
            "temporal_contract": "known_at_lte_cutoff",
            "proxy_role": "primary",
            "coverage_feature": None,
            "notes": None,
        }

    def _target(self):
        return {
            "target_id": "tt0000001",
            "target_year": 2026,
            "cutoff_at": "2026-06-10T00:00:00Z",
            "release_at": "2026-08-01T00:00:00Z",
        }

    def _observe(self):
        self.audience.add_observation(
            {
                "observation_id": "views-1",
                "project_id": "aud-1",
                "signal_type": "trailer_views",
                "value": 123456,
                "unit": "count",
                "observed_at": "2026-06-05T10:00:00Z",
                "known_at": "2026-06-05T12:00:00Z",
                "planned_release_at": "2026-08-01T00:00:00Z",
                "method": "youtube_public_stats",
                "method_version": "1",
                "source_id": "yt",
                "sample_size": 1,
                "coverage_fraction": 1.0,
            }
        )

    def test_value_and_known_materialize_and_pass_temporal_audit(self):
        self._observe()
        plan = self._plan([self._spec(self._feature()), self._spec(self._feature("known"))])
        payload = self.materializer.materialize(plan, [self._target()])
        rows = {row["feature_name"]: row for row in payload["observations"]}
        self.assertEqual(rows[self._feature()]["value"], 123456.0)
        self.assertEqual(rows[self._feature("known")]["value"], 1.0)
        self.assertIn("views-1", rows[self._feature()]["provenance_id"])
        audit = TemporalProxyAvailabilityAuditor.audit(plan, payload)
        self.assertTrue(audit["passed"])
        self.assertEqual(audit["violation_count"], 0)

    def test_missing_protocol_is_missing_but_known_feature_is_zero(self):
        plan = self._plan(
            [
                self._spec(self._feature(version="2")),
                self._spec(self._feature("known", version="2")),
            ]
        )
        payload = self.materializer.materialize(plan, [self._target()])
        rows = {row["feature_name"]: row for row in payload["observations"]}
        self.assertFalse(rows[self._feature(version="2")]["available"])
        self.assertIsNone(rows[self._feature(version="2")]["value"])
        self.assertTrue(rows[self._feature("known", version="2")]["available"])
        self.assertEqual(rows[self._feature("known", version="2")]["value"], 0.0)
        self.assertTrue(TemporalProxyAvailabilityAuditor.audit(plan, payload)["passed"])

    def test_coverage_and_sample_size_require_metadata(self):
        self._observe()
        plan = self._plan(
            [
                self._spec(self._feature("coverage")),
                self._spec(self._feature("sample_size")),
                self._spec(self._feature("age_days")),
            ]
        )
        payload = self.materializer.materialize(plan, [self._target()])
        rows = {row["feature_name"]: row for row in payload["observations"]}
        self.assertEqual(rows[self._feature("coverage")]["value"], 1.0)
        self.assertEqual(rows[self._feature("sample_size")]["value"], 1.0)
        self.assertGreater(rows[self._feature("age_days")]["value"], 4.0)

    def test_canonical_name_is_preregistered_protocol_contract(self):
        name = self._feature()
        self.assertEqual(
            name,
            "audience__trailer_views__youtube_public_stats__1__count__value",
        )
        with self.assertRaises(ProxyHypothesisValidationError):
            audience_feature_name("trailer_views", "YouTube Stats", "1", "count")

    def test_published_at_contract_is_not_silently_mapped_to_known_at(self):
        self._observe()
        spec = self._spec(self._feature())
        spec["temporal_contract"] = "published_at_lte_cutoff"
        plan = self._plan([spec])
        with self.assertRaises(ProxyHypothesisValidationError):
            self.materializer.materialize(plan, [self._target()])

    def test_mixed_source_and_audience_plan_uses_one_payload(self):
        source = SourceContextStore(self.source_db)
        try:
            source.upsert_source(
                {
                    "source_id": "s1",
                    "url": "https://example.test/book",
                    "retrieved_at": "2026-01-01T00:00:00Z",
                }
            )
            source.upsert_work(
                {"work_id": "w1", "title": "Book", "source_type": "novel"}
            )
            source.upsert_project(
                {
                    "project_id": "src-1",
                    "imdb_id": "tt0000001",
                    "title": "Future",
                    "adaptation_format": "film",
                    "format_known_at": "2026-01-01T00:00:00Z",
                }
            )
            source.link_source(
                {
                    "project_id": "src-1",
                    "work_id": "w1",
                    "relation_type": "based_on",
                    "is_primary": True,
                    "known_at": "2026-01-02T00:00:00Z",
                    "source_id": "s1",
                }
            )
        finally:
            source.close()
        self._observe()
        source_spec = {
            "feature_name": "source_work_count",
            "source_layer": "source_context",
            "temporal_contract": "known_at_lte_cutoff",
            "proxy_role": "context",
            "coverage_feature": None,
            "notes": None,
        }
        plan = self._plan([source_spec, self._spec(self._feature())])
        payload = self.materializer.materialize(plan, [self._target()])
        rows = {row["feature_name"]: row for row in payload["observations"]}
        self.assertEqual(rows["source_work_count"]["value"], 1.0)
        self.assertEqual(rows[self._feature()]["value"], 123456.0)
        self.assertTrue(TemporalProxyAvailabilityAuditor.audit(plan, payload)["passed"])


if __name__ == "__main__":
    unittest.main()
