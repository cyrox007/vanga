from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import duckdb

from src.creative_team_features import load_person_context, load_training_context


class CreativeTeamContextTests(unittest.TestCase):
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
            CREATE TABLE title_crew (
                tconst VARCHAR,
                directors VARCHAR,
                writers VARCHAR
            )
            """
        )
        self.conn.execute(
            "CREATE TABLE title_writers (tconst VARCHAR, nconst VARCHAR)"
        )

        movies = [
            ("tt_d1", "movie", "D1", "2019", "100", "Drama,Sci-Fi"),
            ("tt_d2", "movie", "D2", "2020", "100", "Comedy"),
            ("tt_d3", "movie", "D3", "2021", "100", "Drama"),
            ("tt_d4", "movie", "D4", "2022", "100", "Action"),
            ("tt_d5", "movie", "D5", "2023", "100", "Sci-Fi"),
            ("tt_d6", "movie", "D6", "2024", "100", "Drama"),
            ("tt_w1", "movie", "W1", "2020", "100", "Drama"),
            ("tt_w2", "movie", "W2", "2023", "100", "Comedy"),
            ("tt_target", "movie", "Target", "2025", "120", "Drama,Sci-Fi"),
            ("tt_same", "movie", "Same", "2025", "120", "Drama"),
            ("tt_future", "movie", "Future", "2026", "120", "Drama"),
        ]
        self.conn.executemany(
            "INSERT INTO title_basics VALUES (?, ?, ?, ?, ?, ?)", movies
        )
        ratings = [
            ("tt_d1", 10.0, 1000),
            ("tt_d2", 2.0, 1000),
            ("tt_d3", 8.0, 1000),
            ("tt_d4", 4.0, 1000),
            ("tt_d5", 6.0, 1000),
            ("tt_d6", 7.0, 1000),
            ("tt_w1", 9.0, 1000),
            ("tt_w2", 3.0, 1000),
            ("tt_target", 5.0, 1000),
            ("tt_same", 5.0, 1000),
            ("tt_future", 1.0, 1000),
        ]
        self.conn.executemany(
            "INSERT INTO title_ratings VALUES (?, ?, ?)", ratings
        )

        director_titles = [
            "tt_d1", "tt_d2", "tt_d3", "tt_d4", "tt_d5", "tt_d6",
            "tt_target", "tt_same", "tt_future",
        ]
        self.conn.executemany(
            "INSERT INTO title_principals VALUES (?, ?, ?, ?)",
            [(tconst, 1, "nm_director", "director") for tconst in director_titles],
        )

        self.conn.executemany(
            "INSERT INTO title_crew VALUES (?, ?, ?)",
            [
                ("tt_target", "nm_director", "nm_writer"),
                ("tt_same", "nm_director", "nm_director"),
            ],
        )
        self.conn.executemany(
            "INSERT INTO title_writers VALUES (?, ?)",
            [
                ("tt_w1", "nm_writer"),
                ("tt_w2", "nm_writer"),
                ("tt_target", "nm_writer"),
                ("tt_same", "nm_director"),
                ("tt_future", "nm_writer"),
            ],
        )

    def tearDown(self):
        self.conn.close()
        self.temp.cleanup()

    def test_training_context_uses_same_genre_once_and_excludes_future(self):
        frame = load_training_context(self.conn, ["tt_target"])
        row = frame.iloc[0]

        # Совпали d1, d3, d5, d6. d1 имеет два совпавших жанра, но считается один раз.
        self.assertEqual(int(row["director_genre_prior_count"]), 4)
        self.assertAlmostEqual(
            float(row["director_genre_avg_rating"]),
            (10.0 + 8.0 + 6.0 + 7.0) / 4.0,
            places=5,
        )
        # Recent = последние пять прошлых работ: 2024..2020, 2019 исключается.
        self.assertEqual(int(row["director_recent_count"]), 5)
        self.assertAlmostEqual(
            float(row["director_recent_avg_rating"]),
            (7.0 + 6.0 + 4.0 + 8.0 + 2.0) / 5.0,
            places=5,
        )

        # Future 2026 с рейтингом 1.0 не должен попасть ни в одну историю.
        self.assertEqual(int(row["writer_genre_prior_count"]), 1)
        self.assertAlmostEqual(float(row["writer_genre_avg_rating"]), 9.0)
        self.assertEqual(int(row["writer_recent_count"]), 2)
        self.assertAlmostEqual(float(row["writer_recent_avg_rating"]), 6.0)
        self.assertEqual(int(row["director_is_writer"]), 0)

    def test_training_context_detects_director_is_writer(self):
        frame = load_training_context(self.conn, ["tt_same"])
        self.assertEqual(int(frame.iloc[0]["director_is_writer"]), 1)

    def test_inference_context_matches_training_definition(self):
        context = load_person_context(
            self.conn,
            nconst="nm_director",
            role="director",
            before_year=2025,
            genres=["Drama", "Sci-Fi"],
        )

        self.assertEqual(context["genre_prior_count"], 4)
        self.assertAlmostEqual(
            context["genre_avg_rating"],
            (10.0 + 8.0 + 6.0 + 7.0) / 4.0,
            places=5,
        )
        self.assertEqual(context["recent_count"], 5)
        self.assertAlmostEqual(
            context["recent_avg_rating"],
            (7.0 + 6.0 + 4.0 + 8.0 + 2.0) / 5.0,
            places=5,
        )
        self.assertEqual(context["genre_known"], 1.0)
        self.assertEqual(context["recent_known"], 1.0)

    def test_missing_person_context_is_explicit(self):
        context = load_person_context(
            self.conn,
            nconst=None,
            role="writer",
            before_year=2025,
            genres="Drama",
        )
        self.assertEqual(context["genre_avg_rating"], 6.5)
        self.assertEqual(context["genre_prior_count"], 0)
        self.assertEqual(context["genre_known"], 0.0)
        self.assertEqual(context["recent_avg_rating"], 6.5)
        self.assertEqual(context["recent_count"], 0)
        self.assertEqual(context["recent_known"], 0.0)


if __name__ == "__main__":
    unittest.main()
