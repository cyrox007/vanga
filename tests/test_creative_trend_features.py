from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

import duckdb

from settings import config
from src.creative_kinovanga import KinoVanga
from src.creative_training import get_batches
from src.trend_features import (
    CREATIVE_TREND_FEATURE_NAMES,
    fetch_batch_creative_trend_context,
    fetch_person_recent_trend,
)


class CreativeTrendFeatureTests(unittest.TestCase):
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
                "VANGA_TRAIN_CREATIVE_TREND_FEATURES",
            )
        }
        config.ABSPATH = self.temp.name
        config.IMDB_DB_PATH = str(self.db_path)
        os.environ["VANGA_TRAIN_COVERAGE_FEATURES"] = "1"
        os.environ["VANGA_TRAIN_CREATIVE_TEAM_FEATURES"] = "1"
        os.environ["VANGA_TRAIN_DIRECTOR_WRITER_PAIR_FEATURES"] = "1"
        os.environ["VANGA_TRAIN_DIRECTOR_ACTOR_PAIR_FEATURES"] = "1"
        os.environ["VANGA_TRAIN_CREATIVE_TREND_FEATURES"] = "1"

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
                ("nm_dir", "Director Trend"),
                ("nm_writer", "Writer Trend"),
                ("nm_other_dir", "Other Director"),
                ("nm_other_writer", "Other Writer"),
            ],
        )

        # История режиссёра: older 4/5/6, recent 7/8/9 => trend +3.
        for index, (year, rating) in enumerate(
            zip(range(2017, 2023), [4.0, 5.0, 6.0, 7.0, 8.0, 9.0]),
            start=1,
        ):
            self._insert_movie(
                f"ttd{index:02d}",
                f"Director history {index}",
                year,
                rating,
                director="nm_dir",
                writer="nm_other_writer",
            )

        # История сценариста: older 9/8/7, recent 6/5/4 => trend -3.
        for index, (year, rating) in enumerate(
            zip(range(2017, 2023), [9.0, 8.0, 7.0, 6.0, 5.0, 4.0]),
            start=1,
        ):
            self._insert_movie(
                f"ttw{index:02d}",
                f"Writer history {index}",
                year,
                rating,
                director="nm_other_dir",
                writer="nm_writer",
            )

        self._insert_movie(
            "tttarget",
            "Target",
            2024,
            1.0,
            director="nm_dir",
            writer="nm_writer",
        )
        # Работа того же года и future намеренно экстремальны: обе должны быть
        # исключены строгим условием startYear < target_year.
        self._insert_movie(
            "ttsame",
            "Same year",
            2024,
            0.0,
            director="nm_dir",
            writer="nm_writer",
        )
        self._insert_movie(
            "ttfuture",
            "Future",
            2025,
            10.0,
            director="nm_dir",
            writer="nm_writer",
        )

    def _insert_movie(
        self,
        tconst: str,
        title: str,
        year: int,
        rating: float,
        *,
        director: str,
        writer: str,
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
        self.conn.execute(
            "INSERT INTO title_crew VALUES (?, ?, ?)",
            [tconst, director, writer],
        )
        self.conn.execute(
            "INSERT INTO title_writers VALUES (?, ?)",
            [tconst, writer],
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

    def test_batch_context_uses_only_six_previous_works(self):
        context = fetch_batch_creative_trend_context(
            self.conn,
            ["tttarget"],
        )["tttarget"]

        self.assertAlmostEqual(context["director_recent_trend"], 3.0, places=5)
        self.assertEqual(context["director_recent_trend_known"], 1.0)
        self.assertAlmostEqual(context["writer_recent_trend"], -3.0, places=5)
        self.assertEqual(context["writer_recent_trend_known"], 1.0)

    def test_inference_matches_batch_semantics(self):
        director = fetch_person_recent_trend(
            self.conn,
            nconst="nm_dir",
            before_year=2024,
            role="director",
        )
        writer = fetch_person_recent_trend(
            self.conn,
            nconst="nm_writer",
            before_year=2024,
            role="writer",
        )

        self.assertAlmostEqual(director["trend"], 3.0, places=5)
        self.assertEqual(director["known"], 1.0)
        self.assertAlmostEqual(writer["trend"], -3.0, places=5)
        self.assertEqual(writer["known"], 1.0)

    def test_less_than_six_works_is_explicitly_unknown(self):
        limited = fetch_person_recent_trend(
            self.conn,
            nconst="nm_other_dir",
            before_year=2021,
            role="director",
        )
        self.assertEqual(limited["trend"], 0.0)
        self.assertEqual(limited["known"], 0.0)

    def test_training_wrapper_emits_schema_v10_features(self):
        target = None
        for X, _y, _titles, tconsts in get_batches([], batch_size=100):
            if "tttarget" in tconsts:
                target = X.iloc[tconsts.index("tttarget")]
                break

        self.assertIsNotNone(target)
        for feature_name in CREATIVE_TREND_FEATURE_NAMES:
            self.assertIn(feature_name, target.index)
        self.assertAlmostEqual(float(target["director_recent_trend"]), 3.0, places=5)
        self.assertEqual(float(target["director_recent_trend_known"]), 1.0)
        self.assertAlmostEqual(float(target["writer_recent_trend"]), -3.0, places=5)
        self.assertEqual(float(target["writer_recent_trend_known"]), 1.0)

    def test_schema_v10_inference_parity(self):
        engine = KinoVanga.__new__(KinoVanga)
        engine.conn = self.conn
        engine._people_cache = {}
        engine.metadata = {
            "feature_names": [
                "director_avg_rating",
                "writer_avg_rating",
                "director_id",
                "writer_id",
                *CREATIVE_TREND_FEATURE_NAMES,
            ],
            "categorical_features": ["director_id", "writer_id"],
        }

        X = engine._prepare_features(
            year=2024,
            runtime=110,
            genres=["Drama"],
            director="Director Trend",
            writer="Writer Trend",
        )
        values = dict(zip(engine.metadata["feature_names"], X[0]))

        self.assertAlmostEqual(float(values["director_recent_trend"]), 3.0, places=5)
        self.assertEqual(float(values["director_recent_trend_known"]), 1.0)
        self.assertAlmostEqual(float(values["writer_recent_trend"]), -3.0, places=5)
        self.assertEqual(float(values["writer_recent_trend_known"]), 1.0)

    def test_trend_block_can_be_disabled_to_reproduce_schema_v9(self):
        os.environ["VANGA_TRAIN_CREATIVE_TREND_FEATURES"] = "0"

        X, _y, _titles, _tconsts = next(get_batches([], batch_size=100))
        for feature_name in CREATIVE_TREND_FEATURE_NAMES:
            self.assertNotIn(feature_name, X.columns)
        self.assertIn("director_actor_1_pair_count", X.columns)
        self.assertIn("director_writer_pair_count", X.columns)
        self.assertIn("director_genre_avg_rating", X.columns)


if __name__ == "__main__":
    unittest.main()
