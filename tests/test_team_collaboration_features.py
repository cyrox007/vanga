from __future__ import annotations

import math
import os
import tempfile
import unittest
from pathlib import Path

import duckdb

from src.team_collaboration_features import (
    TEAM_COLLABORATION_FEATURE_NAMES,
    fetch_batch_team_collaboration_context,
    fetch_team_collaboration_context,
    team_collaboration_features_enabled,
)


class TeamCollaborationFeatureTests(unittest.TestCase):
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

        self.old_env = os.environ.get("VANGA_TRAIN_TEAM_COLLABORATION_FEATURES")
        os.environ["VANGA_TRAIN_TEAM_COLLABORATION_FEATURES"] = "1"

        # Director↔writer: d1+w1 имеют одну прошлую работу; d2+w1 — нет.
        self._movie("tt_dw", 2020, 8.0, directors=["nm_d1"], actors=[], writers=["nm_w1"])

        # Director↔actor histories для target-матрицы 2x3.
        self._movie("tt_d1a1_1", 2020, 8.0, directors=["nm_d1"], actors=["nm_a1"])
        self._movie("tt_d1a1_2", 2021, 6.0, directors=["nm_d1"], actors=["nm_a1"])
        self._movie("tt_d1a3", 2022, 5.0, directors=["nm_d1"], actors=["nm_a3"])
        self._movie("tt_d2a1", 2023, 9.0, directors=["nm_d2"], actors=["nm_a1"])

        # Same-year/future связи не должны попасть в target 2024.
        self._movie("tt_same", 2024, 1.0, directors=["nm_d2"], actors=["nm_a2"], writers=["nm_w1"])
        self._movie("tt_future", 2025, 1.0, directors=["nm_d2"], actors=["nm_a3"], writers=["nm_w1"])

        self._movie(
            "tt_target",
            2024,
            2.0,
            directors=["nm_d1", "nm_d2"],
            actors=["nm_a1", "nm_a2", "nm_a3"],
            writers=["nm_w1"],
        )

    def _movie(
        self,
        tconst: str,
        year: int,
        rating: float,
        *,
        directors: list[str],
        actors: list[str],
        writers: list[str] | None = None,
    ) -> None:
        writers = writers or []
        self.conn.execute(
            "INSERT INTO title_basics VALUES (?, 'movie', ?, ?, '120', 'Drama')",
            [tconst, tconst, str(year)],
        )
        self.conn.execute("INSERT INTO title_ratings VALUES (?, ?, 1000)", [tconst, rating])
        ordering = 1
        for director in directors:
            self.conn.execute(
                "INSERT INTO title_principals VALUES (?, ?, ?, 'director')",
                [tconst, ordering, director],
            )
            ordering += 1
        for actor in actors:
            self.conn.execute(
                "INSERT INTO title_principals VALUES (?, ?, ?, 'actor')",
                [tconst, ordering, actor],
            )
            ordering += 1
        self.conn.execute(
            "INSERT INTO title_crew VALUES (?, ?, ?)",
            [
                tconst,
                ",".join(directors) if directors else "\\N",
                ",".join(writers) if writers else "\\N",
            ],
        )
        for writer in writers:
            self.conn.execute("INSERT INTO title_writers VALUES (?, ?)", [tconst, writer])

    def tearDown(self):
        self.conn.close()
        if self.old_env is None:
            os.environ.pop("VANGA_TRAIN_TEAM_COLLABORATION_FEATURES", None)
        else:
            os.environ["VANGA_TRAIN_TEAM_COLLABORATION_FEATURES"] = self.old_env
        self.temp.cleanup()

    def test_batch_uses_all_directors_writer_and_all_cast(self):
        ctx = fetch_batch_team_collaboration_context(self.conn, ["tt_target"])["tt_target"]

        self.assertEqual(ctx["team_writer_pair_total"], 2.0)
        self.assertAlmostEqual(ctx["team_writer_pair_known_ratio"], 0.5, places=5)
        self.assertAlmostEqual(ctx["team_writer_pair_prior_collaboration_mean"], 0.5, places=5)
        self.assertEqual(ctx["team_writer_pair_prior_collaboration_max"], 1.0)
        self.assertAlmostEqual(ctx["team_writer_pair_prior_rating_avg"], 8.0, places=5)

        self.assertEqual(ctx["team_actor_pair_total"], 6.0)
        self.assertAlmostEqual(ctx["team_actor_pair_known_ratio"], 3 / 6, places=5)
        self.assertAlmostEqual(ctx["team_actor_pair_prior_collaboration_mean"], 4 / 6, places=5)
        self.assertAlmostEqual(ctx["team_actor_pair_prior_collaboration_median"], 0.5, places=5)
        self.assertEqual(ctx["team_actor_pair_prior_collaboration_max"], 2.0)
        self.assertAlmostEqual(ctx["team_actor_pair_prior_rating_avg"], 7.0, places=5)
        self.assertAlmostEqual(ctx["team_actor_pair_prior_rating_median"], 7.0, places=5)
        self.assertAlmostEqual(
            ctx["team_actor_pair_prior_rating_std"],
            math.sqrt(8 / 3),
            places=5,
        )

    def test_inference_matches_batch_semantics(self):
        batch = fetch_batch_team_collaboration_context(self.conn, ["tt_target"])["tt_target"]
        inferred = fetch_team_collaboration_context(
            self.conn,
            director_nconsts=["nm_d1", "nm_d2"],
            writer_nconst="nm_w1",
            actor_nconsts=["nm_a1", "nm_a2", "nm_a3"],
            before_year=2024,
        )
        for name in TEAM_COLLABORATION_FEATURE_NAMES:
            self.assertAlmostEqual(batch[name], inferred[name], places=5, msg=name)

    def test_unknown_people_remain_in_denominator_without_fake_history(self):
        ctx = fetch_team_collaboration_context(
            self.conn,
            director_nconsts=["nm_d1", "nm_d2", "Unknown"],
            writer_nconst="nm_w1",
            actor_nconsts=["nm_a1", "nm_a2"],
            before_year=2024,
        )
        self.assertEqual(ctx["team_writer_pair_total"], 3.0)
        self.assertAlmostEqual(ctx["team_writer_pair_known_ratio"], 1 / 3, places=5)
        self.assertEqual(ctx["team_actor_pair_total"], 6.0)
        self.assertAlmostEqual(ctx["team_actor_pair_known_ratio"], 2 / 6, places=5)

    def test_feature_block_has_reproducible_switch(self):
        self.assertTrue(team_collaboration_features_enabled())
        os.environ["VANGA_TRAIN_TEAM_COLLABORATION_FEATURES"] = "0"
        self.assertFalse(team_collaboration_features_enabled())


if __name__ == "__main__":
    unittest.main()
