from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import duckdb

from src.pre_release_analysis import analyze_synopsis, build_pre_release_profile


class PreReleaseAnalysisTests(unittest.TestCase):
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
                startYear VARCHAR
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
                nconst VARCHAR,
                category VARCHAR
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
            "INSERT INTO title_basics VALUES (?, ?, ?, ?)",
            [
                ("tt1", "movie", "Past One", "2020"),
                ("tt2", "movie", "Past Two", "2022"),
                ("tt3", "movie", "Future", "2027"),
            ],
        )
        self.conn.executemany(
            "INSERT INTO title_ratings VALUES (?, ?, ?)",
            [
                ("tt1", 8.0, 10000),
                ("tt2", 7.0, 5000),
                ("tt3", 9.9, 1),
            ],
        )
        self.conn.executemany(
            "INSERT INTO name_basics VALUES (?, ?)",
            [
                ("nm_d", "Director Known"),
                ("nm_w", "Writer Known"),
                ("nm_a", "Actor Known"),
            ],
        )
        self.conn.executemany(
            "INSERT INTO title_principals VALUES (?, ?, ?)",
            [
                ("tt1", "nm_d", "director"),
                ("tt2", "nm_d", "director"),
                ("tt3", "nm_d", "director"),
                ("tt1", "nm_a", "actor"),
                ("tt2", "nm_a", "actor"),
            ],
        )
        self.conn.executemany(
            "INSERT INTO title_writers VALUES (?, ?)",
            [
                ("tt1", "nm_w"),
                ("tt2", "nm_w"),
                ("tt3", "nm_w"),
            ],
        )

    def tearDown(self):
        self.conn.close()
        self.temp.cleanup()

    def test_profile_exposes_history_counts_and_does_not_use_future_movies(self):
        profile = build_pre_release_profile(
            conn=self.conn,
            year=2025,
            runtime=140,
            director="Director Known",
            writer="Writer Known",
            actors=["Actor Known", "Unknown Actor"],
            synopsis=(
                "Герой должен спасти королевство. Империя угрожает миру, "
                "а древняя магия меняет отношения внутри его семьи."
            ),
            rating=7.2,
            uncertainty={"lower": 6.1, "upper": 8.3},
            contributions={
                "director_avg_rating": 0.4,
                "writer_avg_rating": 0.3,
                "actor_1_avg_rating": 0.1,
                "genres_combined": -0.2,
            },
        )

        coverage = profile["data_coverage"]
        self.assertEqual(coverage["director"]["works_count"], 2)
        self.assertEqual(coverage["writer"]["works_count"], 2)
        self.assertEqual(coverage["actors"][0]["works_count"], 2)
        self.assertFalse(coverage["actors"][1]["known"])
        self.assertGreaterEqual(coverage["percent"], 60)

        self.assertEqual(profile["potential"]["level"], "high_upside")
        self.assertEqual(profile["potential"]["range_upper"], 8.3)
        self.assertEqual(profile["likely_strengths"][0]["key"], "direction")
        self.assertEqual(profile["likely_weaknesses"][0]["key"], "genre")
        self.assertFalse(profile["validated_target"])

    def test_low_coverage_is_reported_as_uncertain(self):
        profile = build_pre_release_profile(
            conn=self.conn,
            year=2025,
            runtime=100,
            director="Unknown Director",
            writer=None,
            actors=[],
            synopsis=None,
            rating=6.6,
            uncertainty={"lower": 5.5, "upper": 7.7},
            contributions={"director_avg_rating": 0.01},
        )

        self.assertEqual(profile["data_coverage"]["level"], "low")
        self.assertEqual(profile["potential"]["level"], "uncertain")
        self.assertTrue(profile["data_coverage"]["warnings"])

    def test_synopsis_flags_worldbuilding_compression_only_as_heuristic(self):
        synopsis = (
            "В далёкой галактике рушится империя. Герои путешествуют между "
            "планетами, пока магия древнего мира пробуждает новую фракцию и "
            "угрожает всей вселенной. Им предстоит бороться за выживание."
        )
        analysis = analyze_synopsis(synopsis, runtime=95)

        self.assertTrue(analysis["available"])
        self.assertGreaterEqual(analysis["worldbuilding_signals"], 3)
        kinds = {item["kind"] for item in analysis["risk_signals"]}
        self.assertIn("worldbuilding_compression", kinds)
        self.assertEqual(analysis["method"], "lexical_structure_v1")
        self.assertIn("эвристикой", analysis["note"])


if __name__ == "__main__":
    unittest.main()
