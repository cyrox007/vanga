from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

import duckdb

from src.full_cast_features import (
    FULL_CAST_FEATURE_NAMES,
    fetch_batch_full_cast_context,
    fetch_full_cast_context,
    full_cast_features_enabled,
)


class FullCastFeatureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp.name) / "imdb.duckdb"
        self.conn = duckdb.connect(str(self.db_path))
        self.conn.execute(
            "CREATE TABLE title_basics (tconst VARCHAR, titleType VARCHAR, primaryTitle VARCHAR, startYear VARCHAR, runtimeMinutes VARCHAR, genres VARCHAR)"
        )
        self.conn.execute(
            "CREATE TABLE title_ratings (tconst VARCHAR, averageRating DOUBLE, numVotes BIGINT)"
        )
        self.conn.execute(
            "CREATE TABLE title_principals (tconst VARCHAR, ordering INTEGER, nconst VARCHAR, category VARCHAR)"
        )
        self.old_env = os.environ.get("VANGA_TRAIN_FULL_CAST_FEATURES")
        os.environ["VANGA_TRAIN_FULL_CAST_FEATURES"] = "1"

        # Четвёртый актёр намеренно самый сильный: тест доказывает, что schema v12
        # не останавливается на legacy actor slots 1..3.
        self._movie("tt_a1", 2020, 8.0, "Drama", ["nm_a"])
        self._movie("tt_a2", 2021, 7.0, "Drama", ["nm_a"])
        self._movie("tt_b1", 2020, 5.0, "Comedy", ["nm_b"])
        self._movie("tt_b2", 2022, 6.0, "Drama", ["nm_b"])
        self._movie("tt_d1", 2023, 9.0, "Drama", ["nm_d"])

        # Эти экстремальные строки не должны попасть в историю target 2024.
        self._movie("tt_same", 2024, 1.0, "Drama", ["nm_a"])
        self._movie("tt_future", 2025, 1.0, "Drama", ["nm_d"])
        self._movie(
            "tt_target",
            2024,
            2.0,
            "Drama",
            ["nm_a", "nm_b", "nm_c", "nm_d"],
        )

    def _movie(
        self,
        tconst: str,
        year: int,
        rating: float,
        genres: str,
        actors: list[str],
    ) -> None:
        self.conn.execute(
            "INSERT INTO title_basics VALUES (?, 'movie', ?, ?, '120', ?)",
            [tconst, tconst, str(year), genres],
        )
        self.conn.execute(
            "INSERT INTO title_ratings VALUES (?, ?, 1000)",
            [tconst, rating],
        )
        for ordering, actor in enumerate(actors, start=1):
            self.conn.execute(
                "INSERT INTO title_principals VALUES (?, ?, ?, 'actor')",
                [tconst, ordering, actor],
            )

    def tearDown(self):
        self.conn.close()
        if self.old_env is None:
            os.environ.pop("VANGA_TRAIN_FULL_CAST_FEATURES", None)
        else:
            os.environ["VANGA_TRAIN_FULL_CAST_FEATURES"] = self.old_env
        self.temp.cleanup()

    def test_batch_uses_every_principal_actor_and_genre_history(self):
        ctx = fetch_batch_full_cast_context(self.conn, ["tt_target"])["tt_target"]
        self.assertEqual(ctx["cast_size"], 4.0)
        self.assertEqual(ctx["cast_known_ratio"], 0.75)
        self.assertAlmostEqual(ctx["cast_avg_rating"], (7.5 + 5.5 + 9.0) / 3, places=5)
        self.assertAlmostEqual(ctx["cast_rating_median"], 7.5, places=5)
        self.assertAlmostEqual(ctx["cast_rating_min"], 5.5, places=5)
        self.assertAlmostEqual(ctx["cast_rating_max"], 9.0, places=5)
        self.assertAlmostEqual(ctx["cast_prior_count_mean"], 1.25, places=5)

        self.assertEqual(ctx["cast_genre_known_ratio"], 0.75)
        self.assertAlmostEqual(ctx["cast_genre_avg_rating"], (7.5 + 6.0 + 9.0) / 3, places=5)
        self.assertAlmostEqual(ctx["cast_genre_prior_count_mean"], 1.0, places=5)
        self.assertAlmostEqual(ctx["cast_genre_rating_max"], 9.0, places=5)

    def test_inference_matches_batch_and_preserves_unknown_coverage(self):
        batch = fetch_batch_full_cast_context(self.conn, ["tt_target"])["tt_target"]
        inferred = fetch_full_cast_context(
            self.conn,
            actor_nconsts=["nm_a", "nm_b", "nm_c", "nm_d"],
            before_year=2024,
            genres=["Drama"],
        )
        for name in FULL_CAST_FEATURE_NAMES:
            self.assertAlmostEqual(batch[name], inferred[name], places=5, msg=name)

        weak = fetch_full_cast_context(
            self.conn,
            actor_nconsts=["nm_a", "Unknown", "nm_c"],
            before_year=2024,
            genres=["Drama"],
        )
        self.assertEqual(weak["cast_size"], 3.0)
        self.assertAlmostEqual(weak["cast_known_ratio"], 1 / 3, places=5)
        self.assertAlmostEqual(weak["cast_genre_known_ratio"], 1 / 3, places=5)

    def test_same_year_target_and_future_do_not_leak(self):
        ctx = fetch_full_cast_context(
            self.conn,
            actor_nconsts=["nm_a", "nm_d"],
            before_year=2024,
            genres="Drama",
        )
        self.assertAlmostEqual(ctx["cast_avg_rating"], (7.5 + 9.0) / 2, places=5)
        self.assertEqual(ctx["cast_prior_count_max"], 2.0)

    def test_feature_block_has_reproducible_switch(self):
        self.assertTrue(full_cast_features_enabled())
        os.environ["VANGA_TRAIN_FULL_CAST_FEATURES"] = "0"
        self.assertFalse(full_cast_features_enabled())


if __name__ == "__main__":
    unittest.main()
