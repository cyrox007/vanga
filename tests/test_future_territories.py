from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from src.future_releases import FutureReleaseStore
from src.future_territories import FutureTerritoryStore


class FutureTerritoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.db = Path(self.temp.name) / "future.duckdb"
        with FutureReleaseStore(self.db) as store:
            for source in (
                {
                    "source_id": "wd-release",
                    "provider": "Wikidata",
                    "usage_basis": "public_record",
                    "retrieved_at": "2026-10-01T00:00:00Z",
                },
                {
                    "source_id": "tmdb-release",
                    "provider": "TMDb",
                    "usage_basis": "licensed",
                    "retrieved_at": "2026-10-01T00:00:00Z",
                },
                {
                    "source_id": "wd-map",
                    "provider": "Wikidata",
                    "usage_basis": "public_record",
                    "retrieved_at": "2026-10-02T00:00:00Z",
                },
            ):
                store.upsert_source(source)
            store.upsert_project(
                {
                    "project_id": "film-a",
                    "imdb_id": "tt1234567",
                    "canonical_title": "Future Film",
                }
            )
            store.add_release_window(
                {
                    "observation_id": "wd-us",
                    "project_id": "film-a",
                    "territory": "wikidata:Q30",
                    "release_start_at": "2027-05-20T00:00:00Z",
                    "release_end_at": "2027-05-20T00:00:00Z",
                    "precision": "exact",
                    "known_at": "2026-10-01T00:00:00Z",
                    "source_id": "wd-release",
                    "confidence": 0.85,
                }
            )
            store.add_release_window(
                {
                    "observation_id": "tmdb-us",
                    "project_id": "film-a",
                    "territory": "iso3166:US",
                    "release_start_at": "2027-05-20T00:00:00Z",
                    "release_end_at": "2027-05-20T00:00:00Z",
                    "precision": "exact",
                    "known_at": "2026-10-01T00:00:00Z",
                    "source_id": "tmdb-release",
                    "confidence": 0.9,
                }
            )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _add_us_mapping(self, *, known_at="2026-10-02T00:00:00Z") -> None:
        with FutureTerritoryStore(self.db) as store:
            store.add_mapping(
                {
                    "mapping_id": f"map-us-{known_at}",
                    "source_territory": "wikidata:Q30",
                    "canonical_territory": "iso3166:US",
                    "known_at": known_at,
                    "source_id": "wd-map",
                    "confidence": 0.95,
                }
            )

    def test_mapping_is_temporal_and_not_visible_before_known_at(self) -> None:
        self._add_us_mapping()
        with FutureTerritoryStore(self.db) as store:
            early = store.resolve_as_of("wikidata:Q30", "2026-10-01T12:00:00Z")
            late = store.resolve_as_of("wikidata:Q30", "2026-10-03T00:00:00Z")
        self.assertFalse(early["resolved"])
        self.assertTrue(late["resolved"])
        self.assertEqual(late["canonical_territory"], "iso3166:us")

    def test_iso_territory_is_already_canonical(self) -> None:
        with FutureTerritoryStore(self.db) as store:
            result = store.resolve_as_of("iso3166:US", "2026-10-03T00:00:00Z")
        self.assertTrue(result["resolved"])
        self.assertEqual(result["canonical_territory"], "iso3166:us")

    def test_cross_source_agreement_after_mapping(self) -> None:
        self._add_us_mapping()
        with FutureTerritoryStore(self.db) as store:
            snapshot = store.release_snapshot_as_of(
                "film-a",
                "2026-10-03T00:00:00Z",
                canonical_territory="iso3166:US",
            )
        self.assertTrue(snapshot["resolved"])
        self.assertFalse(snapshot["conflict"])
        self.assertEqual(snapshot["canonical_territory"], "iso3166:us")
        self.assertEqual(snapshot["release_window"]["source_count"], 2)
        self.assertEqual(
            {item["source_id"] for item in snapshot["release_window"]["evidence"]},
            {"wd-release", "tmdb-release"},
        )

    def test_cross_source_disagreement_is_explicit_conflict(self) -> None:
        self._add_us_mapping()
        with FutureReleaseStore(self.db) as registry:
            registry.upsert_source(
                {
                    "source_id": "official-release",
                    "provider": "Official studio",
                    "usage_basis": "public_record",
                    "retrieved_at": "2026-10-04T00:00:00Z",
                }
            )
            registry.add_release_window(
                {
                    "observation_id": "official-us",
                    "project_id": "film-a",
                    "territory": "iso3166:US",
                    "release_start_at": "2027-05-27T00:00:00Z",
                    "release_end_at": "2027-05-27T00:00:00Z",
                    "precision": "exact",
                    "known_at": "2026-10-04T00:00:00Z",
                    "source_id": "official-release",
                    "confidence": 0.95,
                }
            )
        with FutureTerritoryStore(self.db) as store:
            snapshot = store.release_snapshot_as_of(
                "film-a",
                "2026-10-05T00:00:00Z",
                canonical_territory="US",
            )
        self.assertFalse(snapshot["resolved"])
        self.assertTrue(snapshot["conflict"])
        self.assertEqual(len(snapshot["candidates"]), 2)

    def test_unresolved_wikidata_observation_is_reported_not_guessed(self) -> None:
        with FutureTerritoryStore(self.db) as store:
            snapshot = store.release_snapshot_as_of(
                "film-a",
                "2026-10-01T12:00:00Z",
                canonical_territory="US",
            )
        self.assertTrue(snapshot["resolved"])
        self.assertEqual(snapshot["release_window"]["source_count"], 1)
        self.assertEqual(snapshot["unresolved_observations"][0]["territory"], "wikidata:q30")


if __name__ == "__main__":
    unittest.main()
