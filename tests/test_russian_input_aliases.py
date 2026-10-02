from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import duckdb

from src.input_aliases import RussianInputResolver
from src.normalize import normalize_genre_str


class RussianInputAliasTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp.name) / "imdb.duckdb"
        self.conn = duckdb.connect(str(self.db_path))
        self.conn.execute(
            """
            CREATE TABLE title_basics (
                tconst VARCHAR,
                primaryTitle VARCHAR,
                startYear VARCHAR
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
                nconst VARCHAR,
                category VARCHAR
            )
            """
        )
        self.conn.executemany(
            "INSERT INTO title_basics VALUES (?, ?, ?)",
            [
                ("tt0816692", "Interstellar", "2014"),
                ("tt9999999", "Interstellar Legacy", "1999"),
            ],
        )
        self.conn.executemany(
            "INSERT INTO name_basics VALUES (?, ?)",
            [
                ("nm0634240", "Christopher Nolan"),
                ("nm0000138", "Leonardo DiCaprio"),
            ],
        )
        self.conn.executemany(
            "INSERT INTO title_principals VALUES (?, ?, ?)",
            [
                ("tt0816692", "nm0634240", "director"),
                ("tt0816692", "nm0000138", "actor"),
            ],
        )
        self.resolver = RussianInputResolver(self.conn)

    def tearDown(self):
        self.conn.close()
        self.temp.cleanup()

    def test_resolves_russian_title_director_and_actor_to_local_imdb(self):
        matches = {
            "интерстеллар": ["tt9999999", "tt0816692"],
            "кристофер нолан": ["nm0634240"],
            "леонардо ди каприо": ["nm0000138"],
        }
        with patch.object(self.resolver, "_query_wikidata", return_value=matches):
            resolved = self.resolver.resolve_inputs(
                title="Интерстеллар",
                director="Кристофер Нолан",
                actors=["Леонардо Ди Каприо"],
                year=2014,
            )

        self.assertEqual(resolved["title"], "Interstellar")
        self.assertEqual(resolved["director"], "Christopher Nolan")
        self.assertEqual(resolved["actors"], ["Leonardo DiCaprio"])
        self.assertEqual(
            resolved["matches"]["title"]["imdb_id"],
            "tt0816692",
        )
        self.assertEqual(
            resolved["matches"]["director"]["imdb_id"],
            "nm0634240",
        )

    def test_latin_input_skips_remote_resolution(self):
        with patch.object(self.resolver, "_query_wikidata") as lookup:
            resolved = self.resolver.resolve_inputs(
                title="Interstellar",
                director="Christopher Nolan",
                actors=["Leonardo DiCaprio"],
                year=2014,
            )

        lookup.assert_not_called()
        self.assertEqual(resolved["title"], "Interstellar")
        self.assertEqual(resolved["director"], "Christopher Nolan")
        self.assertEqual(resolved["actors"], ["Leonardo DiCaprio"])

    def test_wikidata_failure_falls_back_to_original_russian_input(self):
        with patch.object(
            self.resolver,
            "_query_wikidata",
            side_effect=RuntimeError("network down"),
        ):
            resolved = self.resolver.resolve_inputs(
                title="Интерстеллар",
                director="Кристофер Нолан",
                actors=["Леонардо Ди Каприо"],
                year=2014,
            )

        self.assertEqual(resolved["title"], "Интерстеллар")
        self.assertEqual(resolved["director"], "Кристофер Нолан")
        self.assertEqual(resolved["actors"], ["Леонардо Ди Каприо"])

    def test_russian_genres_are_normalized_to_imdb_values(self):
        self.assertEqual(
            normalize_genre_str("фантастика, драма, приключения"),
            "Sci-Fi,Drama,Adventure",
        )


if __name__ == "__main__":
    unittest.main()
