from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from src.future_release_import import FutureReleaseBatchImporter
from src.future_releases import FutureReleaseStore
from src.future_temporal_facts import FutureTemporalFactStore
from src.wikidata_future_facts import (
    WikidataFutureFactsCollector,
    WikidataFutureFactsError,
)


UTC = timezone.utc


def _b(value: str) -> dict[str, str]:
    return {"type": "literal", "value": value}


def _u(value: str) -> dict[str, str]:
    return {"type": "uri", "value": value}


def _facts_raw() -> dict:
    return {
        "head": {"vars": []},
        "results": {
            "bindings": [
                {
                    "film": _u("http://www.wikidata.org/entity/Q100"),
                    "runtime": _b("132"),
                    "genre": _u("http://www.wikidata.org/entity/Q130232"),
                    "genreLabelEn": _b("drama film"),
                },
                {
                    "film": _u("http://www.wikidata.org/entity/Q100"),
                    "runtime": _b("132"),
                    "genre": _u("http://www.wikidata.org/entity/Q471839"),
                    "genreLabelEn": _b("science fiction film"),
                },
            ]
        },
    }


class WikidataFutureFactsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.db = root / "future.duckdb"
        self.cache = root / "cache"
        with FutureReleaseStore(self.db) as store:
            store.upsert_project(
                {
                    "project_id": "wikidata:Q100",
                    "imdb_id": "tt0000100",
                    "wikidata_id": "Q100",
                    "canonical_title": "Future Film",
                }
            )
        self.project = {
            "project_id": "wikidata:Q100",
            "imdb_id": "tt0000100",
            "wikidata_id": "Q100",
            "canonical_title": "Future Film",
        }
        self.retrieved = datetime(2026, 10, 4, 10, 0, tzinfo=UTC)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_query_is_sparql11_and_has_no_blazegraph_label_service(self) -> None:
        query = WikidataFutureFactsCollector.build_query(["Q100", "Q200", "Q100"])
        self.assertIn("VALUES ?film { wd:Q100 wd:Q200 }", query)
        self.assertIn("wdt:P2047", query)
        self.assertIn("wdt:P136", query)
        self.assertIn("rdfs:label", query)
        self.assertNotIn("SERVICE wikibase:label", query)
        self.assertNotIn("bigdata.com", query)
        with self.assertRaises(WikidataFutureFactsError):
            WikidataFutureFactsCollector.build_query(["bad-qid"])

    def test_normalize_emits_runtime_sorted_genres_and_source_stream(self) -> None:
        bundle, warnings = WikidataFutureFactsCollector.normalize(
            _facts_raw(),
            projects=[self.project],
            retrieved_at=self.retrieved,
        )
        self.assertEqual(warnings, [])
        self.assertEqual(len(bundle["sources"]), 1)
        snapshot_source_id = bundle["sources"][0]["source_id"]
        self.assertTrue(snapshot_source_id.startswith("wikidata:Q100:facts:20261004T100000Z:"))
        facts = {item["fact_type"]: item for item in bundle["temporal_facts"]}
        self.assertEqual(facts["runtime_minutes"]["value"], 132)
        self.assertEqual(
            facts["genres"]["value"],
            ["drama film", "science fiction film"],
        )
        self.assertEqual(facts["runtime_minutes"]["source_id"], snapshot_source_id)
        self.assertEqual(
            facts["runtime_minutes"]["source_stream_id"],
            "wikidata:Q100:facts",
        )
        self.assertEqual(facts["runtime_minutes"]["known_at"], self.retrieved.isoformat())

    def test_runtime_conflict_is_warning_and_not_imported(self) -> None:
        raw = _facts_raw()
        raw["results"]["bindings"].append(
            {
                "film": _u("http://www.wikidata.org/entity/Q100"),
                "runtime": _b("140"),
            }
        )
        bundle, warnings = WikidataFutureFactsCollector.normalize(
            raw,
            projects=[self.project],
            retrieved_at=self.retrieved,
        )
        reasons = {item["reason"] for item in warnings}
        self.assertIn("runtime_conflict_within_wikidata", reasons)
        fact_types = {item["fact_type"] for item in bundle["temporal_facts"]}
        self.assertNotIn("runtime_minutes", fact_types)
        self.assertIn("genres", fact_types)

    def test_immutable_snapshots_share_stream_and_latest_replaces_runtime(self) -> None:
        first_bundle, _ = WikidataFutureFactsCollector.normalize(
            _facts_raw(),
            projects=[self.project],
            retrieved_at=self.retrieved,
        )
        second_raw = _facts_raw()
        for row in second_raw["results"]["bindings"]:
            row["runtime"] = _b("140")
        second_at = datetime(2026, 10, 5, 10, 0, tzinfo=UTC)
        second_bundle, _ = WikidataFutureFactsCollector.normalize(
            second_raw,
            projects=[self.project],
            retrieved_at=second_at,
        )
        self.assertNotEqual(
            first_bundle["sources"][0]["source_id"],
            second_bundle["sources"][0]["source_id"],
        )
        self.assertEqual(
            first_bundle["temporal_facts"][0]["source_stream_id"],
            second_bundle["temporal_facts"][0]["source_stream_id"],
        )

        def batch(bundle, batch_id, fingerprint):
            return {
                "version": 1,
                "batch_id": batch_id,
                "provider": "wikidata-wdqs-facts",
                "retrieved_at": (
                    self.retrieved.isoformat()
                    if batch_id == "first"
                    else second_at.isoformat()
                ),
                "source_fingerprint_sha256": fingerprint * 64,
                "bundle": bundle,
            }

        with FutureReleaseBatchImporter(self.db) as importer:
            importer.import_batch(batch(first_bundle, "first", "a"))
            importer.import_batch(batch(second_bundle, "second", "b"))

        with FutureTemporalFactStore(self.db) as store:
            first = store.snapshot_as_of("wikidata:Q100", "2026-10-04T12:00:00Z")
            second = store.snapshot_as_of("wikidata:Q100", "2026-10-05T12:00:00Z")
        self.assertEqual(first["facts"]["runtime_minutes"], 132)
        self.assertEqual(second["facts"]["runtime_minutes"], 140)
        self.assertNotIn("runtime_minutes", second["conflicts"])
        evidence = second["candidates"]["runtime_minutes"][0]["evidence"][0]
        self.assertEqual(evidence["source_stream_id"], "wikidata:Q100:facts")
        self.assertEqual(evidence["source_id"], second_bundle["sources"][0]["source_id"])

    def test_collect_caches_and_emits_importer_compatible_batch(self) -> None:
        queries: list[str] = []

        def transport(query: str) -> dict:
            queries.append(query)
            return _facts_raw()

        collector = WikidataFutureFactsCollector(
            cache_dir=self.cache,
            transport=transport,
        )
        result = collector.collect_from_registry(
            self.db,
            project_ids=["wikidata:Q100"],
            retrieved_at=self.retrieved,
        )
        self.assertEqual(len(queries), 1)
        self.assertTrue(Path(result["raw_cache_path"]).exists())
        self.assertEqual(result["temporal_fact_count"], 2)
        self.assertFalse(result["network_required_for_inference"])

        with FutureReleaseBatchImporter(self.db) as importer:
            imported = importer.import_batch(result["batch"])
        self.assertFalse(imported["idempotent"])
        self.assertEqual(imported["counts"]["temporal_facts"], 2)

    def test_invalid_endpoint_is_rejected(self) -> None:
        with self.assertRaises(WikidataFutureFactsError):
            WikidataFutureFactsCollector(endpoint="file:///tmp/wdqs")


if __name__ == "__main__":
    unittest.main()
