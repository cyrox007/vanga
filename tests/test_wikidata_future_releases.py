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
            "head": {
                "vars": [
                    "film",
                    "filmLabelEn",
                    "filmLabelRu",
                    "imdb",
                    "releaseStatement",
                    "releaseDate",
                    "precision",
                    "territory",
                ]
            },
            "results": {
                "bindings": [
                    {
                        "film": _uri("http://www.wikidata.org/entity/Q100"),
                        "filmLabelEn": _cell("Exact Film"),
                        "imdb": _cell("tt1234567"),
                        "releaseStatement": _uri(
                            "http://www.wikidata.org/entity/statement/Q100-AAA"
                        ),
                        "releaseDate": _cell("2027-05-20T00:00:00Z"),
                        "precision": _cell("11"),
                        "territory": _uri("http://www.wikidata.org/entity/Q30"),
                    },
                    {
                        "film": _uri("http://www.wikidata.org/entity/Q200"),
                        "filmLabelEn": _cell("Month Film"),
                        "releaseStatement": _uri(
                            "http://www.wikidata.org/entity/statement/Q200-BBB"
                        ),
                        "releaseDate": _cell("2027-08-01T00:00:00Z"),
                        "precision": _cell("10"),
                        "territory": _uri("http://www.wikidata.org/entity/Q145"),
                    },
                    {
                        "film": _uri("http://www.wikidata.org/entity/Q300"),
                        "filmLabelRu": _cell("Фильм с годом"),
                        "releaseStatement": _uri(
                            "http://www.wikidata.org/entity/statement/Q300-CCC"
                        ),
                        "releaseDate": _cell("2028-01-01T00:00:00Z"),
                        "precision": _cell("9"),
                    },
                    {
                        "film": _uri("http://www.wikidata.org/entity/Q400"),
                        "filmLabelEn": _cell("Century Film"),
                        "releaseStatement": _uri(
                            "http://www.wikidata.org/entity/statement/Q400-DDD"
                        ),
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

    def test_query_uses_statement_precision_territory_and_plain_labels(self):
        query = self.collector.build_query(
            datetime(2026, 10, 4, tzinfo=timezone.utc),
            datetime(2029, 1, 1, tzinfo=timezone.utc),
            limit=100,
        )
        self.assertIn("p:P577 ?releaseStatement", query)
        self.assertIn("psv:P577 ?releaseValue", query)
        self.assertIn("wikibase:timePrecision ?precision", query)
        self.assertIn("pq:P291 ?territory", query)
        self.assertIn("rdfs:label", query)
        self.assertIn("wikibase:DeprecatedRank", query)
        self.assertNotIn("SERVICE wikibase:label", query)
        self.assertNotIn("bigdata.com", query)
        self.assertIn("LIMIT 100", query)

    def test_normalize_preserves_precision_and_territory(self):
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
        self.assertEqual(exact["territory"], "wikidata:Q30")
        self.assertEqual(exact["release_start_at"], "2027-05-20T00:00:00+00:00")
        self.assertEqual(exact["release_start_at"], exact["release_end_at"])

        month = windows["wikidata:Q200"]
        self.assertEqual(month["precision"], "month")
        self.assertEqual(month["territory"], "wikidata:Q145")
        self.assertEqual(month["release_start_at"], "2027-08-01T00:00:00+00:00")
        self.assertEqual(month["release_end_at"], "2027-08-31T23:59:59+00:00")

        year = windows["wikidata:Q300"]
        self.assertEqual(year["precision"], "year")
        self.assertEqual(year["territory"], "unspecified")
        self.assertEqual(year["release_start_at"], "2028-01-01T00:00:00+00:00")
        self.assertEqual(year["release_end_at"], "2028-12-31T23:59:59+00:00")

        reasons = [item["reason"] for item in warnings]
        self.assertEqual(
            reasons,
            ["release_territory_unspecified", "unsupported_time_precision"],
        )

    def test_unqualified_release_is_never_promoted_to_worldwide(self):
        bundle, _ = self.collector.normalize(self.raw, retrieved_at=self.retrieved)
        year = next(
            item
            for item in bundle["release_windows"]
            if item["project_id"] == "wikidata:Q300"
        )
        self.assertEqual(year["territory"], "unspecified")
        self.assertLess(year["confidence"], 0.85)

    def test_collect_writes_raw_cache_and_returns_importable_batch(self):
        result = self.collector.collect(
            "2026-10-04T00:00:00Z",
            "2029-01-01T00:00:00Z",
            retrieved_at=self.retrieved,
        )
        self.assertEqual(len(self.queries), 1)
        self.assertEqual(result["project_count"], 3)
        self.assertEqual(result["release_observation_count"], 3)
        self.assertEqual(result["warning_count"], 2)
        cache = Path(result["raw_cache_path"])
        self.assertTrue(cache.exists())

        db = self.root / "future.duckdb"
        with FutureReleaseBatchImporter(db) as importer:
            imported = importer.import_batch(result["batch"])
            self.assertFalse(imported["idempotent"])

            # Default worldwide больше не принимает территориальный P291 как
            # мировую дату по умолчанию.
            default_snapshot = importer.store.snapshot_as_of(
                "wikidata:Q100",
                self.retrieved,
            )
            self.assertIsNone(default_snapshot["release_at"])
            self.assertEqual(default_snapshot["release_candidates"], [])

            us_snapshot = importer.store.snapshot_as_of(
                "wikidata:Q100",
                self.retrieved,
                territory="wikidata:Q30",
            )
            self.assertEqual(us_snapshot["release_at"], "2027-05-20T00:00:00+00:00")

            uk_snapshot = importer.store.snapshot_as_of(
                "wikidata:Q200",
                self.retrieved,
                territory="wikidata:Q145",
            )
            self.assertIsNone(uk_snapshot["release_at"])
            self.assertEqual(uk_snapshot["release_window"]["precision"], "month")

            unspecified = importer.store.snapshot_as_of(
                "wikidata:Q300",
                self.retrieved,
                territory="unspecified",
            )
            self.assertEqual(unspecified["release_window"]["precision"], "year")

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

    def test_different_statements_and_territories_are_distinct_evidence(self):
        raw = {
            "results": {
                "bindings": [
                    {
                        "film": _uri("http://www.wikidata.org/entity/Q100"),
                        "filmLabelEn": _cell("Exact Film"),
                        "releaseStatement": _uri(
                            "http://www.wikidata.org/entity/statement/Q100-A"
                        ),
                        "releaseDate": _cell("2027-05-20T00:00:00Z"),
                        "precision": _cell("11"),
                        "territory": _uri("http://www.wikidata.org/entity/Q30"),
                    },
                    {
                        "film": _uri("http://www.wikidata.org/entity/Q100"),
                        "filmLabelEn": _cell("Exact Film"),
                        "releaseStatement": _uri(
                            "http://www.wikidata.org/entity/statement/Q100-B"
                        ),
                        "releaseDate": _cell("2027-06-20T00:00:00Z"),
                        "precision": _cell("11"),
                        "territory": _uri("http://www.wikidata.org/entity/Q145"),
                    },
                ]
            }
        }
        bundle, warnings = self.collector.normalize(raw, retrieved_at=self.retrieved)
        self.assertEqual(warnings, [])
        self.assertEqual(len(bundle["projects"]), 1)
        self.assertEqual(len(bundle["sources"]), 2)
        self.assertEqual(len(bundle["release_windows"]), 2)
        self.assertEqual(
            {item["territory"] for item in bundle["release_windows"]},
            {"wikidata:Q30", "wikidata:Q145"},
        )

    def test_invalid_endpoint_range_and_past_only_collection_are_rejected(self):
        with self.assertRaises(WikidataFutureReleaseError):
            WikidataFutureReleaseCollector(endpoint="file:///tmp/wdqs")
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
