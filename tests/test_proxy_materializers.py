from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

import duckdb

from src.production_context import ProductionContextStore
from src.proxy_ablation import TemporalProxyAvailabilityAuditor
from src.proxy_materializers import ProxySourceMaterializer
from src.source_context import SourceContextStore


def _fingerprint(payload: dict) -> str:
    return hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _plan(features: list[dict]) -> dict:
    plan = {
        "registry_version": 1,
        "hypothesis_id": "materializer-test",
        "title": "Materializer test",
        "retrospective_signal": {
            "dimension": "plot",
            "change_type": "removed",
            "rationale": "test",
            "evidence": [
                {
                    "evidence_kind": "storydiff",
                    "reference_id": "storydiff:test",
                    "confidence": 0.9,
                }
            ],
        },
        "candidate_pre_release_features": features,
        "ablation": {
            "baseline_schema": "v15",
            "candidate_label": "candidate-test",
            "holdout_policy": "stable_temporal_last_two_years",
            "primary_metric": "mae",
            "max_mae_regression": 0.0,
            "require_improvement": True,
            "dataset_fingerprint_required": True,
            "preregistered_at": "2026-01-01T00:00:00+00:00",
        },
        "status": "ablation_ready",
        "post_release_features_allowed": False,
        "expert_interpretation_as_feature": False,
        "automatic_catboost_inclusion": False,
    }
    plan["plan_fingerprint_sha256"] = _fingerprint(plan)
    return plan


class ProxyMaterializerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.source_db = root / "source.duckdb"
        self.production_db = root / "production.duckdb"
        self.imdb_db = root / "imdb.duckdb"
        self._build_imdb()
        self._build_source()
        self._build_production()
        self.materializer = ProxySourceMaterializer(
            source_db_path=self.source_db,
            production_db_path=self.production_db,
            imdb_db_path=self.imdb_db,
        )
        self.target = {
            "target_id": "tt9000001",
            "target_year": 2025,
            "cutoff_at": "2025-03-01T00:00:00+00:00",
            "release_at": "2025-08-01T00:00:00+00:00",
        }

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _build_imdb(self) -> None:
        conn = duckdb.connect(str(self.imdb_db))
        conn.execute(
            """
            CREATE TABLE title_basics(
                tconst VARCHAR, titleType VARCHAR, primaryTitle VARCHAR,
                startYear VARCHAR, runtimeMinutes VARCHAR, genres VARCHAR
            )
            """
        )
        conn.execute("CREATE TABLE title_ratings(tconst VARCHAR, averageRating DOUBLE)")
        conn.execute(
            "CREATE TABLE title_principals(tconst VARCHAR, ordering INTEGER, nconst VARCHAR, category VARCHAR)"
        )
        conn.execute("CREATE TABLE title_writers(tconst VARCHAR, nconst VARCHAR)")
        conn.executemany(
            "INSERT INTO title_basics VALUES (?, 'movie', ?, ?, '100', 'Drama')",
            [
                ("tt8000001", "Past", "2024"),
                ("tt8000002", "Same Year", "2025"),
                ("tt9000001", "Target", "2025"),
                ("tt9999999", "Future", "2026"),
            ],
        )
        conn.executemany(
            "INSERT INTO title_ratings VALUES (?, ?)",
            [
                ("tt8000001", 8.0),
                ("tt8000002", 9.9),
                ("tt9000001", 7.0),
                ("tt9999999", 10.0),
            ],
        )
        conn.executemany(
            "INSERT INTO title_writers VALUES (?, 'nm-writer')",
            [
                ("tt8000001",),
                ("tt8000002",),
                ("tt9000001",),
                ("tt9999999",),
            ],
        )
        conn.execute(
            "INSERT INTO title_principals VALUES ('tt9000001', 1, 'nm-director', 'director')"
        )
        conn.close()

    def _build_source(self) -> None:
        store = SourceContextStore(self.source_db)
        try:
            store.upsert_source(
                {
                    "source_id": "src-book",
                    "url": "https://example.com/book",
                    "published_at": "2024-01-01T00:00:00+00:00",
                    "retrieved_at": "2025-01-05T00:00:00+00:00",
                }
            )
            store.upsert_work(
                {
                    "work_id": "book-1",
                    "title": "Book",
                    "source_type": "novel",
                    "first_publication_at": "2000-01-01T00:00:00+00:00",
                    "series_size": 3,
                    "series_position": 1,
                }
            )
            store.upsert_project(
                {
                    "project_id": "source-target",
                    "imdb_id": "tt9000001",
                    "title": "Target",
                    "release_at": "2025-08-01T00:00:00+00:00",
                    "adaptation_format": "film",
                    "planned_runtime_minutes": 120,
                    "format_known_at": "2025-01-15T00:00:00+00:00",
                }
            )
            store.link_source(
                {
                    "link_id": "link-book",
                    "project_id": "source-target",
                    "work_id": "book-1",
                    "relation_type": "based_on",
                    "is_primary": True,
                    "known_at": "2025-01-20T00:00:00+00:00",
                    "source_id": "src-book",
                }
            )
        finally:
            store.close()

    def _build_production(self) -> None:
        store = ProductionContextStore(self.production_db)
        try:
            store.upsert_source(
                {
                    "source_id": "prod-source",
                    "url": "https://example.com/prod",
                    "published_at": "2025-02-01T00:00:00+00:00",
                    "retrieved_at": "2025-02-01T00:00:00+00:00",
                }
            )
            store.upsert_project(
                {
                    "project_id": "prod-target",
                    "imdb_id": "tt9000001",
                    "title": "Target",
                    "release_at": "2025-08-01T00:00:00+00:00",
                    "identity_known_at": "2025-01-01T00:00:00+00:00",
                }
            )
            store.add_event(
                {
                    "event_id": "rewrite-1",
                    "project_id": "prod-target",
                    "event_type": "rewrite",
                    "event_at": "2025-01-20T00:00:00+00:00",
                    "known_at": "2025-02-10T00:00:00+00:00",
                    "stage": "writing",
                    "source_id": "prod-source",
                    "details": {},
                }
            )
            # После cutoff: materializer не должен увидеть второй rewrite.
            store.add_event(
                {
                    "event_id": "rewrite-late",
                    "project_id": "prod-target",
                    "event_type": "rewrite",
                    "known_at": "2025-04-10T00:00:00+00:00",
                    "stage": "production",
                    "source_id": "prod-source",
                    "details": {},
                }
            )
        finally:
            store.close()

    def test_materializes_three_layers_and_passes_temporal_audit(self):
        plan = _plan(
            [
                {
                    "feature_name": "source_planned_runtime_minutes",
                    "source_layer": "source_context",
                    "temporal_contract": "planned_before_release",
                    "proxy_role": "primary",
                    "coverage_feature": None,
                    "notes": None,
                },
                {
                    "feature_name": "production_rewrite_count",
                    "source_layer": "production_context",
                    "temporal_contract": "known_at_lte_cutoff",
                    "proxy_role": "context",
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

        materialization = self.materializer.materialize(plan, [self.target])
        rows = {item["feature_name"]: item for item in materialization["observations"]}

        self.assertEqual(rows["source_planned_runtime_minutes"]["value"], 120.0)
        self.assertEqual(rows["production_rewrite_count"]["value"], 1.0)
        self.assertEqual(rows["writer_prior_count"]["value"], 1.0)
        self.assertEqual(rows["writer_prior_count"]["history_year"], 2024)
        audit = TemporalProxyAvailabilityAuditor.audit(plan, materialization)
        self.assertTrue(audit["passed"], audit["violations"])

    def test_missing_source_link_is_explicit_not_zero(self):
        store = SourceContextStore(self.source_db)
        try:
            store.upsert_project(
                {
                    "project_id": "empty-source",
                    "imdb_id": "tt7777777",
                    "title": "Empty",
                    "release_at": "2025-08-01T00:00:00+00:00",
                    "adaptation_format": "film",
                    "format_known_at": "2025-01-01T00:00:00+00:00",
                }
            )
        finally:
            store.close()
        target = {
            "target_id": "tt7777777",
            "target_year": 2025,
            "cutoff_at": "2025-03-01T00:00:00+00:00",
            "release_at": "2025-08-01T00:00:00+00:00",
        }
        plan = _plan(
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

        row = self.materializer.materialize(plan, [target])["observations"][0]

        self.assertFalse(row["available"])
        self.assertIsNone(row["value"])
        self.assertIn("source link", row["missing_reason"])
        audit = TemporalProxyAvailabilityAuditor.audit(plan, self.materializer.materialize(plan, [target]))
        self.assertTrue(audit["passed"])

    def test_no_production_events_is_missing_not_fake_zero(self):
        store = ProductionContextStore(self.production_db)
        try:
            store.upsert_project(
                {
                    "project_id": "prod-empty",
                    "imdb_id": "tt7777778",
                    "title": "Empty prod",
                    "release_at": "2025-08-01T00:00:00+00:00",
                    "identity_known_at": "2025-01-01T00:00:00+00:00",
                }
            )
        finally:
            store.close()
        target = {
            "target_id": "tt7777778",
            "target_year": 2025,
            "cutoff_at": "2025-03-01T00:00:00+00:00",
            "release_at": "2025-08-01T00:00:00+00:00",
        }
        plan = _plan(
            [
                {
                    "feature_name": "production_change_count",
                    "source_layer": "production_context",
                    "temporal_contract": "known_at_lte_cutoff",
                    "proxy_role": "primary",
                    "coverage_feature": None,
                    "notes": None,
                }
            ]
        )

        row = self.materializer.materialize(plan, [target])["observations"][0]

        self.assertFalse(row["available"])
        self.assertIsNone(row["value"])
        self.assertIn("production events", row["missing_reason"])

    def test_imdb_history_excludes_same_year_and_future(self):
        plan = _plan(
            [
                {
                    "feature_name": "writer_avg_rating",
                    "source_layer": "imdb_history",
                    "temporal_contract": "history_before_target_year",
                    "proxy_role": "primary",
                    "coverage_feature": None,
                    "notes": None,
                }
            ]
        )

        row = self.materializer.materialize(plan, [self.target])["observations"][0]

        self.assertTrue(row["available"])
        self.assertEqual(row["history_year"], 2024)
        self.assertAlmostEqual(row["value"], 8.0)
        self.assertNotAlmostEqual(row["value"], 9.9)


if __name__ == "__main__":
    unittest.main()
