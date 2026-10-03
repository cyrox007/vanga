from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

import duckdb

from settings import config
from src.creative_kinovanga import KinoVanga
from src.creative_training import get_batches
from src.director_team_features import (
    DIRECTOR_TEAM_FEATURE_NAMES,
    fetch_batch_director_team_context,
    fetch_director_team_context,
)


class DirectorTeamFeatureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp.name) / "imdb.duckdb"
        self.old_abspath = config.ABSPATH
        self.old_db = config.IMDB_DB_PATH
        self.env_names = (
            "VANGA_TRAIN_COVERAGE_FEATURES",
            "VANGA_TRAIN_CREATIVE_TEAM_FEATURES",
            "VANGA_TRAIN_DIRECTOR_WRITER_PAIR_FEATURES",
            "VANGA_TRAIN_DIRECTOR_ACTOR_PAIR_FEATURES",
            "VANGA_TRAIN_CREATIVE_TREND_FEATURES",
            "VANGA_TRAIN_DIRECTOR_TEAM_FEATURES",
        )
        self.old_env = {name: os.environ.get(name) for name in self.env_names}
        config.ABSPATH = self.temp.name
        config.IMDB_DB_PATH = str(self.db_path)
        for name in self.env_names:
            os.environ[name] = "1"

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
        self.conn.execute(
            "CREATE TABLE title_crew (tconst VARCHAR, directors VARCHAR, writers VARCHAR)"
        )
        self.conn.execute("CREATE TABLE title_writers (tconst VARCHAR, nconst VARCHAR)")
        self.conn.execute("CREATE TABLE name_basics (nconst VARCHAR, primaryName VARCHAR)")
        self.conn.executemany(
            "INSERT INTO name_basics VALUES (?, ?)",
            [
                ("nm_a", "Director A"),
                ("nm_b", "Director B"),
                ("nm_writer", "Writer"),
                ("nm_actor", "Actor"),
            ],
        )

        self._movie("tt2020", 2020, 8.0, ["nm_a", "nm_b"])
        self._movie("tt2021", 2021, 4.0, ["nm_a"])
        self._movie("tt2022", 2022, 6.0, ["nm_a", "nm_b"])
        self._movie("tt2023", 2023, 10.0, ["nm_b"])
        self._movie("tt2024", 2024, 2.0, ["nm_a", "nm_b"])
        self._movie("tt2025", 2025, 1.0, ["nm_a", "nm_b"])

    def _movie(self, tconst: str, year: int, rating: float, directors: list[str]):
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
            "INSERT INTO title_principals VALUES (?, 10, 'nm_actor', 'actor')",
            [tconst],
        )
        self.conn.execute(
            "INSERT INTO title_crew VALUES (?, ?, 'nm_writer')",
            [tconst, ",".join(directors)],
        )
        self.conn.execute("INSERT INTO title_writers VALUES (?, 'nm_writer')", [tconst])

    def tearDown(self):
        self.conn.close()
        config.ABSPATH = self.old_abspath
        config.IMDB_DB_PATH = self.old_db
        for name, value in self.old_env.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        self.temp.cleanup()

    def test_batch_uses_all_directors_and_excludes_target_future(self):
        ctx = fetch_batch_director_team_context(self.conn, ["tt2024"])["tt2024"]
        self.assertEqual(ctx["director_team_size"], 2.0)
        self.assertEqual(ctx["director_team_known_ratio"], 1.0)
        self.assertAlmostEqual(ctx["director_team_avg_rating"], 7.0, places=5)
        self.assertAlmostEqual(ctx["director_team_prior_count_mean"], 3.0, places=5)
        self.assertEqual(ctx["director_team_prior_collaboration_count"], 2.0)
        self.assertAlmostEqual(
            ctx["director_team_prior_collaboration_avg_rating"], 7.0, places=5
        )
        self.assertEqual(ctx["director_team_collaboration_known"], 1.0)

    def test_inference_matches_batch_team_semantics(self):
        ctx = fetch_director_team_context(
            self.conn,
            director_nconsts=["nm_a", "nm_b"],
            before_year=2024,
        )
        self.assertEqual(ctx["director_team_size"], 2.0)
        self.assertAlmostEqual(ctx["director_team_avg_rating"], 7.0, places=5)
        self.assertEqual(ctx["director_team_prior_collaboration_count"], 2.0)
        self.assertAlmostEqual(
            ctx["director_team_prior_collaboration_avg_rating"], 7.0, places=5
        )

    def test_training_wrapper_emits_schema_v11_team_features(self):
        target = None
        for X, _y, _titles, tconsts in get_batches([], batch_size=100):
            if "tt2024" in tconsts:
                target = X.iloc[tconsts.index("tt2024")]
                break
        self.assertIsNotNone(target)
        for name in DIRECTOR_TEAM_FEATURE_NAMES:
            self.assertIn(name, target.index)
        self.assertEqual(float(target["director_team_size"]), 2.0)
        self.assertEqual(float(target["director_team_prior_collaboration_count"]), 2.0)

    def test_inference_prepare_features_accepts_multiple_directors(self):
        engine = KinoVanga.__new__(KinoVanga)
        engine.conn = self.conn
        engine._people_cache = {}
        engine.metadata = {
            "feature_names": list(DIRECTOR_TEAM_FEATURE_NAMES),
            "categorical_features": [],
        }
        X = engine._prepare_features(
            year=2024,
            runtime=120,
            genres=["Drama"],
            director="Director A",
            directors=["Director A", "Director B"],
            actors=[],
        )
        values = dict(zip(engine.metadata["feature_names"], X[0]))
        self.assertEqual(float(values["director_team_size"]), 2.0)
        self.assertAlmostEqual(float(values["director_team_avg_rating"]), 7.0, places=5)
        self.assertEqual(float(values["director_team_prior_collaboration_count"]), 2.0)

    def test_director_team_block_can_be_disabled_to_reproduce_v10(self):
        os.environ["VANGA_TRAIN_DIRECTOR_TEAM_FEATURES"] = "0"
        X, _y, _titles, _ids = next(get_batches([], batch_size=100))
        for name in DIRECTOR_TEAM_FEATURE_NAMES:
            self.assertNotIn(name, X.columns)
        self.assertIn("director_recent_trend", X.columns)


if __name__ == "__main__":
    unittest.main()
