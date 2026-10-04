from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from src.future_release_import import FutureReleaseBatchImporter
from src.wikidata_future_releases import (
    WikidataFutureReleaseCollector,
    WikidataFutureReleaseError,
)


def _cell(value):
    return {"type": "literal", "value": str(value)}


def _uri(value):
    return {"type": "uri", "value": str(value)}


class WikidataFutureReleaseCollectorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.raw = {
            "head": {"vars": ["film", "filmLabel", "imdb", "releaseStatement", "releaseDate", "precision"]},
            "results": {
                "bindings": [
                    {
                        "film": _uri("http://www.wikidata.org/entity/Q100"),
                        "filmLabel": _cell("Exact Film"),
                        "imdb": _cell("tt1234567"),
                        "releaseStatement": _uri("http://www.wikidata.org/entity/statement/Q100-AAA"),
                        "releaseDate": _cell("2027-05-20T00:00:00Z"),
                        "precision": _cell("11"),
                    },
                    {
                        "film": _uri("http://www.wikidata.org/entity/Q200"),
                        "filmLabel": _cell("Month Film"),
                        "releaseStatement": _uri("http://www.wikidata.org/entity/statement/Q200-BBB"),
                        "releaseDate": _cell("2027-08-01T00:00:00Z"),
                        "precision": _cell("10"),
                    },
                    {
                        "film": _uri("http://www.wikidata.org/entity/Q300"),
                        "filmLabel": _cell("Year Film"),
                        "releaseStatement": _uri("http://www.wikidata.org/entity/statement/Q300-CCC"),
                        "releaseDate": _cell("2028-01-01T00:00:00Z"),
                        "precision": _cell("9"),
                    },
                    {
                        "film": _uri("http://www.wikidata.org/entity/Q400"),
                        "filmLabel": _cell("Century Film"),
                        "releaseStatement": _uri("http://www.wikidata.org/entity/statement/Q400-DDD"),
                        "releaseDate": _cell("2100-01-01T00:00:00Z"),
                        "precision": _cell("7"),
                    },
                ]
            },
        }
        self.queries = []

        def transport(query):
            self.queries.append(query)
            return self.raw

        self.collector = WikidataFutureReleaseCollector(
            cache_dir=self.root / "cache",
            transport=transport,
        )
        self.retrieved = datetime(2026, 10, 4, 8, 0, tzinfo=timezone.utc)

    def tearDown(self):
        self.tmp.cleanup()

    def test_query_uses_statement_value_and_time_precision(self):
        query = self.collector.build_query(
            datetime(2026, 10, 4, tzinfo=timezone.utc),
            datetime(2029, 1, 1, tzinfo=timezone.utc),
            limit=100,
        )
        self.assertIn("p:P577 ?releaseStatement", query)
        self.assertIn("psv:P577 ?releaseValue", query)
        self.assertIn("wikibase:timePrecision ?precision", query)
        self.assertIn("wikibase:DeprecatedRank", query)
        self.assertIn("LIMIT 100", query)

    def test_normalize_preserves_day_month_year_precision(self):
        bundle, warnings = self.collector.normalize(
            self.raw,
            retrieved_at=self.retrieved,
        )
        self.assertEqual(len(bundle["projects"]), 3)
        windows = {
            item["project_id"]: item for item in bundle["release_windows"]
        }
        exact = windows["wikidata:Q100"]
        self.assertEqual(exact["precision"], "exact")
        self.assertEqual(exact["release_start_at"], "2027-05-20T00:00:00+00:00")
        self.assertEqual(exact["release_start_at"], exact["release_end_at"])

        month = windows["wikidata:Q200"]
        self.assertEqual(month["precision"], "month")
        self.assertEqual(month["release_start_at"], "2027-08-01T00:00:00+00:00")
        self.assertEqual(month["release_end_at"], "2027-08-31T23:59:59+00:00")

        year = windows["wikidata:Q300"]
        self.assertEqual(year["precision"], "year")
        self.assertEqual(year["release_start_at"], "2028-01-01T00:00:00+00:00")
        self.assertEqual(year["release_end_at"], "2028-12-31T23:59:59+00:00")

        self.assertEqual(len(warnings), 1)
        self.assertEqual(warnings[0]["qid"], "Q400")
        self.assertEqual(warnings[0]["reason"], "unsupported_time_precision")

    def test_collect_writes_raw_cache_and_returns_importable_batch(self):
        result = self.collector.collect(
            "2026-10-04T00:00:00Z",
            "2029-01-01T00:00:00Z",
            retrieved_at=self.retrieved,
        )
        self.assertEqual(len(self.queries), 1)
        self.assertEqual(result["project_count"], 3)
        self.assertEqual(result["release_observation_count"], 3)
        self.assertEqual(result["warning_count"], 1)
        cache = Path(result["raw_cache_path"])
        self.assertTrue(cache.exists())

        db = self.root / "future.duckdb"
        with FutureReleaseBatchImporter(db) as importer:
            imported = importer.import_batch(result["batch"])
            self.assertFalse(imported["idempotent"])
            snapshot = importer.store.snapshot_as_of(
                "wikidata:Q100", self.retrieved
            )
            self.assertEqual(snapshot["release_at"], "2027-05-20T00:00:00+00:00")
            month = importer.store.snapshot_as_of("wikidata:Q200", self.retrieved)
            self.assertIsNone(month["release_at"])
            self.assertEqual(month["release_window"]["precision"], "month")

    def test_same_raw_and_retrieval_time_reuses_cache_and_batch_fingerprint(self):
        first = self.collector.collect(
            "2026-10-04T00:00:00Z",
            "2029-01-01T00:00:00Z",
            retrieved_at=self.retrieved,
        )
        second = self.collector.collect(
            "2026-10-04T00:00:00Z",
            "2029-01-01T00:00:00Z",
            retrieved_at=self.retrieved,
        )
        self.assertEqual(first["raw_cache_path"], second["raw_cache_path"])
        self.assertEqual(
            first["batch"]["source_fingerprint_sha256"],
            second["batch"]["source_fingerprint_sha256"],
        )
        self.assertEqual(first["batch"]["batch_id"], second["batch"]["batch_id"])

    def test_different_statements_are_distinct_provenance_sources(self):
        raw = {
            "results": {
                "bindings": [
                    {
                        "film": _uri("http://www.wikidata.org/entity/Q100"),
                        "filmLabel": _cell("Exact Film"),
                        "releaseStatement": _uri("http://www.wikidata.org/entity/statement/Q100-A"),
                        "releaseDate": _cell("2027-05-20T00:00:00Z"),
                        "precision": _cell("11"),
                    },
                    {
                        "film": _uri("http://www.wikidata.org/entity/Q100"),
                        "filmLabel": _cell("Exact Film"),
                        "releaseStatement": _uri("http://www.wikidata.org/entity/statement/Q100-B"),
                        "releaseDate": _cell("2027-06-20T00:00:00Z"),
                        "precision": _cell("11"),
                    },
                ]
            }
        }
        bundle, warnings = self.collector.normalize(raw, retrieved_at=self.retrieved)
        self.assertEqual(warnings, [])
        self.assertEqual(len(bundle["projects"]), 1)
        self.assertEqual(len(bundle["sources"]), 2)
        self.assertEqual(len(bundle["release_windows"]), 2)

    def test_invalid_range_and_past_only_collection_are_rejected(self):
        with self.assertRaises(WikidataFutureReleaseError):
            self.collector.collect(
                "2028-01-01T00:00:00Z",
                "2027-01-01T00:00:00Z",
                retrieved_at=self.retrieved,
            )
        with self.assertRaises(WikidataFutureReleaseError):
            self.collector.collect(
                "2025-01-01T00:00:00Z",
                "2026-01-01T00:00:00Z",
                retrieved_at=self.retrieved,
            )


if __name__ == "__main__":
    unittest.main()
