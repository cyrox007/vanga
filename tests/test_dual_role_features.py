from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

import duckdb

from src.dual_role_features import (
    DUAL_ROLE_FEATURE_NAMES,
    dual_role_features_enabled,
    fetch_batch_dual_role_context,
    fetch_dual_role_context,
)


class DualRoleFeatureTests(unittest.TestCase):
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
        self.conn.execute("CREATE TABLE title_crew (tconst VARCHAR, directors VARCHAR, writers VARCHAR)")
        self.conn.execute("CREATE TABLE title_writers (tconst VARCHAR, nconst VARCHAR)")
        self.old_env = os.environ.get("VANGA_TRAIN_DUAL_ROLE_FEATURES")
        os.environ["VANGA_TRAIN_DUAL_ROLE_FEATURES"] = "1"

        # d1 дважды был одновременно режиссёром и сценаристом: avg=7, count=2.
        self._movie("tt_d1_1", 2020, 8.0, ["nm_d1"], ["nm_d1"])
        self._movie("tt_d1_2", 2021, 6.0, ["nm_d1"], ["nm_d1"])
        # d2 имеет режиссёрский опыт, но не dual-role опыт.
        self._movie("tt_d2_direct", 2022, 5.0, ["nm_d2"], ["nm_other"])
        # target writer имеет собственный прошлый dual-role фильм.
        self._movie("tt_w1", 2019, 9.0, ["nm_w1"], ["nm_w1"])

        # Same-year/future dual-role d2 не должны попасть в target 2024.
        self._movie("tt_d2_same", 2024, 10.0, ["nm_d2"], ["nm_d2"])
        self._movie("tt_d2_future", 2025, 10.0, ["nm_d2"], ["nm_d2"])

        self._movie("tt_target", 2024, 2.0, ["nm_d1", "nm_d2"], ["nm_w1"])
        self._movie("tt_overlap", 2024, 2.0, ["nm_d1", "nm_d2"], ["nm_d1"])

    def _movie(
        self,
        tconst: str,
        year: int,
        rating: float,
        directors: list[str],
        writers: list[str],
    ) -> None:
        self.conn.execute(
            "INSERT INTO title_basics VALUES (?, 'movie', ?, ?, '120', 'Drama')",
            [tconst, tconst, str(year)],
        )
        self.conn.execute("INSERT INTO title_ratings VALUES (?, ?, 1000)", [tconst, rating])
        for ordering, director in enumerate(directors, start=1):
            self.conn.execute(
                "INSERT INTO title_principals VALUES (?, ?, ?, 'director')",
                [tconst, ordering, director],
            )
        self.conn.execute(
            "INSERT INTO title_crew VALUES (?, ?, ?)",
            [tconst, ",".join(directors), ",".join(writers)],
        )
        for writer in writers:
            self.conn.execute("INSERT INTO title_writers VALUES (?, ?)", [tconst, writer])

    def tearDown(self):
        self.conn.close()
        if self.old_env is None:
            os.environ.pop("VANGA_TRAIN_DUAL_ROLE_FEATURES", None)
        else:
            os.environ["VANGA_TRAIN_DUAL_ROLE_FEATURES"] = self.old_env
        self.temp.cleanup()

    def test_batch_uses_all_directors_and_excludes_same_year_future(self):
        ctx = fetch_batch_dual_role_context(self.conn, ["tt_target"])["tt_target"]
        self.assertAlmostEqual(ctx["director_team_dual_role_known_ratio"], 0.5, places=5)
        self.assertAlmostEqual(ctx["director_team_dual_role_avg_rating"], 7.0, places=5)
        self.assertAlmostEqual(ctx["director_team_dual_role_prior_count_mean"], 1.0, places=5)
        self.assertAlmostEqual(ctx["director_team_dual_role_prior_count_max"], 2.0, places=5)
        self.assertAlmostEqual(ctx["writer_dual_role_avg_rating"], 9.0, places=5)
        self.assertEqual(ctx["writer_dual_role_prior_count"], 1.0)
        self.assertEqual(ctx["writer_dual_role_known"], 1.0)
        self.assertEqual(ctx["writer_is_in_director_team"], 0.0)

    def test_inference_matches_batch(self):
        batch = fetch_batch_dual_role_context(self.conn, ["tt_target"])["tt_target"]
        inferred = fetch_dual_role_context(
            self.conn,
            director_nconsts=["nm_d1", "nm_d2"],
            writer_nconst="nm_w1",
            before_year=2024,
        )
        for name in DUAL_ROLE_FEATURE_NAMES:
            self.assertAlmostEqual(batch[name], inferred[name], places=5, msg=name)

    def test_writer_overlap_with_director_team_is_explicit(self):
        ctx = fetch_batch_dual_role_context(self.conn, ["tt_overlap"])["tt_overlap"]
        self.assertEqual(ctx["writer_is_in_director_team"], 1.0)
        self.assertAlmostEqual(ctx["writer_dual_role_avg_rating"], 7.0, places=5)
        self.assertEqual(ctx["writer_dual_role_prior_count"], 2.0)

    def test_unknown_director_reduces_coverage_without_fake_rating(self):
        ctx = fetch_dual_role_context(
            self.conn,
            director_nconsts=["nm_d1", "Unknown"],
            writer_nconst=None,
            before_year=2024,
        )
        self.assertAlmostEqual(ctx["director_team_dual_role_known_ratio"], 0.5, places=5)
        self.assertAlmostEqual(ctx["director_team_dual_role_avg_rating"], 7.0, places=5)
        self.assertAlmostEqual(ctx["director_team_dual_role_prior_count_mean"], 1.0, places=5)
        self.assertEqual(ctx["writer_dual_role_known"], 0.0)

    def test_feature_block_has_reproducible_switch(self):
        self.assertTrue(dual_role_features_enabled())
        os.environ["VANGA_TRAIN_DUAL_ROLE_FEATURES"] = "0"
        self.assertFalse(dual_role_features_enabled())


if __name__ == "__main__":
    unittest.main()
