from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import duckdb

from src.future_prediction_payload import FuturePredictionPayloadBuilder
from src.future_releases import FutureReleaseStore
from src.production_context import ProductionContextStore
from src.source_context import SourceContextStore


class FuturePredictionPayloadTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.future_db = root / "future.duckdb"
        self.imdb_db = root / "imdb.duckdb"
        self.source_db = root / "source.duckdb"
        self.production_db = root / "production.duckdb"
        self._seed_imdb()
        self._seed_future()
        self._seed_source()
        self._seed_production()
        self.builder = FuturePredictionPayloadBuilder(
            future_db_path=self.future_db,
            imdb_db_path=self.imdb_db,
            source_db_path=self.source_db,
            production_db_path=self.production_db,
        )

    def tearDown(self):
        self.tmp.cleanup()

    def _seed_imdb(self):
        conn = duckdb.connect(str(self.imdb_db))
        try:
            conn.execute(
                "CREATE TABLE title_basics(tconst VARCHAR, runtimeMinutes INTEGER, genres VARCHAR)"
            )
            conn.execute(
                "INSERT INTO title_basics VALUES ('tt1234567', 119, 'Drama,Sci-Fi')"
            )
        finally:
            conn.close()

    def _seed_future(self):
        with FutureReleaseStore(self.future_db) as store:
            store.upsert_source(
                {
                    "source_id": "official",
                    "provider": "Studio",
                    "usage_basis": "public_record",
                    "retrieved_at": "2026-01-01T00:00:00Z",
                }
            )
            store.upsert_source(
                {
                    "source_id": "press",
                    "provider": "Press",
                    "usage_basis": "manual_reference",
                    "retrieved_at": "2026-01-02T00:00:00Z",
                }
            )
            store.upsert_project(
                {
                    "project_id": "film-a",
                    "imdb_id": "tt1234567",
                    "canonical_title": "Future Film",
                }
            )
            store.add_release_window(
                {
                    "observation_id": "release-a",
                    "project_id": "film-a",
                    "release_start_at": "2027-07-01T00:00:00Z",
                    "precision": "exact",
                    "known_at": "2026-01-10T00:00:00Z",
                    "source_id": "official",
                }
            )
            for person_id, imdb_id, name, role, order in (
                ("director-a", "nm1000001", "Director A", "director", None),
                ("writer-a", "nm1000002", "Writer A", "writer", None),
                ("actor-a", "nm1000003", "Actor A", "actor", 1),
                ("actor-b", "nm1000004", "Actor B", "actor", 2),
            ):
                store.upsert_person(
                    {
                        "person_id": person_id,
                        "imdb_id": imdb_id,
                        "canonical_name": name,
                    }
                )
                store.link_person(
                    {
                        "link_id": f"link-{person_id}",
                        "project_id": "film-a",
                        "person_id": person_id,
                        "role": role,
                        "billing_order": order,
                        "known_at": "2026-01-15T00:00:00Z",
                        "source_id": "official",
                    }
                )

    def _seed_source(self):
        store = SourceContextStore(self.source_db)
        try:
            store.upsert_source(
                {
                    "source_id": "source-official",
                    "url": "https://example.test/book",
                    "published_at": "2026-01-01T00:00:00Z",
                    "retrieved_at": "2026-01-01T00:00:00Z",
                    "confidence": 1.0,
                }
            )
            store.upsert_work(
                {
                    "work_id": "book-a",
                    "title": "Book A",
                    "source_type": "novel",
                    "first_publication_at": "2015-01-01T00:00:00Z",
                    "series_id": "series-a",
                    "series_position": 1,
                    "series_size": 3,
                }
            )
            store.upsert_project(
                {
                    "project_id": "source-film-a",
                    "imdb_id": "tt1234567",
                    "title": "Future Film",
                    "release_at": "2027-07-01T00:00:00Z",
                    "adaptation_format": "film",
                    "planned_runtime_minutes": 125,
                    "format_known_at": "2026-01-20T00:00:00Z",
                }
            )
            store.link_source(
                {
                    "link_id": "source-link-a",
                    "project_id": "source-film-a",
                    "work_id": "book-a",
                    "relation_type": "adaptation_of",
                    "is_primary": True,
                    "known_at": "2026-01-20T00:00:00Z",
                    "source_id": "source-official",
                }
            )
        finally:
            store.close()

    def _seed_production(self):
        store = ProductionContextStore(self.production_db)
        try:
            store.upsert_project(
                {
                    "project_id": "production-film-a",
                    "imdb_id": "tt1234567",
                    "title": "Future Film",
                    "release_at": "2027-07-01T00:00:00Z",
                    "franchise_id": "franchise-a",
                    "installment_index": 2,
                    "identity_known_at": "2026-01-12T00:00:00Z",
                }
            )
        finally:
            store.close()

    def test_builds_predict_contract_from_dated_context_and_override(self):
        result = self.builder.build(
            "film-a",
            "2026-02-01T00:00:00Z",
            genres_override=["Drama", "Sci-Fi"],
        )
        self.assertEqual(result["version"], 2)
        self.assertTrue(result["prediction_ready"])
        self.assertEqual(result["blockers"], [])
        request = result["request"]
        self.assertEqual(request["title"], "Future Film")
        self.assertEqual(request["imdb_id"], "tt1234567")
        self.assertEqual(request["director"], "Director A")
        self.assertEqual(request["directors"], ["Director A"])
        self.assertEqual(request["writer"], "Writer A")
        self.assertEqual(request["actors"], ["Actor A", "Actor B"])
        self.assertEqual(request["year"], 2027)
        self.assertEqual(request["runtime"], 125)
        self.assertEqual(request["genres"], ["Drama", "Sci-Fi"])
        self.assertEqual(request["source"]["title"], "Book A")
        self.assertEqual(request["source"]["type"], "novel")
        self.assertEqual(request["source"]["series_size"], 3)
        self.assertEqual(result["input_sources"]["runtime"], "source_context")
        self.assertEqual(result["input_sources"]["genres"], "override")
        self.assertFalse(result["input_sources"]["current_imdb_snapshot_allowed"])
        self.assertFalse(result["input_sources"]["imdb_local_available"])
        self.assertTrue(result["source_context"]["available"])
        self.assertTrue(result["production_context"]["available"])
        self.assertEqual(
            result["production_context"]["features"]["production_franchise_known"],
            1.0,
        )
        self.assertTrue(result["temporal_contract"]["historical_backtest_safe"])
        self.assertFalse(result["network_required"])

    def test_current_imdb_snapshot_is_disabled_by_default(self):
        result = self.builder.build("film-a", "2026-02-01T00:00:00Z")
        self.assertFalse(result["prediction_ready"])
        self.assertIn("genres_missing", result["blockers"])
        self.assertNotIn("runtime_missing", result["blockers"])
        self.assertIsNone(result["request"])
        self.assertEqual(result["input_sources"]["runtime"], "source_context")
        self.assertIsNone(result["input_sources"]["genres"])
        self.assertFalse(result["input_sources"]["current_imdb_snapshot_allowed"])
        self.assertEqual(
            result["imdb_current_snapshot"]["disabled_reason"],
            "undated_current_snapshot_requires_explicit_opt_in",
        )
        self.assertTrue(result["temporal_contract"]["historical_backtest_safe"])
        self.assertFalse(result["temporal_contract"]["current_imdb_snapshot_used"])

    def test_current_imdb_snapshot_opt_in_is_explicitly_not_backtest_safe(self):
        result = self.builder.build(
            "film-a",
            "2026-02-01T00:00:00Z",
            allow_current_imdb_snapshot=True,
        )
        self.assertTrue(result["prediction_ready"])
        self.assertEqual(result["request"]["runtime"], 125)
        self.assertEqual(result["request"]["genres"], ["Drama", "Sci-Fi"])
        self.assertEqual(result["input_sources"]["runtime"], "source_context")
        self.assertEqual(
            result["input_sources"]["genres"],
            "imdb_current_snapshot",
        )
        self.assertTrue(result["input_sources"]["imdb_local_available"])
        self.assertIn(
            "current_imdb_snapshot_not_point_in_time",
            result["warnings"],
        )
        self.assertTrue(result["temporal_contract"]["current_imdb_snapshot_used"])
        self.assertFalse(result["temporal_contract"]["historical_backtest_safe"])

    def test_current_imdb_snapshot_can_supply_runtime_only_with_opt_in(self):
        # До 20 января planned runtime Source Context ещё не был известен.
        safe = self.builder.build(
            "film-a",
            "2026-01-18T00:00:00Z",
            genres_override=["Drama"],
        )
        self.assertFalse(safe["prediction_ready"])
        self.assertIn("runtime_missing", safe["blockers"])

        interactive = self.builder.build(
            "film-a",
            "2026-01-18T00:00:00Z",
            allow_current_imdb_snapshot=True,
        )
        self.assertTrue(interactive["prediction_ready"])
        self.assertEqual(interactive["request"]["runtime"], 119)
        self.assertEqual(interactive["request"]["genres"], ["Drama", "Sci-Fi"])
        self.assertEqual(
            interactive["input_sources"]["runtime"],
            "imdb_current_snapshot",
        )
        self.assertFalse(
            interactive["temporal_contract"]["historical_backtest_safe"]
        )

    def test_explicit_runtime_and_genres_override_local_context(self):
        result = self.builder.build(
            "film-a",
            "2026-02-01T00:00:00Z",
            runtime_override=140,
            genres_override=["Adventure", "Drama"],
            synopsis="Короткий синопсис.",
        )
        self.assertTrue(result["prediction_ready"])
        self.assertEqual(result["request"]["runtime"], 140)
        self.assertEqual(result["request"]["genres"], ["Adventure", "Drama"])
        self.assertEqual(result["request"]["synopsis"], "Короткий синопсис.")
        self.assertEqual(result["input_sources"]["runtime"], "override")
        self.assertEqual(result["input_sources"]["genres"], "override")
        self.assertTrue(result["temporal_contract"]["historical_backtest_safe"])

    def test_release_conflict_blocks_prediction_instead_of_choosing_source(self):
        with FutureReleaseStore(self.future_db) as store:
            store.add_release_window(
                {
                    "observation_id": "release-press",
                    "project_id": "film-a",
                    "release_start_at": "2027-08-01T00:00:00Z",
                    "precision": "exact",
                    "known_at": "2026-01-25T00:00:00Z",
                    "source_id": "press",
                    "confidence": 0.7,
                }
            )
        result = self.builder.build("film-a", "2026-02-01T00:00:00Z")
        self.assertFalse(result["prediction_ready"])
        self.assertIn("release_date_conflict", result["blockers"])
        self.assertIn("exact_release_date_missing", result["blockers"])
        self.assertIsNone(result["request"])

    def test_non_exact_release_window_blocks_year_instead_of_inventing_date(self):
        with FutureReleaseStore(self.future_db) as store:
            store.upsert_project(
                {"project_id": "film-window", "canonical_title": "Window Film"}
            )
            store.add_release_window(
                {
                    "observation_id": "window-release",
                    "project_id": "film-window",
                    "release_start_at": "2028-01-01T00:00:00Z",
                    "release_end_at": "2028-12-31T23:59:59Z",
                    "precision": "year",
                    "known_at": "2026-01-01T00:00:00Z",
                    "source_id": "official",
                }
            )
        result = self.builder.build(
            "film-window",
            "2026-02-01T00:00:00Z",
            runtime_override=120,
            genres_override=["Drama"],
        )
        self.assertFalse(result["prediction_ready"])
        self.assertIn("exact_release_date_missing", result["blockers"])
        self.assertIn("director_missing", result["blockers"])
        self.assertIsNone(result["request"])

    def test_cutoff_at_or_after_release_is_blocked(self):
        result = self.builder.build(
            "film-a",
            "2027-07-01T00:00:00Z",
            runtime_override=120,
            genres_override=["Drama"],
        )
        self.assertFalse(result["prediction_ready"])
        self.assertIn("cutoff_must_be_pre_release", result["blockers"])


if __name__ == "__main__":
    unittest.main()
