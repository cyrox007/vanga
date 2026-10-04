from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from src.future_releases import FutureReleaseStore
from src.future_temporal_facts import (
    FutureTemporalFactStore,
    TemporalFuturePredictionPayloadBuilder,
)


class FutureTemporalFactsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.db = root / "future.duckdb"
        self.imdb = root / "missing-imdb.duckdb"
        self.source = root / "missing-source.duckdb"
        self.production = root / "missing-production.duckdb"
        with FutureReleaseStore(self.db) as store:
            for source_id in ("source-a", "source-b"):
                store.upsert_source(
                    {
                        "source_id": source_id,
                        "provider": "Test",
                        "usage_basis": "public_record",
                        "retrieved_at": "2026-10-01T00:00:00Z",
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
                    "territory": "worldwide",
                    "release_start_at": "2027-05-20T00:00:00Z",
                    "release_end_at": "2027-05-20T00:00:00Z",
                    "precision": "exact",
                    "known_at": "2026-10-01T00:00:00Z",
                    "source_id": "source-a",
                    "confidence": 1.0,
                }
            )
            store.upsert_person(
                {
                    "person_id": "director-a",
                    "canonical_name": "Director A",
                }
            )
            store.link_person(
                {
                    "link_id": "director-link-a",
                    "project_id": "film-a",
                    "person_id": "director-a",
                    "role": "director",
                    "known_at": "2026-10-01T00:00:00Z",
                    "source_id": "source-a",
                    "confidence": 1.0,
                }
            )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _add(self, fact_type: str, value, *, source="source-a", known_at="2026-10-02T00:00:00Z", observation_id=None):
        with FutureTemporalFactStore(self.db) as store:
            return store.add_fact(
                {
                    "observation_id": observation_id,
                    "project_id": "film-a",
                    "fact_type": fact_type,
                    "value": value,
                    "known_at": known_at,
                    "source_id": source,
                    "confidence": 0.9,
                }
            )

    def test_fact_is_hidden_before_known_at_and_visible_after(self) -> None:
        self._add("runtime_minutes", 123)
        with FutureTemporalFactStore(self.db) as store:
            early = store.snapshot_as_of("film-a", "2026-10-01T12:00:00Z")
            late = store.snapshot_as_of("film-a", "2026-10-03T00:00:00Z")
        self.assertNotIn("runtime_minutes", early["facts"])
        self.assertEqual(late["facts"]["runtime_minutes"], 123)
        self.assertEqual(late["conflicts"], [])

    def test_conflicting_sources_do_not_resolve_silently(self) -> None:
        self._add("genres", ["Drama"], source="source-a", observation_id="genre-a")
        self._add("genres", ["Comedy"], source="source-b", observation_id="genre-b")
        with FutureTemporalFactStore(self.db) as store:
            snapshot = store.snapshot_as_of("film-a", "2026-10-03T00:00:00Z")
        self.assertIn("genres", snapshot["conflicts"])
        self.assertNotIn("genres", snapshot["facts"])
        self.assertEqual(len(snapshot["candidates"]["genres"]), 2)

    def test_latest_observation_per_source_replaces_older_value(self) -> None:
        self._add(
            "runtime_minutes",
            120,
            source="source-a",
            known_at="2026-10-02T00:00:00Z",
            observation_id="runtime-old",
        )
        self._add(
            "runtime_minutes",
            130,
            source="source-a",
            known_at="2026-10-04T00:00:00Z",
            observation_id="runtime-new",
        )
        with FutureTemporalFactStore(self.db) as store:
            mid = store.snapshot_as_of("film-a", "2026-10-03T00:00:00Z")
            late = store.snapshot_as_of("film-a", "2026-10-05T00:00:00Z")
        self.assertEqual(mid["facts"]["runtime_minutes"], 120)
        self.assertEqual(late["facts"]["runtime_minutes"], 130)

    def test_temporal_runtime_and_genres_make_payload_prediction_ready(self) -> None:
        self._add("runtime_minutes", 124, observation_id="runtime")
        self._add("genres", ["Drama", "Sci-Fi"], observation_id="genres")
        self._add("synopsis", "A dated pre-release synopsis.", observation_id="synopsis")
        builder = TemporalFuturePredictionPayloadBuilder(
            future_db_path=self.db,
            imdb_db_path=self.imdb,
            source_db_path=self.source,
            production_db_path=self.production,
        )
        result = builder.build("film-a", "2026-10-03T00:00:00Z")
        self.assertTrue(result["prediction_ready"])
        self.assertEqual(result["blockers"], [])
        self.assertEqual(result["request"]["runtime"], 124)
        self.assertEqual(result["request"]["genres"], ["Drama", "Sci-Fi"])
        self.assertEqual(result["request"]["synopsis"], "A dated pre-release synopsis.")
        self.assertEqual(result["input_sources"]["runtime"], "p9_temporal_fact")
        self.assertEqual(result["input_sources"]["genres"], "p9_temporal_fact")
        self.assertEqual(result["input_sources"]["synopsis"], "p9_temporal_fact")
        self.assertTrue(result["temporal_contract"]["historical_backtest_safe"])

    def test_genre_conflict_blocks_prediction(self) -> None:
        self._add("runtime_minutes", 124, observation_id="runtime")
        self._add("genres", ["Drama"], source="source-a", observation_id="genre-a")
        self._add("genres", ["Comedy"], source="source-b", observation_id="genre-b")
        builder = TemporalFuturePredictionPayloadBuilder(
            future_db_path=self.db,
            imdb_db_path=self.imdb,
            source_db_path=self.source,
            production_db_path=self.production,
        )
        result = builder.build("film-a", "2026-10-03T00:00:00Z")
        self.assertFalse(result["prediction_ready"])
        self.assertIn("genres_fact_conflict", result["blockers"])
        self.assertIsNone(result["request"])


if __name__ == "__main__":
    unittest.main()
