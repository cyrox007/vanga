from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from src.future_release_import import FutureReleaseBatchImporter
from src.future_releases import FutureReleaseStore
from src.tmdb_future_releases import TmdbFutureReleaseCollector, TmdbFutureReleaseError


UTC = timezone.utc


class TmdbFutureReleaseCollectorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.db = root / "future.duckdb"
        self.cache = root / "cache"
        with FutureReleaseStore(self.db) as store:
            store.upsert_project(
                {
                    "project_id": "wikidata:Q100",
                    "imdb_id": "tt1234567",
                    "wikidata_id": "Q100",
                    "canonical_title": "Future Film",
                }
            )
            store.upsert_project(
                {
                    "project_id": "manual:no-imdb",
                    "canonical_title": "No IMDb Film",
                }
            )
        self.retrieved = datetime(2026, 10, 4, 12, 0, tzinfo=UTC)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_registry_selection_requires_imdb_id(self) -> None:
        projects, warnings = TmdbFutureReleaseCollector.registry_projects(
            self.db,
            project_ids=["wikidata:Q100", "manual:no-imdb", "missing"],
        )
        self.assertEqual([item["project_id"] for item in projects], ["wikidata:Q100"])
        pairs = {(item.get("project_id"), item["reason"]) for item in warnings}
        self.assertIn(("manual:no-imdb", "imdb_id_missing"), pairs)
        self.assertIn(("missing", "project_not_found"), pairs)

    def test_find_requires_single_movie_result(self) -> None:
        self.assertEqual(
            TmdbFutureReleaseCollector._resolve_tmdb_id({"movie_results": [{"id": 42}]}),
            42,
        )
        self.assertIsNone(TmdbFutureReleaseCollector._resolve_tmdb_id({"movie_results": []}))
        self.assertIsNone(
            TmdbFutureReleaseCollector._resolve_tmdb_id(
                {"movie_results": [{"id": 1}, {"id": 2}]}
            )
        )

    def test_normalize_keeps_theatrical_dates_and_iso_territory(self) -> None:
        release_payload = {
            "id": 900,
            "results": [
                {
                    "iso_3166_1": "US",
                    "release_dates": [
                        {"type": 3, "release_date": "2027-05-20T00:00:00.000Z"},
                        {"type": 4, "release_date": "2027-06-01T00:00:00.000Z"},
                    ],
                },
                {
                    "iso_3166_1": "GB",
                    "release_dates": [
                        {"type": 2, "release_date": "2027-05-18T00:00:00.000Z"},
                    ],
                },
            ],
        }
        project = {
            "project_id": "wikidata:Q100",
            "imdb_id": "tt1234567",
            "canonical_title": "Future Film",
        }
        bundle, warnings = TmdbFutureReleaseCollector.normalize_release_dates(
            release_payload,
            project=project,
            tmdb_id=900,
            retrieved_at=self.retrieved,
        )
        self.assertEqual(warnings, [])
        self.assertEqual(len(bundle["sources"]), 1)
        self.assertEqual(len(bundle["release_windows"]), 2)
        self.assertEqual(
            {item["territory"] for item in bundle["release_windows"]},
            {"iso3166:US", "iso3166:GB"},
        )
        self.assertTrue(all(item["precision"] == "exact" for item in bundle["release_windows"]))
        self.assertFalse(
            any(item["release_start_at"].startswith("2027-06-01") for item in bundle["release_windows"])
        )

    def test_collect_uses_imdb_find_caches_and_imports_batch(self) -> None:
        calls: list[str] = []

        def transport(url: str) -> dict:
            calls.append(url)
            if "/find/tt1234567" in url:
                return {"movie_results": [{"id": 900, "title": "Future Film"}]}
            if "/movie/900/release_dates" in url:
                return {
                    "id": 900,
                    "results": [
                        {
                            "iso_3166_1": "US",
                            "release_dates": [
                                {"type": 3, "release_date": "2027-05-20T00:00:00Z"}
                            ],
                        }
                    ],
                }
            raise AssertionError(url)

        collector = TmdbFutureReleaseCollector(
            cache_dir=self.cache,
            transport=transport,
        )
        result = collector.collect_from_registry(
            self.db,
            project_ids=["wikidata:Q100"],
            retrieved_at=self.retrieved,
        )
        self.assertEqual(len(calls), 2)
        self.assertIn("external_source=imdb_id", calls[0])
        self.assertEqual(result["resolved_project_count"], 1)
        self.assertEqual(result["release_observation_count"], 1)
        self.assertTrue(Path(result["raw_cache_path"]).exists())
        self.assertFalse(result["network_required_for_inference"])

        with FutureReleaseBatchImporter(self.db) as importer:
            imported = importer.import_batch(result["batch"])
            self.assertFalse(imported["idempotent"])
            snapshot = importer.store.snapshot_as_of(
                "wikidata:Q100",
                self.retrieved,
                territory="iso3166:US",
            )
        self.assertEqual(snapshot["release_at"], "2027-05-20T00:00:00+00:00")

    def test_ambiguous_tmdb_find_is_warning_not_guess(self) -> None:
        def transport(url: str) -> dict:
            if "/find/tt1234567" in url:
                return {"movie_results": [{"id": 1}, {"id": 2}]}
            raise AssertionError("release_dates не должен запрашиваться при ambiguous find")

        collector = TmdbFutureReleaseCollector(cache_dir=self.cache, transport=transport)
        result = collector.collect_from_registry(
            self.db,
            project_ids=["wikidata:Q100"],
            retrieved_at=self.retrieved,
        )
        self.assertEqual(result["resolved_project_count"], 0)
        self.assertEqual(result["release_observation_count"], 0)
        self.assertIn("tmdb_movie_not_resolved", {item["reason"] for item in result["warnings"]})

    def test_token_is_required_without_test_transport(self) -> None:
        with self.assertRaises(TmdbFutureReleaseError):
            TmdbFutureReleaseCollector(token="", api_base="https://api.themoviedb.org/3")


if __name__ == "__main__":
    unittest.main()
