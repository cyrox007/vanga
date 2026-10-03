from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import duckdb

from src.production_wikidata import ProductionCandidate, ProductionWikidataCache


class ProductionWikidataRefreshTests(unittest.TestCase):
    def test_refresh_known_reselects_cached_ok_films_with_own_cursor(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "enrichment.duckdb"
            conn = duckdb.connect(str(path))
            conn.execute(
                """
                CREATE TABLE film_enrichment (
                    imdb_id VARCHAR PRIMARY KEY,
                    imdb_title VARCHAR,
                    imdb_year INTEGER,
                    wikidata_id VARCHAR,
                    wikidata_json VARCHAR,
                    enwiki_title VARCHAR,
                    enwiki_revision BIGINT,
                    enwiki_plot VARCHAR,
                    ruwiki_title VARCHAR,
                    ruwiki_revision BIGINT,
                    ruwiki_plot VARCHAR,
                    status VARCHAR NOT NULL,
                    error VARCHAR,
                    fetched_at TIMESTAMP WITH TIME ZONE NOT NULL
                )
                """
            )
            for imdb_id, qid in (("tt0001", "Q1"), ("tt0002", "Q2"), ("tt0003", "Q3")):
                conn.execute(
                    """
                    INSERT INTO film_enrichment(
                        imdb_id, imdb_title, wikidata_id, wikidata_json, status, fetched_at
                    ) VALUES (?, ?, ?, '{}', 'ok', current_timestamp)
                    """,
                    [imdb_id, imdb_id, qid],
                )
            conn.close()

            cache = ProductionWikidataCache(path)
            try:
                # Две записи уже cached: обычный new-pass должен их исключить.
                for imdb_id, qid in (("tt0001", "Q1"), ("tt0002", "Q2")):
                    cache.upsert(
                        ProductionCandidate(imdb_id, qid),
                        metadata={"production_companies": []},
                        status="ok",
                    )
                normal = cache.load_candidates(after_imdb="", limit=10)
                self.assertEqual([item.imdb_id for item in normal], ["tt0003"])

                refresh = cache.load_candidates(
                    after_imdb="tt0001",
                    limit=10,
                    refresh_known=True,
                )
                self.assertEqual([item.imdb_id for item in refresh], ["tt0002", "tt0003"])

                with self.assertRaises(ValueError):
                    cache.load_candidates(
                        after_imdb="",
                        limit=10,
                        retry_errors=True,
                        refresh_known=True,
                    )
            finally:
                cache.close()


if __name__ == "__main__":
    unittest.main()
