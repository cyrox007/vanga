from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from src.future_releases import FutureReleaseStore
from src.future_territories import FutureTerritoryStore
from src.wikidata_territories import WikidataTerritoryCollector


UTC = timezone.utc


def _u(value: str) -> dict[str, str]:
    return {"type": "uri", "value": value}


def _b(value: str) -> dict[str, str]:
    return {"type": "literal", "value": value}


class WikidataTerritoryCollectorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.db = root / "future.duckdb"
        self.cache = root / "cache"
        with FutureReleaseStore(self.db) as store:
            store.upsert_source(
                {
                    "source_id": "release-source",
                    "provider": "Wikidata",
                    "usage_basis": "public_record",
                    "retrieved_at": "2026-10-01T00:00:00Z",
                }
            )
            store.upsert_project(
                {
                    "project_id": "film-a",
                    "canonical_title": "Future Film",
                }
            )
            store.add_release_window(
                {
                    "observation_id": "release-us",
                    "project_id": "film-a",
                    "territory": "wikidata:Q30",
                    "release_start_at": "2027-05-20T00:00:00Z",
                    "release_end_at": "2027-05-20T00:00:00Z",
                    "precision": "exact",
                    "known_at": "2026-10-01T00:00:00Z",
                    "source_id": "release-source",
                    "confidence": 0.85,
                }
            )
        self.retrieved = datetime(2026, 10, 4, 13, 0, tzinfo=UTC)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_registry_qids_reads_only_wikidata_territories(self) -> None:
        self.assertEqual(WikidataTerritoryCollector.registry_qids(self.db), ["Q30"])

    def test_query_uses_p297_and_plain_sparql(self) -> None:
        query = WikidataTerritoryCollector.build_query(["Q30", "Q145", "Q30"])
        self.assertIn("VALUES ?territory { wd:Q145 wd:Q30 }", query)
        self.assertIn("wdt:P297", query)
        self.assertNotIn("SERVICE", query)

    def test_normalize_rejects_conflicting_iso_codes(self) -> None:
        raw = {
            "results": {
                "bindings": [
                    {"territory": _u("http://www.wikidata.org/entity/Q30"), "isoCode": _b("US")},
                    {"territory": _u("http://www.wikidata.org/entity/Q30"), "isoCode": _b("ZZ")},
                ]
            }
        }
        records, warnings = WikidataTerritoryCollector.normalize(
            raw,
            retrieved_at=self.retrieved,
        )
        self.assertEqual(records, [])
        self.assertIn("iso_code_conflict_within_wikidata", {item["reason"] for item in warnings})

    def test_collect_apply_and_resolve_as_of(self) -> None:
        queries: list[str] = []

        def transport(query: str) -> dict:
            queries.append(query)
            return {
                "results": {
                    "bindings": [
                        {
                            "territory": _u("http://www.wikidata.org/entity/Q30"),
                            "isoCode": _b("US"),
                        }
                    ]
                }
            }

        collector = WikidataTerritoryCollector(
            cache_dir=self.cache,
            transport=transport,
        )
        result = collector.collect(self.db, retrieved_at=self.retrieved)
        self.assertEqual(len(queries), 1)
        self.assertEqual(result["mapping_count"], 1)
        self.assertTrue(Path(result["raw_cache_path"]).exists())
        applied = collector.apply(self.db, result)
        self.assertEqual(applied["applied_mapping_count"], 1)
        with FutureTerritoryStore(self.db) as store:
            early = store.resolve_as_of("wikidata:Q30", "2026-10-04T12:59:59Z")
            late = store.resolve_as_of("wikidata:Q30", "2026-10-04T13:00:00Z")
        self.assertFalse(early["resolved"])
        self.assertEqual(late["canonical_territory"], "iso3166:us")


if __name__ == "__main__":
    unittest.main()
