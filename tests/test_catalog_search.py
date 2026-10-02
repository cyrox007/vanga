from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

import duckdb

from src.catalog import CatalogSearch


class CatalogSearchTests(unittest.TestCase):
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
                originalTitle VARCHAR,
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
                averageRating VARCHAR,
                numVotes VARCHAR
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
            CREATE TABLE title_principals (
                tconst VARCHAR,
                ordering VARCHAR,
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
        self.conn.executemany(
            "INSERT INTO title_basics VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                ("tt0816692", "movie", "Interstellar", "Interstellar", "2014", "169", "Adventure,Drama,Sci-Fi"),
                ("tt9999999", "movie", "Interstellar Legacy", "Interstellar Legacy", "2030", "140", "Sci-Fi"),
            ],
        )
        self.conn.executemany(
            "INSERT INTO title_ratings VALUES (?, ?, ?)",
            [
                ("tt0816692", "8.7", "2200000"),
                ("tt9999999", "7.1", "1234"),
            ],
        )
        self.conn.executemany(
            "INSERT INTO name_basics VALUES (?, ?)",
            [
                ("nm0634240", "Christopher Nolan"),
                ("nm0000138", "Leonardo DiCaprio"),
                ("nm0004266", "Anne Hathaway"),
                ("nm0254645", "Jonathan Nolan"),
            ],
        )
        self.conn.executemany(
            "INSERT INTO title_principals VALUES (?, ?, ?, ?)",
            [
                ("tt0816692", "1", "nm0634240", "director"),
                ("tt0816692", "2", "nm0000138", "actor"),
                ("tt0816692", "3", "nm0004266", "actress"),
            ],
        )
        self.conn.executemany(
            "INSERT INTO title_crew VALUES (?, ?, ?)",
            [
                ("tt0816692", "nm0634240", "nm0254645"),
                ("tt9999999", "\\N", "\\N"),
            ],
        )
        self.conn.execute(
            "INSERT INTO title_writers VALUES (?, ?)",
            ["tt0816692", "nm0254645"],
        )

        self.resolver = Mock()
        self.resolver.needs_resolution.side_effect = (
            lambda value: bool(value and any("А" <= ch <= "я" for ch in value))
        )
        self.catalog = CatalogSearch(self.conn, self.resolver)

    def tearDown(self):
        self.conn.close()
        self.temp.cleanup()

    def test_movie_search_returns_autofill_payload(self):
        items = self.catalog.search_movies("Interstellar", year=2014)

        self.assertEqual(items[0]["imdb_id"], "tt0816692")
        self.assertEqual(items[0]["director"], "Christopher Nolan")
        self.assertEqual(items[0]["writer"], "Jonathan Nolan")
        self.assertEqual(items[0]["runtime"], 169)
        self.assertIn("Sci-Fi", items[0]["genres"])
        self.assertEqual(
            items[0]["actors"],
            ["Leonardo DiCaprio", "Anne Hathaway"],
        )

    def test_people_search_filters_role(self):
        directors = self.catalog.search_people("Christopher", role="director")
        actors = self.catalog.search_people("Leonardo", role="actor")
        writers = self.catalog.search_people("Jonathan", role="writer")

        self.assertEqual(directors[0]["imdb_id"], "nm0634240")
        self.assertEqual(actors[0]["imdb_id"], "nm0000138")
        self.assertEqual(writers[0]["imdb_id"], "nm0254645")

    def test_current_ratings_returns_local_imdb_values_in_request_order(self):
        items = self.catalog.current_ratings(
            ["tt9999999", "bad", "tt0816692", "tt9999999"]
        )

        self.assertEqual(
            [item["imdb_id"] for item in items],
            ["tt9999999", "tt0816692"],
        )
        self.assertEqual(items[0]["rating"], 7.1)
        self.assertEqual(items[1]["num_votes"], 2200000)

    def test_short_queries_do_not_hit_database(self):
        self.assertEqual(self.catalog.search_movies("I"), [])
        self.assertEqual(self.catalog.search_people("L", role="actor"), [])


if __name__ == "__main__":
    unittest.main()
