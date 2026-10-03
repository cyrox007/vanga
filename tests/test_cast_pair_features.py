from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

import duckdb

from src.cast_pair_features import (
    CAST_PAIR_FEATURE_NAMES,
    cast_pair_features_enabled,
    fetch_batch_cast_pair_context,
    fetch_cast_pair_context,
)


class CastPairFeatureTests(unittest.TestCase):
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
        self.old_env = os.environ.get("VANGA_TRAIN_CAST_PAIR_FEATURES")
        os.environ["VANGA_TRAIN_CAST_PAIR_FEATURES"] = "1"

        # A+B работали дважды: count=2, avg=7.
        self._movie("tt_ab1", 2020, 8.0, ["nm_a", "nm_b"])
        self._movie("tt_ab2", 2021, 6.0, ["nm_a", "nm_b"])
        # A+C один раз: count=1, avg=5.
        self._movie("tt_ac", 2022, 5.0, ["nm_a", "nm_c"])
        # C+D один раз: count=1, avg=9.
        self._movie("tt_cd", 2023, 9.0, ["nm_c", "nm_d"])

        # Same-year и future совместные фильмы не должны утекать в target 2024.
        self._movie("tt_same", 2024, 1.0, ["nm_b", "nm_c"])
        self._movie("tt_future", 2025, 1.0, ["nm_a", "nm_d"])
        self._movie("tt_target", 2024, 2.0, ["nm_a", "nm_b", "nm_c", "nm_d"])

    def _movie(self, tconst: str, year: int, rating: float, actors: list[str]) -> None:
        self.conn.execute(
            "INSERT INTO title_basics VALUES (?, 'movie', ?, ?, '120', 'Drama')",
            [tconst, tconst, str(year)],
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
            os.environ.pop("VANGA_TRAIN_CAST_PAIR_FEATURES", None)
        else:
            os.environ["VANGA_TRAIN_CAST_PAIR_FEATURES"] = self.old_env
        self.temp.cleanup()

    def test_batch_uses_all_cast_pairs_and_excludes_same_year_future(self):
        ctx = fetch_batch_cast_pair_context(self.conn, ["tt_target"])["tt_target"]
        # Для четырёх актёров существует 6 unordered пар.
        self.assertEqual(ctx["cast_pair_total"], 6.0)
        # История есть у AB, AC, CD. BC same-year и AD future не считаются.
        self.assertAlmostEqual(ctx["cast_pair_known_ratio"], 3 / 6, places=5)
        counts = [2.0, 1.0, 1.0, 0.0, 0.0, 0.0]
        self.assertAlmostEqual(
            ctx["cast_pair_prior_collaboration_mean"],
            sum(counts) / 6,
            places=5,
        )
        self.assertEqual(ctx["cast_pair_prior_collaboration_median"], 0.5)
        self.assertEqual(ctx["cast_pair_prior_collaboration_max"], 2.0)
        self.assertAlmostEqual(ctx["cast_pair_prior_rating_avg"], (7.0 + 5.0 + 9.0) / 3, places=5)
        self.assertEqual(ctx["cast_pair_prior_rating_median"], 7.0)

    def test_inference_matches_batch_pair_semantics(self):
        batch = fetch_batch_cast_pair_context(self.conn, ["tt_target"])["tt_target"]
        inferred = fetch_cast_pair_context(
            self.conn,
            actor_nconsts=["nm_a", "nm_b", "nm_c", "nm_d"],
            before_year=2024,
        )
        for name in CAST_PAIR_FEATURE_NAMES:
            self.assertAlmostEqual(batch[name], inferred[name], places=5, msg=name)

    def test_unknown_actor_keeps_pair_denominator_without_fake_history(self):
        ctx = fetch_cast_pair_context(
            self.conn,
            actor_nconsts=["nm_a", "nm_b", "Unknown"],
            before_year=2024,
        )
        self.assertEqual(ctx["cast_pair_total"], 3.0)
        self.assertAlmostEqual(ctx["cast_pair_known_ratio"], 1 / 3, places=5)
        self.assertAlmostEqual(ctx["cast_pair_prior_collaboration_mean"], 2 / 3, places=5)
        self.assertAlmostEqual(ctx["cast_pair_prior_rating_avg"], 7.0, places=5)

    def test_feature_block_has_reproducible_switch(self):
        self.assertTrue(cast_pair_features_enabled())
        os.environ["VANGA_TRAIN_CAST_PAIR_FEATURES"] = "0"
        self.assertFalse(cast_pair_features_enabled())


if __name__ == "__main__":
    unittest.main()
