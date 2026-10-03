from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

import duckdb

from settings import config
from src.creative_kinovanga import KinoVanga
from src.creative_team_features import (
    CREATIVE_TEAM_FEATURE_NAMES,
    fetch_batch_creative_team_context,
    fetch_person_creative_context,
)
from src.creative_training import get_batches


class CreativeTeamFeatureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp.name) / "imdb.duckdb"
        self.old_abspath = config.ABSPATH
        self.old_imdb_db_path = config.IMDB_DB_PATH
        self.old_coverage = os.environ.get("VANGA_TRAIN_COVERAGE_FEATURES")
        self.old_creative = os.environ.get("VANGA_TRAIN_CREATIVE_TEAM_FEATURES")
        config.ABSPATH = self.temp.name
        config.IMDB_DB_PATH = str(self.db_path)
        os.environ["VANGA_TRAIN_COVERAGE_FEATURES"] = "1"
        os.environ["VANGA_TRAIN_CREATIVE_TEAM_FEATURES"] = "1"

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
            ("tt2018", "movie", "Old Comedy", "2018", "100", "Comedy", 1.0),
            ("tt2019", "movie", "Old Drama", "2019", "100", "Drama", 2.0),
            ("tt2020", "movie", "Middle Comedy", "2020", "100", "Comedy", 3.0),
            ("tt2021", "movie", "Middle Drama", "2021", "100", "Drama", 4.0),
            ("tt2022", "movie", "Recent Drama", "2022", "100", "Drama", 5.0),
            ("tt2023", "movie", "Recent Mixed", "2023", "100", "Action,Drama", 9.0),
            ("tt2024", "movie", "Target", "2024", "120", "Drama", 2.5),
            ("tt2025", "movie", "Future", "2025", "120", "Drama", 10.0),
        ]
        self.conn.executemany(
            "INSERT INTO title_basics VALUES (?, ?, ?, ?, ?, ?)",
            [row[:6] for row in movies],
        )
        self.conn.executemany(
            "INSERT INTO title_ratings VALUES (?, ?, ?)",
            [(row[0], row[6], 1000) for row in movies],
        )
        self.conn.execute(
            "INSERT INTO name_basics VALUES ('nm_multi', 'Multi Person')"
        )

        for tconst, *_rest in movies:
            self.conn.execute(
                "INSERT INTO title_principals VALUES (?, 1, 'nm_multi', 'director')",
                [tconst],
            )
            self.conn.execute(
                "INSERT INTO title_crew VALUES (?, 'nm_multi', 'nm_multi')",
                [tconst],
            )
            self.conn.execute(
                "INSERT INTO title_writers VALUES (?, 'nm_multi')",
                [tconst],
            )

    def tearDown(self):
        self.conn.close()
        config.ABSPATH = self.old_abspath
        config.IMDB_DB_PATH = self.old_imdb_db_path
        if self.old_coverage is None:
            os.environ.pop("VANGA_TRAIN_COVERAGE_FEATURES", None)
        else:
            os.environ["VANGA_TRAIN_COVERAGE_FEATURES"] = self.old_coverage
        if self.old_creative is None:
            os.environ.pop("VANGA_TRAIN_CREATIVE_TEAM_FEATURES", None)
        else:
            os.environ["VANGA_TRAIN_CREATIVE_TEAM_FEATURES"] = self.old_creative
        self.temp.cleanup()

    def test_batch_context_uses_matching_genres_and_last_five_past_works(self):
        context = fetch_batch_creative_team_context(self.conn, ["tt2024"])["tt2024"]

        # Drama history: 2019=2, 2021=4, 2022=5, 2023=9. Target=2.5 и
        # future=10 не должны попадать ни в genre history, ни в recent form.
        self.assertAlmostEqual(context["director_genre_avg_rating"], 5.0, places=5)
        self.assertEqual(context["director_genre_prior_count"], 4.0)
        self.assertAlmostEqual(context["writer_genre_avg_rating"], 5.0, places=5)
        self.assertEqual(context["writer_genre_prior_count"], 4.0)

        # Последние пять прошлых работ: 2019..2023 = 2,3,4,5,9 => 4.6.
        self.assertAlmostEqual(context["director_recent_avg_rating"], 4.6, places=5)
        self.assertAlmostEqual(context["writer_recent_avg_rating"], 4.6, places=5)
        self.assertEqual(context["director_is_writer"], 1.0)

    def test_training_wrapper_emits_candidate_v7_features(self):
        target = None
        for X, _y, _titles, tconsts in get_batches([], batch_size=100):
            if "tt2024" in tconsts:
                target = X.iloc[tconsts.index("tt2024")]
                break

        self.assertIsNotNone(target)
        for feature_name in CREATIVE_TEAM_FEATURE_NAMES:
            self.assertIn(feature_name, target.index)
        self.assertAlmostEqual(float(target["director_genre_avg_rating"]), 5.0, places=5)
        self.assertEqual(float(target["director_genre_prior_count"]), 4.0)
        self.assertAlmostEqual(float(target["director_recent_avg_rating"]), 4.6, places=5)
        self.assertAlmostEqual(float(target["writer_genre_avg_rating"]), 5.0, places=5)
        self.assertEqual(float(target["writer_genre_prior_count"]), 4.0)
        self.assertAlmostEqual(float(target["writer_recent_avg_rating"]), 4.6, places=5)
        self.assertEqual(float(target["director_is_writer"]), 1.0)

    def test_person_context_matches_training_semantics(self):
        director = fetch_person_creative_context(
            self.conn,
            nconst="nm_multi",
            before_year=2024,
            role="director",
            genres=["Drama"],
        )
        writer = fetch_person_creative_context(
            self.conn,
            nconst="nm_multi",
            before_year=2024,
            role="writer",
            genres="Drama",
        )

        for context in (director, writer):
            self.assertAlmostEqual(context["genre_avg_rating"], 5.0, places=5)
            self.assertEqual(context["genre_prior_count"], 4.0)
            self.assertAlmostEqual(context["recent_avg_rating"], 4.6, places=5)

    def test_schema_v7_inference_fills_same_creative_values(self):
        engine = KinoVanga.__new__(KinoVanga)
        engine.conn = self.conn
        engine._people_cache = {}
        engine.metadata = {
            "feature_names": [
                "director_avg_rating",
                "writer_avg_rating",
                "director_id",
                "writer_id",
                *CREATIVE_TEAM_FEATURE_NAMES,
            ],
            "categorical_features": ["director_id", "writer_id"],
        }

        X = engine._prepare_features(
            year=2024,
            runtime=120,
            genres=["Drama"],
            director="Multi Person",
            writer="Multi Person",
            actors=[],
        )
        values = dict(zip(engine.metadata["feature_names"], X[0]))

        self.assertAlmostEqual(float(values["director_genre_avg_rating"]), 5.0, places=5)
        self.assertEqual(float(values["director_genre_prior_count"]), 4.0)
        self.assertAlmostEqual(float(values["director_recent_avg_rating"]), 4.6, places=5)
        self.assertAlmostEqual(float(values["writer_genre_avg_rating"]), 5.0, places=5)
        self.assertEqual(float(values["writer_genre_prior_count"]), 4.0)
        self.assertAlmostEqual(float(values["writer_recent_avg_rating"]), 4.6, places=5)
        self.assertEqual(float(values["director_is_writer"]), 1.0)

    def test_old_schema_does_not_request_creative_context(self):
        engine = KinoVanga.__new__(KinoVanga)
        engine.conn = self.conn
        engine._people_cache = {}
        engine.metadata = {
            "feature_names": ["startYear", "runtimeMinutes"],
            "categorical_features": [],
        }

        X = engine._prepare_features(
            year=2024,
            runtime=120,
            genres=["Drama"],
            director="Multi Person",
            writer="Multi Person",
        )
        self.assertEqual(X.shape, (1, 2))
        self.assertAlmostEqual(float(X[0, 0]), 1.24, places=5)
        self.assertAlmostEqual(float(X[0, 1]), 1.20, places=5)


if __name__ == "__main__":
    unittest.main()
