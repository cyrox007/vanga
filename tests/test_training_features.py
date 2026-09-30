from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import duckdb

from settings import config
from src.data_filtr import get_batches


class TrainingFeatureContractTests(unittest.TestCase):
    def test_features_use_only_past_films_and_keep_actor_rank(self):
        old_abspath = config.ABSPATH
        old_imdb_db_path = config.IMDB_DB_PATH
        with tempfile.TemporaryDirectory() as tmp:
            config.ABSPATH = tmp
            db_path = Path(tmp) / "imdb.duckdb"
            config.IMDB_DB_PATH = str(db_path)
            try:
                conn = duckdb.connect(str(db_path))
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
                    """
                    CREATE TABLE title_ratings (
                        tconst VARCHAR,
                        averageRating DOUBLE,
                        numVotes BIGINT
                    )
                    """
                )
                conn.execute(
                    """
                    CREATE TABLE title_principals (
                        tconst VARCHAR,
                        ordering INTEGER,
                        nconst VARCHAR,
                        category VARCHAR
                    )
                    """
                )

                conn.executemany(
                    "INSERT INTO title_basics VALUES (?, ?, ?, ?, ?, ?)",
                    [
                        ("tt0000001", "movie", "Past", "2020", "100", "Drama"),
                        ("tt0000002", "movie", "Target", "2024", "120", "Drama"),
                        ("tt0000003", "movie", "Future", "2025", "130", "Drama"),
                    ],
                )
                conn.executemany(
                    "INSERT INTO title_ratings VALUES (?, ?, ?)",
                    [
                        ("tt0000001", 8.0, 1000),
                        ("tt0000002", 2.0, 999999),
                        ("tt0000003", 10.0, 2000),
                    ],
                )

                principals = []
                for tconst in ("tt0000001", "tt0000002", "tt0000003"):
                    principals.append((tconst, 1, "nm_director", "director"))

                # На target актёры имеют ordering 2/4/6: старый код терял 2-го и 3-го.
                principals.extend(
                    [
                        ("tt0000001", 2, "nm_actor1", "actor"),
                        ("tt0000001", 3, "nm_actor2", "actor"),
                        ("tt0000001", 4, "nm_actor3", "actress"),
                        ("tt0000002", 2, "nm_actor1", "actor"),
                        ("tt0000002", 4, "nm_actor2", "actor"),
                        ("tt0000002", 6, "nm_actor3", "actress"),
                        ("tt0000003", 2, "nm_actor1", "actor"),
                        ("tt0000003", 4, "nm_actor2", "actor"),
                        ("tt0000003", 6, "nm_actor3", "actress"),
                    ]
                )
                conn.executemany(
                    "INSERT INTO title_principals VALUES (?, ?, ?, ?)",
                    principals,
                )
                conn.close()

                target_X = None
                for X, _y, _titles, tconsts in get_batches([], batch_size=100):
                    if "tt0000002" in tconsts:
                        idx = tconsts.index("tt0000002")
                        target_X = X.iloc[idx]
                        break

                self.assertIsNotNone(target_X)
                self.assertAlmostEqual(float(target_X["startYear"]), 1.24, places=5)
                self.assertAlmostEqual(float(target_X["runtimeMinutes"]), 1.20, places=5)
                self.assertNotIn("numVotes_log", target_X.index)

                # Рейтинг target=2.0 и future=10.0 не должны влиять на history.
                self.assertAlmostEqual(
                    float(target_X["director_avg_rating"]),
                    8.0,
                    places=5,
                )
                self.assertAlmostEqual(
                    float(target_X["actor_1_avg_rating"]),
                    8.0,
                    places=5,
                )

                self.assertEqual(target_X["actor_1_id"], "nm_actor1")
                self.assertEqual(target_X["actor_2_id"], "nm_actor2")
                self.assertEqual(target_X["actor_3_id"], "nm_actor3")
            finally:
                config.ABSPATH = old_abspath
                config.IMDB_DB_PATH = old_imdb_db_path


if __name__ == "__main__":
    unittest.main()
