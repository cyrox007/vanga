from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import duckdb

from src.production_wikidata import (
    ProductionCandidate,
    ProductionWikidataCache,
    ProductionWikidataClient,
    enrich_production_batch,
)


class FakeWikimediaClient:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def _request_json(self, method: str, url: str, **kwargs) -> dict:
        query = kwargs.get("data", {}).get("query", "")
        self.calls.append({"method": method, "url": url, "query": query})
        if "SELECT ?film ?relation ?entity" in query:
            return {
                "results": {
                    "bindings": [
                        {
                            "film": {"value": "http://www.wikidata.org/entity/Q1"},
                            "relation": {"value": "production_companies"},
                            "entity": {"value": "http://www.wikidata.org/entity/Q10"},
                        },
                        {
                            "film": {"value": "http://www.wikidata.org/entity/Q1"},
                            "relation": {"value": "producers"},
                            "entity": {"value": "http://www.wikidata.org/entity/Q20"},
                        },
                        {
                            "film": {"value": "http://www.wikidata.org/entity/Q1"},
                            "relation": {"value": "series"},
                            "entity": {"value": "http://www.wikidata.org/entity/Q30"},
                        },
                        # Дубликат relation не должен дублировать canonical entity.
                        {
                            "film": {"value": "http://www.wikidata.org/entity/Q1"},
                            "relation": {"value": "production_companies"},
                            "entity": {"value": "http://www.wikidata.org/entity/Q10"},
                        },
                    ]
                }
            }
        if "SELECT ?item ?labelEn ?labelRu" in query:
            return {
                "results": {
                    "bindings": [
                        {
                            "item": {"value": "http://www.wikidata.org/entity/Q10"},
                            "labelEn": {"value": "Studio Ten"},
                            "labelRu": {"value": "Студия Десять"},
                        },
                        {
                            "item": {"value": "http://www.wikidata.org/entity/Q20"},
                            "labelEn": {"value": "Producer Twenty"},
                        },
                        {
                            "item": {"value": "http://www.wikidata.org/entity/Q30"},
                            "labelEn": {"value": "Series Thirty"},
                            "labelRu": {"value": "Серия Тридцать"},
                        },
                    ]
                }
            }
        raise AssertionError(f"Неожиданный SPARQL query: {query}")


class ProductionWikidataTests(unittest.TestCase):
    def test_fetch_batch_collects_company_producer_series_and_labels(self):
        fake = FakeWikimediaClient()
        client = ProductionWikidataClient(fake)  # type: ignore[arg-type]

        result = client.fetch_batch(["Q1"])
        self.assertEqual(
            result["Q1"]["production_companies"],
            [{"qid": "Q10", "label_en": "Studio Ten", "label_ru": "Студия Десять"}],
        )
        self.assertEqual(
            result["Q1"]["producers"],
            [{"qid": "Q20", "label_en": "Producer Twenty", "label_ru": None}],
        )
        self.assertEqual(
            result["Q1"]["series"],
            [{"qid": "Q30", "label_en": "Series Thirty", "label_ru": "Серия Тридцать"}],
        )
        self.assertEqual(len(fake.calls), 2)

    def test_cache_selects_only_uncached_ok_films_and_retry_errors(self):
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
            conn.execute(
                "INSERT INTO film_enrichment(imdb_id, imdb_title, wikidata_id, wikidata_json, status, fetched_at) VALUES ('tt0001','A','Q1','{}','ok', current_timestamp)"
            )
            conn.execute(
                "INSERT INTO film_enrichment(imdb_id, imdb_title, wikidata_id, wikidata_json, status, fetched_at) VALUES ('tt0002','B','Q2','{}','error', current_timestamp)"
            )
            conn.execute(
                "INSERT INTO film_enrichment(imdb_id, imdb_title, wikidata_id, wikidata_json, status, fetched_at) VALUES ('tt0003','C','Q3','{}','ok', current_timestamp)"
            )
            conn.close()

            cache = ProductionWikidataCache(path)
            try:
                cache.upsert(
                    ProductionCandidate("tt0003", "Q3"),
                    metadata={},
                    status="error",
                    error="test",
                )
                normal = cache.load_candidates(after_imdb="", limit=10)
                self.assertEqual([(x.imdb_id, x.wikidata_id) for x in normal], [("tt0001", "Q1")])

                retry = cache.load_candidates(after_imdb="", limit=10, retry_errors=True)
                self.assertEqual([(x.imdb_id, x.wikidata_id) for x in retry], [("tt0003", "Q3")])
            finally:
                cache.close()

    def test_enrich_batch_persists_success(self):
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
            conn.close()

            cache = ProductionWikidataCache(path)
            try:
                client = ProductionWikidataClient(FakeWikimediaClient())  # type: ignore[arg-type]
                ok, failed = enrich_production_batch(
                    [ProductionCandidate("tt0001", "Q1")],
                    client=client,
                    cache=cache,
                )
                self.assertEqual((ok, failed), (1, 0))
                row = cache.conn.execute(
                    "SELECT status, metadata_json FROM production_wikidata WHERE imdb_id='tt0001'"
                ).fetchone()
                self.assertEqual(row[0], "ok")
                self.assertIn("Studio Ten", row[1])
                self.assertIn("Producer Twenty", row[1])
            finally:
                cache.close()


if __name__ == "__main__":
    unittest.main()
