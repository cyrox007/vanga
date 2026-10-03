from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

import duckdb

from src.production_context import ProductionContextStore
from src.production_identity import ProductionIdentityHistory
from src.production_materializer import ProductionContextMaterializer
from src.production_wikidata import ProductionCandidate, ProductionWikidataCache


class ProductionMaterializerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.enrichment_path = root / "enrichment.duckdb"
        self.production_path = root / "production_context.duckdb"

        conn = duckdb.connect(str(self.enrichment_path))
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
            """
            INSERT INTO film_enrichment(
                imdb_id, imdb_title, imdb_year, wikidata_id, wikidata_json, status, fetched_at
            ) VALUES
              ('tt0001', 'Prior Film', 2020, 'Q1', ?, 'ok', '2024-01-10T00:00:00Z'),
              ('tt0002', 'Target Film', 2025, 'Q2', ?, 'ok', '2024-02-10T00:00:00Z')
            """,
            [
                json.dumps({"release_dates": ["+2020-07-01T00:00:00Z"]}),
                json.dumps({"release_dates": ["+2025-07-01T00:00:00Z"]}),
            ],
        )
        conn.close()

        cache = ProductionWikidataCache(self.enrichment_path)
        try:
            common_company = {
                "qid": "Q100",
                "label_en": "Studio Example",
                "label_ru": "Студия Пример",
            }
            common_producer = {
                "qid": "Q200",
                "label_en": "Producer Example",
                "label_ru": "Продюсер Пример",
            }
            common_series = {
                "qid": "Q300",
                "label_en": "Example Film Series",
                "label_ru": "Серия Пример",
            }
            cache.upsert(
                ProductionCandidate("tt0001", "Q1"),
                metadata={
                    "production_companies": [common_company],
                    "producers": [common_producer],
                    "series": [common_series],
                },
                status="ok",
                fetched_at=datetime(2024, 1, 10, tzinfo=timezone.utc),
            )
            cache.upsert(
                ProductionCandidate("tt0002", "Q2"),
                metadata={
                    "production_companies": [common_company],
                    "producers": [common_producer],
                    "series": [common_series],
                },
                status="ok",
                fetched_at=datetime(2024, 2, 10, tzinfo=timezone.utc),
            )
        finally:
            cache.close()

        self.store = ProductionContextStore(self.production_path)
        self.materializer = ProductionContextMaterializer(
            enrichment_db=self.enrichment_path,
            production_store=self.store,
        )
        self.identity = ProductionIdentityHistory(self.store)

    def tearDown(self):
        self.materializer.close()
        self.store.close()
        self.temp.cleanup()

    def test_materializes_company_producer_and_series_as_franchise_only(self):
        count, totals, cursor = self.materializer.materialize_batch(after_imdb="", limit=10)
        self.assertEqual(count, 2)
        self.assertEqual(cursor, "tt0002")
        self.assertEqual(totals, {"production_companies": 2, "producers": 2, "series": 2})

        company = self.identity.resolve_entity(
            "Студия Пример",
            kind="production_company",
            cutoff="2024-06-01T00:00:00Z",
        )
        self.assertEqual(company["id"], "wikidata:Q100")
        producer = self.identity.resolve_entity(
            "Producer Example",
            kind="producer",
            cutoff="2024-06-01T00:00:00Z",
        )
        self.assertEqual(producer["id"], "wikidata:Q200")
        franchise = self.identity.resolve_group(
            "Серия Пример",
            kind="franchise",
            cutoff="2024-06-01T00:00:00Z",
        )
        self.assertEqual(franchise["id"], "wikidata:Q300")

        shared_count = self.store.conn.execute(
            "SELECT COUNT(*) FROM production_groups WHERE kind='shared_universe'"
        ).fetchone()[0]
        self.assertEqual(shared_count, 0)

    def test_known_at_is_fetch_time_not_release_time_and_history_is_temporal(self):
        self.materializer.materialize_batch(after_imdb="", limit=10)

        prior_link = self.store.conn.execute(
            "SELECT known_at FROM project_entity_links WHERE link_id='wikidata:tt0001:production_company:Q100'"
        ).fetchone()[0]
        self.assertEqual(prior_link, datetime(2024, 1, 10, tzinfo=timezone.utc))
        # Несмотря на релиз 2020, до фактического наблюдения 2024 relation неизвестна.
        early = self.identity.history_features_as_of(
            "tt0002",
            "2024-01-01T00:00:00Z",
        )
        self.assertEqual(early["production_production_company_prior_project_count_mean"], 0.0)
        self.assertEqual(early["production_franchise_prior_project_count"], 0.0)

        visible = self.identity.history_features_as_of(
            "tt0002",
            "2024-06-01T00:00:00Z",
        )
        self.assertEqual(visible["production_production_company_prior_project_count_mean"], 1.0)
        self.assertEqual(visible["production_producer_prior_project_count_mean"], 1.0)
        self.assertEqual(visible["production_franchise_prior_project_count"], 1.0)

    def test_repeated_later_sync_preserves_earliest_relation_and_alias_observation(self):
        self.materializer.materialize_batch(after_imdb="", limit=10)

        # DuckDB не разрешает одновременно read-only и read-write connections
        # к одному файлу с разной конфигурацией. Закрываем materializer перед refresh.
        self.materializer.close()
        cache = ProductionWikidataCache(self.enrichment_path)
        try:
            cache.upsert(
                ProductionCandidate("tt0001", "Q1"),
                metadata={
                    "production_companies": [
                        {
                            "qid": "Q100",
                            "label_en": "Studio Example",
                            "label_ru": "Студия Пример",
                        }
                    ],
                    "producers": [],
                    "series": [],
                },
                status="ok",
                fetched_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            )
        finally:
            cache.close()

        self.materializer = ProductionContextMaterializer(
            enrichment_db=self.enrichment_path,
            production_store=self.store,
        )
        row = self.materializer.load_rows(after_imdb="", limit=10)[0]
        self.materializer.materialize_row(row)

        link_known = self.store.conn.execute(
            "SELECT known_at FROM project_entity_links WHERE link_id='wikidata:tt0001:production_company:Q100'"
        ).fetchone()[0]
        alias_known = self.store.conn.execute(
            """
            SELECT known_at FROM production_entity_aliases
            WHERE entity_id='wikidata:Q100' AND alias='Студия Пример'
            """
        ).fetchone()[0]
        link_source = self.store.conn.execute(
            "SELECT source_id FROM project_entity_links WHERE link_id='wikidata:tt0001:production_company:Q100'"
        ).fetchone()[0]
        self.assertEqual(link_known, datetime(2024, 1, 10, tzinfo=timezone.utc))
        self.assertEqual(alias_known, datetime(2024, 1, 10, tzinfo=timezone.utc))
        self.assertEqual(link_source, "wikidata:Q1:20240110T000000Z")

    def test_same_alias_on_different_entities_does_not_leak_earlier_known_at(self):
        # Сначала материализуем Q100 alias в 2024.
        self.materializer.materialize_batch(after_imdb="", limit=1)

        row = {
            "imdb_id": "tt0002",
            "title": "Target Film",
            "year": 2025,
            "wikidata_id": "Q2",
            "wikidata": {"release_dates": ["+2025-07-01T00:00:00Z"]},
            "production": {
                "production_companies": [
                    {
                        "qid": "Q999",
                        "label_en": "Other Studio",
                        "label_ru": "Студия Пример",
                    }
                ],
                "producers": [],
                "series": [],
            },
            "fetched_at": datetime(2025, 1, 1, tzinfo=timezone.utc),
        }
        self.materializer.materialize_row(row)
        rows = self.store.conn.execute(
            """
            SELECT entity_id, known_at
            FROM production_entity_aliases
            WHERE alias='Студия Пример'
            ORDER BY entity_id
            """
        ).fetchall()
        known = {str(entity_id): timestamp for entity_id, timestamp in rows}
        self.assertEqual(known["wikidata:Q100"], datetime(2024, 1, 10, tzinfo=timezone.utc))
        self.assertEqual(known["wikidata:Q999"], datetime(2025, 1, 1, tzinfo=timezone.utc))


if __name__ == "__main__":
    unittest.main()
