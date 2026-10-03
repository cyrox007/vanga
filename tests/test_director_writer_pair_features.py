from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

import duckdb

from settings import config
from src.creative_kinovanga import KinoVanga
from src.creative_training import get_batches
from src.pair_features import (
    DIRECTOR_WRITER_PAIR_FEATURE_NAMES,
    fetch_batch_director_writer_pair_context,
    fetch_director_writer_pair_context,
)


class DirectorWriterPairFeatureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp.name) / "imdb.duckdb"
        self.old_abspath = config.ABSPATH
        self.old_imdb_db_path = config.IMDB_DB_PATH
        self.old_coverage = os.environ.get("VANGA_TRAIN_COVERAGE_FEATURES")
        self.old_creative = os.environ.get("VANGA_TRAIN_CREATIVE_TEAM_FEATURES")
        self.old_pair = os.environ.get("VANGA_TRAIN_DIRECTOR_WRITER_PAIR_FEATURES")
        config.ABSPATH = self.temp.name
        config.IMDB_DB_PATH = str(self.db_path)
        os.environ["VANGA_TRAIN_COVERAGE_FEATURES"] = "1"
        os.environ["VANGA_TRAIN_CREATIVE_TEAM_FEATURES"] = "1"
        os.environ["VANGA_TRAIN_DIRECTOR_WRITER_PAIR_FEATURES"] = "1"

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

        movies = [
            ("tt2020", "Shared Old", "2020", 8.0, "nm_dir", "nm_writer"),
            ("tt2021", "Director Other", "2021", 4.0, "nm_dir", "nm_other_writer"),
            ("tt2022", "Writer Other", "2022", 5.0, "nm_other_dir", "nm_writer"),
            ("tt2023", "Shared Recent", "2023", 6.0, "nm_dir", "nm_writer"),
            ("tt2024", "Target", "2024", 1.0, "nm_dir", "nm_writer"),
            ("tt2025", "Future", "2025", 10.0, "nm_dir", "nm_writer"),
        ]
        for tconst, title, year, rating, director, writer in movies:
            self.conn.execute(
                "INSERT INTO title_basics VALUES (?, 'movie', ?, ?, '110', 'Drama')",
                [tconst, title, year],
            )
            self.conn.execute(
                "INSERT INTO title_ratings VALUES (?, ?, 1000)",
                [tconst, rating],
            )
            self.conn.execute(
                "INSERT INTO title_principals VALUES (?, 1, ?, 'director')",
                [tconst, director],
            )
            self.conn.execute(
                "INSERT INTO title_crew VALUES (?, ?, ?)",
                [tconst, director, writer],
            )
            self.conn.execute(
                "INSERT INTO title_writers VALUES (?, ?)",
                [tconst, writer],
            )

        self.conn.executemany(
            "INSERT INTO name_basics VALUES (?, ?)",
            [
                ("nm_dir", "Director A"),
                ("nm_writer", "Writer B"),
                ("nm_other_dir", "Director Other"),
                ("nm_other_writer", "Writer Other"),
            ],
        )

    def tearDown(self):
        self.conn.close()
        config.ABSPATH = self.old_abspath
        config.IMDB_DB_PATH = self.old_imdb_db_path
        for name, old_value in (
            ("VANGA_TRAIN_COVERAGE_FEATURES", self.old_coverage),
            ("VANGA_TRAIN_CREATIVE_TEAM_FEATURES", self.old_creative),
            ("VANGA_TRAIN_DIRECTOR_WRITER_PAIR_FEATURES", self.old_pair),
        ):
            if old_value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = old_value
        self.temp.cleanup()

    def test_batch_pair_history_uses_only_previous_shared_films(self):
        context = fetch_batch_director_writer_pair_context(
            self.conn,
            ["tt2024"],
        )["tt2024"]

        # Совместные прошлые фильмы: 2020=8 и 2023=6. Работы по отдельности,
        # target=1 и future=10 не должны влиять на pair history.
        self.assertAlmostEqual(
            context["director_writer_pair_avg_rating"],
            7.0,
            places=5,
        )
        self.assertEqual(context["director_writer_pair_count"], 2.0)
        self.assertEqual(context["director_writer_pair_known"], 1.0)

    def test_inference_pair_context_matches_batch_semantics(self):
        context = fetch_director_writer_pair_context(
            self.conn,
            director_nconst="nm_dir",
            writer_nconst="nm_writer",
            before_year=2024,
        )

        self.assertAlmostEqual(
            context["director_writer_pair_avg_rating"],
            7.0,
            places=5,
        )
        self.assertEqual(context["director_writer_pair_count"], 2.0)
        self.assertEqual(context["director_writer_pair_known"], 1.0)

    def test_training_wrapper_emits_schema_v8_pair_features(self):
        target = None
        for X, _y, _titles, tconsts in get_batches([], batch_size=100):
            if "tt2024" in tconsts:
                target = X.iloc[tconsts.index("tt2024")]
                break

        self.assertIsNotNone(target)
        for feature_name in DIRECTOR_WRITER_PAIR_FEATURE_NAMES:
            self.assertIn(feature_name, target.index)
        self.assertAlmostEqual(
            float(target["director_writer_pair_avg_rating"]),
            7.0,
            places=5,
        )
        self.assertEqual(float(target["director_writer_pair_count"]), 2.0)
        self.assertEqual(float(target["director_writer_pair_known"]), 1.0)

    def test_schema_v8_inference_fills_same_pair_values(self):
        engine = KinoVanga.__new__(KinoVanga)
        engine.conn = self.conn
        engine._people_cache = {}
        engine.metadata = {
            "feature_names": [
                "director_avg_rating",
                "writer_avg_rating",
                "director_id",
                "writer_id",
                *DIRECTOR_WRITER_PAIR_FEATURE_NAMES,
            ],
            "categorical_features": ["director_id", "writer_id"],
        }

        X = engine._prepare_features(
            year=2024,
            runtime=110,
            genres=["Drama"],
            director="Director A",
            writer="Writer B",
            actors=[],
        )
        values = dict(zip(engine.metadata["feature_names"], X[0]))

        self.assertAlmostEqual(
            float(values["director_writer_pair_avg_rating"]),
            7.0,
            places=5,
        )
        self.assertEqual(float(values["director_writer_pair_count"]), 2.0)
        self.assertEqual(float(values["director_writer_pair_known"]), 1.0)

    def test_unknown_or_never_collaborated_pair_is_explicitly_missing(self):
        unknown = fetch_director_writer_pair_context(
            self.conn,
            director_nconst="Unknown",
            writer_nconst="nm_writer",
            before_year=2024,
        )
        never = fetch_director_writer_pair_context(
            self.conn,
            director_nconst="nm_other_dir",
            writer_nconst="nm_other_writer",
            before_year=2024,
        )

        for context in (unknown, never):
            self.assertEqual(context["director_writer_pair_avg_rating"], 6.5)
            self.assertEqual(context["director_writer_pair_count"], 0.0)
            self.assertEqual(context["director_writer_pair_known"], 0.0)

    def test_pair_block_can_be_disabled_to_reproduce_schema_v7(self):
        os.environ["VANGA_TRAIN_DIRECTOR_WRITER_PAIR_FEATURES"] = "0"

        X, _y, _titles, _tconsts = next(get_batches([], batch_size=100))
        for feature_name in DIRECTOR_WRITER_PAIR_FEATURE_NAMES:
            self.assertNotIn(feature_name, X.columns)
        self.assertIn("director_genre_avg_rating", X.columns)
        self.assertIn("director_recent_avg_rating", X.columns)


if __name__ == "__main__":
    unittest.main()
