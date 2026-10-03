from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import duckdb

from src.kinovanga import KinoVanga


class InferenceFeatureParityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp.name) / "imdb.duckdb"
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
            CREATE TABLE name_basics (
                nconst VARCHAR,
                primaryName VARCHAR
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

        self.conn.executemany(
            "INSERT INTO title_basics VALUES (?, ?, ?, ?, ?, ?)",
            [
                ("tt_dir", "movie", "Directed Past", "2020", "100", "Drama"),
                ("tt_act", "movie", "Acted Past", "2021", "100", "Drama"),
                ("tt_future", "movie", "Future", "2025", "100", "Drama"),
            ],
        )
        self.conn.executemany(
            "INSERT INTO title_ratings VALUES (?, ?, ?)",
            [
                ("tt_dir", 8.0, 1000),
                ("tt_act", 4.0, 1000),
                ("tt_future", 10.0, 1000),
            ],
        )
        self.conn.executemany(
            "INSERT INTO name_basics VALUES (?, ?)",
            [
                ("nm_same", "Role Switcher"),
                ("nm_future", "Future Only"),
                ("nm_writer", "Writer Person"),
            ],
        )
        self.conn.executemany(
            "INSERT INTO title_principals VALUES (?, ?, ?, ?)",
            [
                ("tt_dir", 1, "nm_same", "director"),
                ("tt_act", 1, "nm_same", "actor"),
                ("tt_future", 1, "nm_same", "director"),
                ("tt_future", 2, "nm_same", "actor"),
                ("tt_future", 3, "nm_future", "actor"),
            ],
        )
        self.conn.executemany(
            "INSERT INTO title_writers VALUES (?, ?)",
            [
                ("tt_dir", "nm_writer"),
                ("tt_future", "nm_writer"),
            ],
        )

        self.engine = KinoVanga.__new__(KinoVanga)
        self.engine.conn = self.conn
        self.engine._people_cache = {}

    def tearDown(self):
        self.conn.close()
        self.temp.cleanup()

    def test_old_model_schema_ignores_writer_without_writer_features(self):
        self.engine.metadata = {
            "feature_names": ["startYear", "runtimeMinutes"],
            "categorical_features": [],
        }

        X = self.engine._prepare_features(
            2024,
            120,
            ["Drama"],
            writer="Writer Person",
        )

        self.assertEqual(X.shape, (1, 2))
        self.assertAlmostEqual(float(X[0, 0]), 1.24, places=5)
        self.assertAlmostEqual(float(X[0, 1]), 1.20, places=5)

    def test_director_history_uses_only_director_credits(self):
        info = self.engine._get_people_info(
            ["Role Switcher"],
            before_year=2024,
            role="director",
        )

        self.assertEqual(info["Role Switcher"]["nconst"], "nm_same")
        self.assertAlmostEqual(
            info["Role Switcher"]["avg_rating"],
            8.0,
            places=5,
        )
        self.assertEqual(info["Role Switcher"]["prior_count"], 1)
        self.assertTrue(info["Role Switcher"]["known"])

    def test_actor_history_uses_only_actor_actress_credits(self):
        info = self.engine._get_people_info(
            ["Role Switcher"],
            before_year=2024,
            role="actor",
        )

        self.assertEqual(info["Role Switcher"]["nconst"], "nm_same")
        self.assertAlmostEqual(
            info["Role Switcher"]["avg_rating"],
            4.0,
            places=5,
        )
        self.assertEqual(info["Role Switcher"]["prior_count"], 1)

    def test_cache_key_keeps_roles_separate(self):
        director = self.engine._get_people_info(
            ["Role Switcher"],
            before_year=2024,
            role="director",
        )
        actor = self.engine._get_people_info(
            ["Role Switcher"],
            before_year=2024,
            role="actor",
        )

        self.assertEqual(director["Role Switcher"]["avg_rating"], 8.0)
        self.assertEqual(actor["Role Switcher"]["avg_rating"], 4.0)
        self.assertIn(
            ("director", "role switcher", 2024),
            self.engine._people_cache,
        )
        self.assertIn(
            ("actor", "role switcher", 2024),
            self.engine._people_cache,
        )

    def test_future_credits_do_not_leak_but_resolved_id_is_preserved(self):
        info = self.engine._get_people_info(
            ["Future Only"],
            before_year=2024,
            role="actor",
        )

        self.assertEqual(info["Future Only"]["nconst"], "nm_future")
        self.assertEqual(info["Future Only"]["avg_rating"], 6.5)
        self.assertEqual(info["Future Only"]["prior_count"], 0)
        self.assertFalse(info["Future Only"]["known"])

    def test_writer_history_uses_only_past_writer_credits(self):
        info = self.engine._get_people_info(
            ["Writer Person"],
            before_year=2024,
            role="writer",
        )

        self.assertEqual(info["Writer Person"]["nconst"], "nm_writer")
        self.assertAlmostEqual(
            info["Writer Person"]["avg_rating"],
            8.0,
            places=5,
        )
        self.assertEqual(info["Writer Person"]["prior_count"], 1)
        self.assertTrue(info["Writer Person"]["known"])

    def test_schema_v6_emits_known_and_prior_count_features(self):
        self.engine.metadata = {
            "feature_names": [
                "director_avg_rating",
                "director_prior_count",
                "director_known",
                "actor_1_avg_rating",
                "actor_1_prior_count",
                "actor_1_known",
                "director_id",
                "actor_1_id",
            ],
            "categorical_features": ["director_id", "actor_1_id"],
        }

        X = self.engine._prepare_features(
            2024,
            120,
            ["Drama"],
            director="Role Switcher",
            actors=["Future Only"],
        )

        self.assertEqual(X.shape, (1, 8))
        self.assertEqual(float(X[0, 0]), 8.0)
        self.assertEqual(float(X[0, 1]), 1.0)
        self.assertEqual(float(X[0, 2]), 1.0)
        self.assertEqual(float(X[0, 3]), 6.5)
        self.assertEqual(float(X[0, 4]), 0.0)
        self.assertEqual(float(X[0, 5]), 0.0)
        self.assertEqual(X[0, 6], "nm_same")
        self.assertEqual(X[0, 7], "nm_future")

    def test_invalid_role_is_rejected(self):
        with self.assertRaises(ValueError):
            self.engine._get_people_info(
                ["Role Switcher"],
                before_year=2024,
                role="producer",
            )


if __name__ == "__main__":
    unittest.main()
