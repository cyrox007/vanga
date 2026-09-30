from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import duckdb

from src.wikimedia_enrichment import (
    EnrichmentStore,
    MovieCandidate,
    WikimediaClient,
    WikidataMatch,
    load_candidates,
)


class WikimediaParsingTests(unittest.TestCase):
    def test_page_title_from_url(self):
        self.assertEqual(
            WikimediaClient.page_title_from_url(
                "https://en.wikipedia.org/wiki/Blade_Runner_2049"
            ),
            "Blade Runner 2049",
        )
        self.assertIsNone(WikimediaClient.page_title_from_url(None))

    def test_find_plot_section_english_and_russian(self):
        en = [
            {"line": "Production", "index": "1"},
            {"line": "Plot", "index": "2"},
        ]
        ru = [
            {"line": "В ролях", "index": "1"},
            {"line": "Сюжет", "index": "3"},
        ]
        self.assertEqual(WikimediaClient._find_plot_section("en", en), "2")
        self.assertEqual(WikimediaClient._find_plot_section("ru", ru), "3")

    def test_clean_wikipedia_html_removes_references(self):
        html = """
        <div>
          <p>Первая часть сюжета<sup class="reference">[1]</sup>.</p>
          <table><tr><td>служебное</td></tr></table>
          <p>Вторая часть.</p>
        </div>
        """
        cleaned = WikimediaClient._clean_wikipedia_html(html)
        self.assertIn("Первая часть сюжета", cleaned)
        self.assertIn("Вторая часть", cleaned)
        self.assertNotIn("[1]", cleaned)
        self.assertNotIn("служебное", cleaned)


class EnrichmentStoreTests(unittest.TestCase):
    def test_upsert_and_retry_queue(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = EnrichmentStore(Path(tmp) / "enrichment.duckdb")
            try:
                movie = MovieCandidate("tt1234567", "Тест", 2026)
                match = WikidataMatch(
                    imdb_id=movie.imdb_id,
                    qid="Q123",
                    enwiki_url="https://en.wikipedia.org/wiki/Test",
                    ruwiki_url=None,
                )
                store.upsert(
                    movie,
                    match=match,
                    structured={"directors": ["Q1"]},
                    en_plot=None,
                    ru_plot=None,
                    status="error",
                    error="временная ошибка",
                )
                retry = store.load_retry_candidates(10)
                self.assertEqual(retry, [movie])

                row = store.conn.execute(
                    "SELECT wikidata_id, wikidata_json, status FROM film_enrichment"
                ).fetchone()
                self.assertEqual(row[0], "Q123")
                self.assertEqual(json.loads(row[1])["directors"], ["Q1"])
                self.assertEqual(row[2], "error")
            finally:
                store.close()

    def test_cursor_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = EnrichmentStore(Path(tmp) / "enrichment.duckdb")
            try:
                self.assertEqual(store.get_state("cursor", ""), "")
                store.set_state("cursor", "tt999")
                self.assertEqual(store.get_state("cursor", ""), "tt999")
                store.reset_state("cursor")
                self.assertEqual(store.get_state("cursor", ""), "")
            finally:
                store.close()


class CandidateLoaderTests(unittest.TestCase):
    def test_load_candidates_filters_year_and_cursor(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "imdb.duckdb"
            conn = duckdb.connect(str(db_path))
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
            conn.executemany(
                "INSERT INTO title_basics VALUES (?, ?, ?, ?)",
                [
                    ("tt0000001", "movie", "Старый", "2019"),
                    ("tt0000002", "movie", "Новый A", "2025"),
                    ("tt0000003", "short", "Не фильм", "2026"),
                    ("tt0000004", "movie", "Новый B", "2026"),
                ],
            )
            conn.close()

            rows = load_candidates(
                db_path,
                after_tconst="",
                since_year=2024,
                limit=10,
            )
            self.assertEqual(
                [row.imdb_id for row in rows],
                ["tt0000002", "tt0000004"],
            )

            rows = load_candidates(
                db_path,
                after_tconst="tt0000002",
                since_year=2024,
                limit=10,
            )
            self.assertEqual([row.imdb_id for row in rows], ["tt0000004"])


if __name__ == "__main__":
    unittest.main()
