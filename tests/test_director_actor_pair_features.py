from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

import duckdb

from settings import config
from src.creative_kinovanga import KinoVanga
from src.creative_training import get_batches
from src.director_actor_features import (
    DIRECTOR_ACTOR_PAIR_FEATURE_NAMES,
    fetch_batch_director_actor_pair_context,
    fetch_director_actor_pair_context,
)


class DirectorActorPairFeatureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp.name) / "imdb.duckdb"
        self.old_abspath = config.ABSPATH
        self.old_imdb_db_path = config.IMDB_DB_PATH
        self.old_env = {
            name: os.environ.get(name)
            for name in (
                "VANGA_TRAIN_COVERAGE_FEATURES",
                "VANGA_TRAIN_CREATIVE_TEAM_FEATURES",
                "VANGA_TRAIN_DIRECTOR_WRITER_PAIR_FEATURES",
                "VANGA_TRAIN_DIRECTOR_ACTOR_PAIR_FEATURES",
            )
        }
        config.ABSPATH = self.temp.name
        config.IMDB_DB_PATH = str(self.db_path)
        os.environ["VANGA_TRAIN_COVERAGE_FEATURES"] = "1"
        os.environ["VANGA_TRAIN_CREATIVE_TEAM_FEATURES"] = "1"
        os.environ["VANGA_TRAIN_DIRECTOR_WRITER_PAIR_FEATURES"] = "1"
        os.environ["VANGA_TRAIN_DIRECTOR_ACTOR_PAIR_FEATURES"] = "1"

        self.conn = duckdb.connect(str(self.db_path))
        self.conn.execute(
            """
            CREATE TABLE title_basics (
                tconst VARCHAR,
                titleType VARCHAR,
                primaryTitle VARCHAR,
                startYear VARCHAR,
                runtimeMinutes VARCHAR,
                genres VARCHAR
            )
            """
        )
        self.conn.execute(
            """
            CREATE TABLE title_ratings (
                tconst VARCHAR,
                averageRating DOUBLE,
                numVotes BIGINT
            )
            """
        )
        self.conn.execute(
            """
            CREATE TABLE title_principals (
                tconst VARCHAR,
                ordering INTEGER,
                nconst VARCHAR,
                category VARCHAR
            )
            """
        )
        self.conn.execute(
            """
            CREATE TABLE title_crew (
                tconst VARCHAR,
                directors VARCHAR,
                writers VARCHAR
            )
            """
        )
        self.conn.execute(
            """
            CREATE TABLE title_writers (
                tconst VARCHAR,
                nconst VARCHAR
            )
            """
        )
        self.conn.execute(
            """
            CREATE TABLE name_basics (
                nconst VARCHAR,
                primaryName VARCHAR
            )
            """
        )

        self.conn.executemany(
            "INSERT INTO name_basics VALUES (?, ?)",
            [
                ("nm_dir", "Director A"),
                ("nm_other_dir", "Other Director"),
                ("nm_writer", "Writer A"),
                ("nm_actor1", "Actor One"),
                ("nm_actor2", "Actor Two"),
                ("nm_actor3", "Actor Three"),
            ],
        )

        self._insert_movie(
            "tt2020", "Actor One Old", 2020, 8.0, "nm_dir", ["nm_actor1"]
        )
        self._insert_movie(
            "tt2021", "Actor Two Old", 2021, 5.0, "nm_dir", ["nm_actor2"]
        )
        self._insert_movie(
            "tt2022",
            "Other Director Actor One",
            2022,
            2.0,
            "nm_other_dir",
            ["nm_actor1"],
        )
        self._insert_movie(
            "tt2023",
            "Shared Recent",
            2023,
            6.0,
            "nm_dir",
            ["nm_actor1", "nm_actor2"],
        )
        self._insert_movie(
            "tt2024",
            "Target",
            2024,
            1.0,
            "nm_dir",
            ["nm_actor1", "nm_actor2", "nm_actor3"],
        )
        self._insert_movie(
            "tt2025",
            "Future",
            2025,
            10.0,
            "nm_dir",
            ["nm_actor1", "nm_actor3"],
        )

    def _insert_movie(
        self,
        tconst: str,
        title: str,
        year: int,
        rating: float,
        director: str,
        actors: list[str],
    ) -> None:
        self.conn.execute(
            "INSERT INTO title_basics VALUES (?, 'movie', ?, ?, '110', 'Drama')",
            [tconst, title, str(year)],
        )
        self.conn.execute(
            "INSERT INTO title_ratings VALUES (?, ?, 1000)",
            [tconst, rating],
        )
        self.conn.execute(
            "INSERT INTO title_principals VALUES (?, 1, ?, 'director')",
            [tconst, director],
        )
        for offset, actor in enumerate(actors, start=2):
            self.conn.execute(
                "INSERT INTO title_principals VALUES (?, ?, ?, 'actor')",
                [tconst, offset, actor],
            )
        self.conn.execute(
            "INSERT INTO title_crew VALUES (?, ?, 'nm_writer')",
            [tconst, director],
        )
        self.conn.execute(
            "INSERT INTO title_writers VALUES (?, 'nm_writer')",
            [tconst],
        )

    def tearDown(self):
        self.conn.close()
        config.ABSPATH = self.old_abspath
        config.IMDB_DB_PATH = self.old_imdb_db_path
        for name, old_value in self.old_env.items():
            if old_value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = old_value
        self.temp.cleanup()

    def test_batch_context_separates_three_actor_slots(self):
        context = fetch_batch_director_actor_pair_context(
            self.conn,
            ["tt2024"],
        )["tt2024"]

        # Actor One: 2020=8 и 2023=6. Фильм 2022 с другим режиссёром,
        # target=1 и future=10 не должны входить в pair history.
        self.assertAlmostEqual(
            context["director_actor_1_pair_avg_rating"], 7.0, places=5
        )
        self.assertEqual(context["director_actor_1_pair_count"], 2.0)
        self.assertEqual(context["director_actor_1_pair_known"], 1.0)

        # Actor Two: 2021=5 и 2023=6.
        self.assertAlmostEqual(
            context["director_actor_2_pair_avg_rating"], 5.5, places=5
        )
        self.assertEqual(context["director_actor_2_pair_count"], 2.0)
        self.assertEqual(context["director_actor_2_pair_known"], 1.0)

        # Actor Three до target с этим режиссёром не работал.
        self.assertEqual(context["director_actor_3_pair_avg_rating"], 6.5)
        self.assertEqual(context["director_actor_3_pair_count"], 0.0)
        self.assertEqual(context["director_actor_3_pair_known"], 0.0)

    def test_single_pair_inference_matches_batch_semantics(self):
        actor_one = fetch_director_actor_pair_context(
            self.conn,
            director_nconst="nm_dir",
            actor_nconst="nm_actor1",
            before_year=2024,
        )
        actor_three = fetch_director_actor_pair_context(
            self.conn,
            director_nconst="nm_dir",
            actor_nconst="nm_actor3",
            before_year=2024,
        )

        self.assertAlmostEqual(actor_one["avg_rating"], 7.0, places=5)
        self.assertEqual(actor_one["count"], 2.0)
        self.assertEqual(actor_one["known"], 1.0)
        self.assertEqual(actor_three["avg_rating"], 6.5)
        self.assertEqual(actor_three["count"], 0.0)
        self.assertEqual(actor_three["known"], 0.0)

    def test_training_wrapper_emits_schema_v9_features(self):
        target = None
        for X, _y, _titles, tconsts in get_batches([], batch_size=100):
            if "tt2024" in tconsts:
                target = X.iloc[tconsts.index("tt2024")]
                break

        self.assertIsNotNone(target)
        for feature_name in DIRECTOR_ACTOR_PAIR_FEATURE_NAMES:
            self.assertIn(feature_name, target.index)
        self.assertAlmostEqual(
            float(target["director_actor_1_pair_avg_rating"]), 7.0, places=5
        )
        self.assertAlmostEqual(
            float(target["director_actor_2_pair_avg_rating"]), 5.5, places=5
        )
        self.assertEqual(float(target["director_actor_3_pair_count"]), 0.0)
        self.assertEqual(float(target["director_actor_3_pair_known"]), 0.0)

    def test_schema_v9_inference_preserves_actor_order_and_parity(self):
        engine = KinoVanga.__new__(KinoVanga)
        engine.conn = self.conn
        engine._people_cache = {}
        engine.metadata = {
            "feature_names": [
                "director_avg_rating",
                "director_id",
                "actor_1_id",
                "actor_2_id",
                "actor_3_id",
                *DIRECTOR_ACTOR_PAIR_FEATURE_NAMES,
            ],
            "categorical_features": [
                "director_id",
                "actor_1_id",
                "actor_2_id",
                "actor_3_id",
            ],
        }

        X = engine._prepare_features(
            year=2024,
            runtime=110,
            genres=["Drama"],
            director="Director A",
            actors=["Actor One", "Actor Two", "Actor Three"],
        )
        values = dict(zip(engine.metadata["feature_names"], X[0]))

        self.assertAlmostEqual(
            float(values["director_actor_1_pair_avg_rating"]), 7.0, places=5
        )
        self.assertEqual(float(values["director_actor_1_pair_count"]), 2.0)
        self.assertAlmostEqual(
            float(values["director_actor_2_pair_avg_rating"]), 5.5, places=5
        )
        self.assertEqual(float(values["director_actor_2_pair_count"]), 2.0)
        self.assertEqual(float(values["director_actor_3_pair_avg_rating"]), 6.5)
        self.assertEqual(float(values["director_actor_3_pair_known"]), 0.0)

    def test_actor_pair_block_can_be_disabled_to_reproduce_schema_v8(self):
        os.environ["VANGA_TRAIN_DIRECTOR_ACTOR_PAIR_FEATURES"] = "0"

        X, _y, _titles, _tconsts = next(get_batches([], batch_size=100))
        for feature_name in DIRECTOR_ACTOR_PAIR_FEATURE_NAMES:
            self.assertNotIn(feature_name, X.columns)
        self.assertIn("director_writer_pair_count", X.columns)
        self.assertIn("director_genre_avg_rating", X.columns)


if __name__ == "__main__":
    unittest.main()
