from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import duckdb

from src.pre_release_analysis import build_pre_release_profile


class PreReleaseOldDatabaseCompatibilityTests(unittest.TestCase):
    def test_missing_title_writers_does_not_break_profile(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "legacy.duckdb"
            conn = duckdb.connect(str(db_path))
            try:
                conn.execute(
                    """
                    CREATE TABLE title_basics (
                        tconst VARCHAR,
                        titleType VARCHAR,
                        primaryTitle VARCHAR,
                        startYear VARCHAR
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
                        nconst VARCHAR,
                        category VARCHAR
                    )
                    """
                )
                conn.execute(
                    """
                    CREATE TABLE name_basics (
                        nconst VARCHAR,
                        primaryName VARCHAR
                    )
                    """
                )
                conn.execute(
                    "INSERT INTO title_basics VALUES ('tt1', 'movie', 'Past', '2020')"
                )
                conn.execute(
                    "INSERT INTO title_ratings VALUES ('tt1', 7.4, 1000)"
                )
                conn.execute(
                    "INSERT INTO name_basics VALUES ('nm_d', 'Known Director')"
                )
                conn.execute(
                    "INSERT INTO title_principals VALUES ('tt1', 'nm_d', 'director')"
                )

                profile = build_pre_release_profile(
                    conn=conn,
                    year=2026,
                    runtime=120,
                    director="Known Director",
                    writer="Writer Missing In Legacy Schema",
                    actors=[],
                    synopsis="Герой пытается спасти город от большой угрозы.",
                    rating=6.9,
                    uncertainty={"lower": 5.8, "upper": 8.0},
                    contributions={"director_avg_rating": 0.2},
                )
            finally:
                conn.close()

        writer = profile["data_coverage"]["writer"]
        self.assertEqual(writer["works_count"], 0)
        self.assertFalse(writer["known"])
        self.assertTrue(profile["data_coverage"]["warnings"])
        self.assertIn("potential", profile)


if __name__ == "__main__":
    unittest.main()
