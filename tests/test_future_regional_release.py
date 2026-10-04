from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from src.future_regional_release import (
    FutureRegionalReleaseService,
    RegionalTemporalFuturePredictionPayloadBuilder,
)
from src.future_releases import FutureReleaseStore
from src.future_temporal_facts import FutureTemporalFactStore
from src.future_territories import FutureTerritoryStore


class FutureRegionalReleaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.db = self.root / "future.duckdb"
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
                {
                    "source_id": "facts",
                    "provider": "Fixture",
                    "usage_basis": "first_party",
                    "retrieved_at": "2026-10-01T00:00:00Z",
                },
            ):
                store.upsert_source(source)
            store.upsert_project(
                {
                    "project_id": "film-a",
                    "canonical_title": "Future Film",
                }
            )
            store.upsert_person(
                {
                    "person_id": "director-a",
                    "canonical_name": "Director A",
                }
            )
            store.link_person(
                {
                    "link_id": "director-link",
                    "project_id": "film-a",
                    "person_id": "director-a",
                    "role": "director",
                    "known_at": "2026-10-01T00:00:00Z",
                    "source_id": "wd-release",
                    "confidence": 0.9,
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
            store.add_release_window(
                {
                    "observation_id": "worldwide-only",
                    "project_id": "film-a",
                    "territory": "worldwide",
                    "release_start_at": "2027-05-01T00:00:00Z",
                    "release_end_at": "2027-05-01T00:00:00Z",
                    "precision": "exact",
                    "known_at": "2026-10-01T00:00:00Z",
                    "source_id": "wd-release",
                    "confidence": 0.7,
                }
            )

        with FutureTerritoryStore(self.db) as territories:
            territories.add_mapping(
                {
                    "mapping_id": "q30-us",
                    "source_territory": "wikidata:Q30",
                    "canonical_territory": "US",
                    "known_at": "2026-10-02T00:00:00Z",
                    "source_id": "wd-map",
                    "confidence": 0.95,
                }
            )

        with FutureTemporalFactStore(self.db) as facts:
            facts.add_fact(
                {
                    "observation_id": "runtime-a",
                    "project_id": "film-a",
                    "fact_type": "runtime_minutes",
                    "value": 120,
                    "known_at": "2026-10-01T00:00:00Z",
                    "source_id": "facts",
                    "confidence": 0.9,
                }
            )
            facts.add_fact(
                {
                    "observation_id": "genres-a",
                    "project_id": "film-a",
                    "fact_type": "genres",
                    "value": ["Drama"],
                    "known_at": "2026-10-01T00:00:00Z",
                    "source_id": "facts",
                    "confidence": 0.9,
                }
            )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_snapshot_combines_wikidata_and_tmdb_for_explicit_iso_territory(self) -> None:
        service = FutureRegionalReleaseService(self.db)
        snapshot = service.snapshot_as_of(
            "film-a",
            "2026-10-03T00:00:00Z",
            territory="US",
        )
        self.assertEqual(snapshot["target_territory"], "iso3166:us")
        self.assertEqual(snapshot["regional_release_resolution"], "resolved")
        self.assertFalse(snapshot["release_date_conflict"])
        self.assertEqual(snapshot["release_at"], "2027-05-20T00:00:00+00:00")
        self.assertEqual(snapshot["release_window"]["source_count"], 2)
        self.assertEqual(
            {item["source_territory"] for item in snapshot["release_window"]["evidence"]},
            {"wikidata:q30", "iso3166:us"},
        )

    def test_worldwide_is_never_used_as_regional_fallback(self) -> None:
        service = FutureRegionalReleaseService(self.db)
        snapshot = service.snapshot_as_of(
            "film-a",
            "2026-10-03T00:00:00Z",
            territory="DE",
        )
        self.assertEqual(snapshot["regional_release_resolution"], "missing")
        self.assertIsNone(snapshot["release_at"])
        self.assertEqual(snapshot["release_candidates"], [])

    def test_regional_catalog_uses_explicit_territory(self) -> None:
        service = FutureRegionalReleaseService(self.db)
        us = service.catalog_as_of(
            "2026-10-03T00:00:00Z",
            territory="US",
            from_at="2027-05-01T00:00:00Z",
            to_at="2027-05-31T23:59:59Z",
        )
        de = service.catalog_as_of(
            "2026-10-03T00:00:00Z",
            territory="DE",
            from_at="2027-05-01T00:00:00Z",
            to_at="2027-05-31T23:59:59Z",
        )
        self.assertEqual([item["project_id"] for item in us["items"]], ["film-a"])
        self.assertEqual(de["items"], [])
        self.assertEqual(us["territory_policy"], "explicit_iso3166_no_worldwide_fallback")

    def test_prediction_builder_uses_regional_release_and_temporal_facts(self) -> None:
        builder = RegionalTemporalFuturePredictionPayloadBuilder(
            future_db_path=self.db,
            imdb_db_path=self.root / "missing-imdb.duckdb",
            source_db_path=self.root / "missing-source.duckdb",
            production_db_path=self.root / "missing-production.duckdb",
        )
        result = builder.build(
            "film-a",
            "2026-10-03T00:00:00Z",
            territory="US",
        )
        self.assertTrue(result["prediction_ready"])
        self.assertEqual(result["target_territory"], "iso3166:us")
        self.assertEqual(result["request"]["year"], 2027)
        self.assertEqual(result["request"]["runtime"], 120)
        self.assertEqual(result["request"]["genres"], ["Drama"])
        self.assertEqual(result["input_sources"]["release_date"], "p9_regional_release")

    def test_prediction_builder_blocks_cross_source_release_conflict(self) -> None:
        with FutureReleaseStore(self.db) as store:
            store.upsert_source(
                {
                    "source_id": "official-release",
                    "provider": "Official studio",
                    "usage_basis": "public_record",
                    "retrieved_at": "2026-10-04T00:00:00Z",
                }
            )
            store.add_release_window(
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

        builder = RegionalTemporalFuturePredictionPayloadBuilder(
            future_db_path=self.db,
            imdb_db_path=self.root / "missing-imdb.duckdb",
            source_db_path=self.root / "missing-source.duckdb",
            production_db_path=self.root / "missing-production.duckdb",
        )
        result = builder.build(
            "film-a",
            "2026-10-05T00:00:00Z",
            territory="US",
        )
        self.assertFalse(result["prediction_ready"])
        self.assertIn("regional_release_date_conflict", result["blockers"])
        self.assertIsNone(result["request"])
        self.assertTrue(result["future_release_snapshot"]["release_date_conflict"])


if __name__ == "__main__":
    unittest.main()
