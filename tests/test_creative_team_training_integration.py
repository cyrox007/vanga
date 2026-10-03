from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import duckdb

from settings import config
from src.data_filtr import get_batches


class CreativeTeamTrainingIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.old_abspath = config.ABSPATH
        self.old_imdb_db_path = config.IMDB_DB_PATH
        config.ABSPATH = self.temp.name
        self.db_path = Path(self.temp.name) / "imdb.duckdb"
        config.IMDB_DB_PATH = str(self.db_path)

        conn = duckdb.connect(str(self.db_path))
        conn.execute(
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
        conn.execute(
            "CREATE TABLE title_ratings (tconst VARCHAR, averageRating DOUBLE, numVotes BIGINT)"
        )
        conn.execute(
            "CREATE TABLE title_principals (tconst VARCHAR, ordering INTEGER, nconst VARCHAR, category VARCHAR)"
        )
        conn.execute(
            "CREATE TABLE title_crew (tconst VARCHAR, directors VARCHAR, writers VARCHAR)"
        )
        conn.execute(
            "CREATE TABLE title_writers (tconst VARCHAR, nconst VARCHAR)"
        )

        conn.executemany(
            "INSERT INTO title_basics VALUES (?, ?, ?, ?, ?, ?)",
            [
                ("tt0000001", "movie", "Past Drama", "2020", "100", "Drama"),
                ("tt0000002", "movie", "Past Comedy", "2022", "100", "Comedy"),
                ("tt0000003", "movie", "Target", "2024", "120", "Drama"),
            ],
        )
        conn.executemany(
            "INSERT INTO title_ratings VALUES (?, ?, ?)",
            [
                ("tt0000001", 8.0, 1000),
                ("tt0000002", 4.0, 1000),
                ("tt0000003", 5.0, 1000),
            ],
        )
        conn.executemany(
            "INSERT INTO title_principals VALUES (?, ?, ?, ?)",
            [
                ("tt0000001", 1, "nm_team", "director"),
                ("tt0000002", 1, "nm_team", "director"),
                ("tt0000003", 1, "nm_team", "director"),
            ],
        )
        conn.executemany(
            "INSERT INTO title_crew VALUES (?, ?, ?)",
            [
                ("tt0000001", "nm_team", "nm_team"),
                ("tt0000002", "nm_team", "nm_team"),
                ("tt0000003", "nm_team", "nm_team"),
            ],
        )
        conn.executemany(
            "INSERT INTO title_writers VALUES (?, ?)",
            [
                ("tt0000001", "nm_team"),
                ("tt0000002", "nm_team"),
                ("tt0000003", "nm_team"),
            ],
        )
        conn.close()

    def tearDown(self):
        config.ABSPATH = self.old_abspath
        config.IMDB_DB_PATH = self.old_imdb_db_path
        self.temp.cleanup()

    def _target_row(self):
        for X, _y, _titles, tconsts in get_batches([], batch_size=100):
            if "tt0000003" in tconsts:
                return X.iloc[tconsts.index("tt0000003")]
        self.fail("Target row не найден")

    def test_creative_team_features_are_off_by_default(self):
        with patch.dict(
            os.environ,
            {"VANGA_TRAIN_CREATIVE_TEAM_FEATURES": "0"},
            clear=False,
        ):
            row = self._target_row()

        self.assertNotIn("director_genre_avg_rating", row.index)
        self.assertNotIn("director_recent_avg_rating", row.index)
        self.assertNotIn("director_is_writer", row.index)

    def test_candidate_v7_adds_context_features_without_future_data(self):
        with patch.dict(
            os.environ,
            {"VANGA_TRAIN_CREATIVE_TEAM_FEATURES": "1"},
            clear=False,
        ):
            row = self._target_row()

        self.assertAlmostEqual(float(row["director_genre_avg_rating"]), 8.0)
        self.assertEqual(float(row["director_genre_prior_count"]), 1.0)
        self.assertEqual(float(row["director_genre_known"]), 1.0)
        self.assertAlmostEqual(float(row["director_recent_avg_rating"]), 6.0)
        self.assertEqual(float(row["director_recent_count"]), 2.0)
        self.assertEqual(float(row["director_recent_known"]), 1.0)
        self.assertAlmostEqual(float(row["writer_genre_avg_rating"]), 8.0)
        self.assertEqual(float(row["writer_genre_prior_count"]), 1.0)
        self.assertAlmostEqual(float(row["writer_recent_avg_rating"]), 6.0)
        self.assertEqual(float(row["director_is_writer"]), 1.0)


if __name__ == "__main__":
    unittest.main()
