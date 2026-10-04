from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import duckdb

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

    def _add(
        self,
        fact_type: str,
        value,
        *,
        source="source-a",
        known_at="2026-10-02T00:00:00Z",
        observation_id=None,
        source_stream_id=None,
    ):
        with FutureTemporalFactStore(self.db) as store:
            payload = {
                "observation_id": observation_id,
                "project_id": "film-a",
                "fact_type": fact_type,
                "value": value,
                "known_at": known_at,
                "source_id": source,
                "confidence": 0.9,
            }
            if source_stream_id is not None:
                payload["source_stream_id"] = source_stream_id
            return store.add_fact(payload)

    def _builder(self) -> TemporalFuturePredictionPayloadBuilder:
        return TemporalFuturePredictionPayloadBuilder(
            future_db_path=self.db,
            imdb_db_path=self.imdb,
            source_db_path=self.source,
            production_db_path=self.production,
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

    def test_genre_order_does_not_create_false_conflict(self) -> None:
        self._add(
            "genres",
            ["Sci-Fi", "Drama"],
            source="source-a",
            observation_id="genre-a",
        )
        self._add(
            "genres",
            ["Drama", "Sci-Fi"],
            source="source-b",
            observation_id="genre-b",
        )
        with FutureTemporalFactStore(self.db) as store:
            snapshot = store.snapshot_as_of("film-a", "2026-10-03T00:00:00Z")
        self.assertEqual(snapshot["conflicts"], [])
        self.assertEqual(snapshot["facts"]["genres"], ["Drama", "Sci-Fi"])

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

    def test_different_snapshot_sources_in_same_stream_replace_each_other(self) -> None:
        with FutureReleaseStore(self.db) as store:
            store.upsert_source(
                {
                    "source_id": "snapshot-late",
                    "provider": "Test",
                    "usage_basis": "public_record",
                    "retrieved_at": "2026-10-04T00:00:00Z",
                }
            )
        self._add(
            "runtime_minutes",
            120,
            source="source-a",
            source_stream_id="provider:film-a:runtime",
            known_at="2026-10-02T00:00:00Z",
            observation_id="stream-old",
        )
        self._add(
            "runtime_minutes",
            130,
            source="snapshot-late",
            source_stream_id="provider:film-a:runtime",
            known_at="2026-10-04T00:00:00Z",
            observation_id="stream-new",
        )
        with FutureTemporalFactStore(self.db) as store:
            snapshot = store.snapshot_as_of("film-a", "2026-10-05T00:00:00Z")
        self.assertEqual(snapshot["facts"]["runtime_minutes"], 130)
        self.assertEqual(snapshot["conflicts"], [])
        evidence = snapshot["candidates"]["runtime_minutes"][0]["evidence"][0]
        self.assertEqual(evidence["source_id"], "snapshot-late")
        self.assertEqual(evidence["source_stream_id"], "provider:film-a:runtime")

    def test_old_seven_column_table_is_migrated_without_losing_history(self) -> None:
        old_db = Path(self.temp.name) / "old-temporal.duckdb"
        with FutureReleaseStore(old_db) as store:
            store.upsert_source(
                {
                    "source_id": "old-source",
                    "provider": "Legacy",
                    "usage_basis": "public_record",
                    "retrieved_at": "2026-10-01T00:00:00Z",
                }
            )
            store.upsert_project(
                {
                    "project_id": "old-film",
                    "canonical_title": "Old Film",
                }
            )
        conn = duckdb.connect(str(old_db))
        try:
            conn.execute(
                """
                CREATE TABLE future_release_temporal_facts(
                    observation_id VARCHAR PRIMARY KEY,
                    project_id VARCHAR NOT NULL,
                    fact_type VARCHAR NOT NULL,
                    value_json VARCHAR NOT NULL,
                    known_at TIMESTAMPTZ NOT NULL,
                    source_id VARCHAR NOT NULL,
                    confidence DOUBLE NOT NULL
                )
                """
            )
            conn.execute(
                """
                INSERT INTO future_release_temporal_facts
                VALUES ('legacy-runtime', 'old-film', 'runtime_minutes', '111',
                        '2026-10-01T00:00:00Z', 'old-source', 0.8)
                """
            )
        finally:
            conn.close()

        with FutureTemporalFactStore(old_db) as store:
            columns = {
                str(row[0])
                for row in store.conn.execute(
                    "DESCRIBE future_release_temporal_facts"
                ).fetchall()
            }
            snapshot = store.snapshot_as_of(
                "old-film",
                "2026-10-02T00:00:00Z",
            )
        self.assertIn("source_stream_id", columns)
        self.assertEqual(snapshot["facts"]["runtime_minutes"], 111)
        evidence = snapshot["candidates"]["runtime_minutes"][0]["evidence"][0]
        self.assertEqual(evidence["source_stream_id"], "old-source")

    def test_temporal_runtime_and_genres_make_payload_prediction_ready(self) -> None:
        self._add("runtime_minutes", 124, observation_id="runtime")
        self._add("genres", ["Drama", "Sci-Fi"], observation_id="genres")
        self._add("synopsis", "A dated pre-release synopsis.", observation_id="synopsis")
        result = self._builder().build("film-a", "2026-10-03T00:00:00Z")
        self.assertTrue(result["prediction_ready"])
        self.assertEqual(result["blockers"], [])
        self.assertEqual(result["request"]["runtime"], 124)
        self.assertEqual(result["request"]["genres"], ["Drama", "Sci-Fi"])
        self.assertEqual(result["request"]["synopsis"], "A dated pre-release synopsis.")
        self.assertEqual(result["input_sources"]["runtime"], "p9_temporal_fact")
        self.assertEqual(result["input_sources"]["genres"], "p9_temporal_fact")
        self.assertEqual(result["input_sources"]["synopsis"], "p9_temporal_fact")
        self.assertTrue(result["temporal_contract"]["historical_backtest_safe"])

    def test_explicit_runtime_keeps_priority_over_temporal_runtime(self) -> None:
        self._add("runtime_minutes", 124, observation_id="runtime")
        self._add("genres", ["Drama"], observation_id="genres")
        result = self._builder().build(
            "film-a",
            "2026-10-03T00:00:00Z",
            runtime_override=140,
        )
        self.assertTrue(result["prediction_ready"])
        self.assertEqual(result["request"]["runtime"], 140)
        self.assertEqual(result["request"]["genres"], ["Drama"])
        self.assertEqual(result["input_sources"]["runtime"], "override")
        self.assertEqual(result["input_sources"]["genres"], "p9_temporal_fact")

    def test_explicit_genres_keep_priority_over_temporal_genres(self) -> None:
        self._add("runtime_minutes", 124, observation_id="runtime")
        self._add("genres", ["Comedy"], observation_id="genres")
        result = self._builder().build(
            "film-a",
            "2026-10-03T00:00:00Z",
            genres_override=["Drama"],
        )
        self.assertTrue(result["prediction_ready"])
        self.assertEqual(result["request"]["runtime"], 124)
        self.assertEqual(result["request"]["genres"], ["Drama"])
        self.assertEqual(result["input_sources"]["runtime"], "p9_temporal_fact")
        self.assertEqual(result["input_sources"]["genres"], "override")

    def test_genre_conflict_blocks_prediction(self) -> None:
        self._add("runtime_minutes", 124, observation_id="runtime")
        self._add("genres", ["Drama"], source="source-a", observation_id="genre-a")
        self._add("genres", ["Comedy"], source="source-b", observation_id="genre-b")
        result = self._builder().build("film-a", "2026-10-03T00:00:00Z")
        self.assertFalse(result["prediction_ready"])
        self.assertIn("genres_fact_conflict", result["blockers"])
        self.assertIsNone(result["request"])


if __name__ == "__main__":
    unittest.main()
