from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import duckdb

from src.rating_history import RatingHistoryError, RatingHistoryStore


class RatingHistoryStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.history_db = root / "rating_history.duckdb"
        self.imdb_db = root / "imdb.duckdb"
        conn = duckdb.connect(str(self.imdb_db))
        try:
            conn.execute(
                """
                CREATE TABLE title_ratings(
                    tconst VARCHAR,
                    averageRating DOUBLE,
                    numVotes BIGINT
                )
                """
            )
            conn.executemany(
                "INSERT INTO title_ratings VALUES (?, ?, ?)",
                [
                    ("tt0000001", 7.1, 100),
                    ("tt0000002", 8.2, 200),
                ],
            )
        finally:
            conn.close()
        self.store = RatingHistoryStore(self.history_db)

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def _update_rating(self, imdb_id: str, rating: float, votes: int):
        conn = duckdb.connect(str(self.imdb_db))
        try:
            conn.execute(
                "UPDATE title_ratings SET averageRating=?, numVotes=? WHERE tconst=?",
                [rating, votes, imdb_id],
            )
        finally:
            conn.close()

    def test_empty_watchlist_does_not_create_snapshot(self):
        result = self.store.capture_daily(
            self.imdb_db,
            observed_at="2026-10-04T01:00:00Z",
            source_fingerprint_sha256="a" * 64,
        )
        self.assertTrue(result["skipped_no_watchlist"])
        self.assertEqual(self.store.status()["snapshot_count"], 0)

    def test_daily_snapshot_is_append_only_and_idempotent(self):
        self.store.watch(["tt0000001", "tt0000002"], source="test")
        first = self.store.capture_daily(
            self.imdb_db,
            observed_at="2026-10-04T01:00:00Z",
            source_fingerprint_sha256="a" * 64,
        )
        second = self.store.capture_daily(
            self.imdb_db,
            observed_at="2026-10-04T20:00:00Z",
            source_fingerprint_sha256="a" * 64,
        )
        self.assertFalse(first["idempotent"])
        self.assertTrue(second["idempotent"])
        self.assertEqual(first["snapshot_id"], second["snapshot_id"])
        self.assertEqual(self.store.status()["snapshot_count"], 1)
        self.assertEqual(self.store.status()["point_count"], 2)

    def test_same_day_different_source_fingerprint_cannot_overwrite(self):
        self.store.watch(["tt0000001"])
        self.store.capture_daily(
            self.imdb_db,
            observed_at="2026-10-04T01:00:00Z",
            source_fingerprint_sha256="a" * 64,
        )
        with self.assertRaises(RatingHistoryError):
            self.store.capture_daily(
                self.imdb_db,
                observed_at="2026-10-04T22:00:00Z",
                source_fingerprint_sha256="b" * 64,
            )

    def test_second_day_appends_new_point_and_as_of_uses_history(self):
        self.store.watch(["tt0000001"])
        self.store.capture_daily(
            self.imdb_db,
            observed_at="2026-10-04T01:00:00Z",
            source_fingerprint_sha256="a" * 64,
        )
        self._update_rating("tt0000001", 7.5, 180)
        self.store.capture_daily(
            self.imdb_db,
            observed_at="2026-10-05T01:00:00Z",
            source_fingerprint_sha256="b" * 64,
        )

        history = self.store.history("tt0000001")
        self.assertEqual(len(history), 2)
        self.assertEqual(history[0]["average_rating"], 7.1)
        self.assertEqual(history[0]["num_votes"], 100)
        self.assertEqual(history[1]["average_rating"], 7.5)
        self.assertEqual(history[1]["num_votes"], 180)

        early = self.store.rating_as_of("tt0000001", "2026-10-04T12:00:00Z")
        late = self.store.rating_as_of("tt0000001", "2026-10-05T12:00:00Z")
        self.assertEqual(early["average_rating"], 7.1)
        self.assertEqual(late["average_rating"], 7.5)
        self.assertTrue(early["point_in_time"])

    def test_missing_tracked_title_is_explicit_in_snapshot_counts(self):
        self.store.watch(["tt0000001", "tt9999999"])
        result = self.store.capture_daily(
            self.imdb_db,
            observed_at="2026-10-04T01:00:00Z",
            source_fingerprint_sha256="a" * 64,
        )
        self.assertEqual(result["tracked_count"], 2)
        self.assertEqual(result["captured_count"], 1)
        self.assertEqual(result["missing_count"], 1)

    def test_unwatch_stops_future_collection_without_deleting_history(self):
        self.store.watch(["tt0000001"])
        self.store.capture_daily(
            self.imdb_db,
            observed_at="2026-10-04T01:00:00Z",
            source_fingerprint_sha256="a" * 64,
        )
        self.store.unwatch(["tt0000001"])
        result = self.store.capture_daily(
            self.imdb_db,
            observed_at="2026-10-05T01:00:00Z",
            source_fingerprint_sha256="b" * 64,
        )
        self.assertTrue(result["skipped_no_watchlist"])
        self.assertEqual(len(self.store.history("tt0000001")), 1)

    def test_invalid_imdb_id_is_rejected(self):
        with self.assertRaises(RatingHistoryError):
            self.store.watch(["movie-1"])


if __name__ == "__main__":
    unittest.main()
