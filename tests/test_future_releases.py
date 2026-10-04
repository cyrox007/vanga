from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from src.future_releases import FutureReleaseError, FutureReleaseStore


class FutureReleaseStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "future.duckdb"
        self.store = FutureReleaseStore(self.db)
        self._seed()

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def _seed(self):
        self.store.upsert_source(
            {
                "source_id": "official",
                "provider": "Studio",
                "url": "https://example.test/studio",
                "usage_basis": "public_record",
                "retrieved_at": "2026-01-01T00:00:00Z",
            }
        )
        self.store.upsert_source(
            {
                "source_id": "press",
                "provider": "Press",
                "url": "https://example.test/press",
                "usage_basis": "manual_reference",
                "retrieved_at": "2026-01-02T00:00:00Z",
            }
        )
        self.store.upsert_project(
            {
                "project_id": "film-a",
                "imdb_id": "tt1234567",
                "wikidata_id": "Q123",
                "canonical_title": "Future Film",
            }
        )
        self.store.add_alias(
            {
                "alias_id": "alias-a",
                "project_id": "film-a",
                "alias": "Будущий фильм",
                "known_at": "2026-01-05T00:00:00Z",
                "source_id": "official",
                "confidence": 1.0,
            }
        )
        self.store.add_release_window(
            {
                "observation_id": "release-old",
                "project_id": "film-a",
                "territory": "worldwide",
                "release_start_at": "2027-05-01T00:00:00Z",
                "precision": "exact",
                "known_at": "2026-01-10T00:00:00Z",
                "source_id": "official",
                "confidence": 1.0,
            }
        )
        self.store.add_release_window(
            {
                "observation_id": "release-new",
                "project_id": "film-a",
                "territory": "worldwide",
                "release_start_at": "2027-07-01T00:00:00Z",
                "precision": "exact",
                "known_at": "2026-02-10T00:00:00Z",
                "source_id": "official",
                "confidence": 1.0,
            }
        )
        self.store.add_status(
            {
                "observation_id": "status-a",
                "project_id": "film-a",
                "status": "filming",
                "known_at": "2026-01-12T00:00:00Z",
                "source_id": "official",
                "confidence": 1.0,
            }
        )
        self.store.upsert_person(
            {
                "person_id": "director-a",
                "imdb_id": "nm1234567",
                "canonical_name": "Director A",
            }
        )
        self.store.upsert_person(
            {
                "person_id": "actor-a",
                "imdb_id": "nm7654321",
                "canonical_name": "Actor A",
            }
        )
        self.store.link_person(
            {
                "link_id": "director-link-official",
                "project_id": "film-a",
                "person_id": "director-a",
                "role": "director",
                "known_at": "2026-01-15T00:00:00Z",
                "source_id": "official",
                "confidence": 1.0,
            }
        )
        self.store.link_person(
            {
                "link_id": "director-link-press",
                "project_id": "film-a",
                "person_id": "director-a",
                "role": "director",
                "known_at": "2026-01-20T00:00:00Z",
                "source_id": "press",
                "confidence": 0.8,
            }
        )
        self.store.link_person(
            {
                "link_id": "actor-link-late",
                "project_id": "film-a",
                "person_id": "actor-a",
                "role": "actor",
                "billing_order": 1,
                "known_at": "2026-03-01T00:00:00Z",
                "source_id": "official",
                "confidence": 1.0,
            }
        )
        self.store.upsert_entity(
            {
                "entity_id": "franchise-a",
                "kind": "franchise",
                "canonical_name": "Franchise A",
                "external_id": "Q999",
            }
        )
        self.store.upsert_entity(
            {
                "entity_id": "studio-a",
                "kind": "studio",
                "canonical_name": "Studio A",
            }
        )
        self.store.link_entity(
            {
                "link_id": "franchise-link",
                "project_id": "film-a",
                "entity_id": "franchise-a",
                "relation_type": "part_of_franchise",
                "known_at": "2026-01-18T00:00:00Z",
                "source_id": "official",
                "confidence": 1.0,
            }
        )
        self.store.link_entity(
            {
                "link_id": "studio-link",
                "project_id": "film-a",
                "entity_id": "studio-a",
                "relation_type": "produced_by",
                "known_at": "2026-01-18T00:00:00Z",
                "source_id": "official",
                "confidence": 1.0,
            }
        )

    def test_release_history_is_as_of_and_same_source_supersedes_old_value(self):
        before = self.store.snapshot_as_of("film-a", "2026-01-20T00:00:00Z")
        self.assertEqual(before["release_at"], "2027-05-01T00:00:00+00:00")
        self.assertFalse(before["release_date_conflict"])

        after = self.store.snapshot_as_of("film-a", "2026-02-20T00:00:00Z")
        self.assertEqual(after["release_at"], "2027-07-01T00:00:00+00:00")
        self.assertFalse(after["release_date_conflict"])

    def test_conflicting_current_sources_are_exposed_not_silently_resolved(self):
        self.store.add_release_window(
            {
                "observation_id": "press-release",
                "project_id": "film-a",
                "territory": "worldwide",
                "release_start_at": "2027-06-15T00:00:00Z",
                "precision": "exact",
                "known_at": "2026-02-15T00:00:00Z",
                "source_id": "press",
                "confidence": 0.7,
            }
        )
        snapshot = self.store.snapshot_as_of("film-a", "2026-02-20T00:00:00Z")
        self.assertTrue(snapshot["release_date_conflict"])
        self.assertIsNone(snapshot["release_window"])
        self.assertIsNone(snapshot["release_at"])
        self.assertEqual(len(snapshot["release_candidates"]), 2)
        evidence_sources = {
            evidence["source_id"]
            for candidate in snapshot["release_candidates"]
            for evidence in candidate["evidence"]
        }
        self.assertEqual(evidence_sources, {"official", "press"})

    def test_duplicate_person_evidence_does_not_duplicate_team_member(self):
        snapshot = self.store.snapshot_as_of("film-a", "2026-02-20T00:00:00Z")
        self.assertEqual(len(snapshot["directors"]), 1)
        self.assertEqual(snapshot["directors"][0]["person_id"], "director-a")
        self.assertEqual(snapshot["directors"][0]["source_ids"], ["official", "press"])
        self.assertEqual(snapshot["cast"], [])

        later = self.store.snapshot_as_of("film-a", "2026-03-10T00:00:00Z")
        self.assertEqual(len(later["cast"]), 1)
        self.assertEqual(later["cast"][0]["person_id"], "actor-a")

    def test_entities_are_temporal_and_normalized_by_canonical_id(self):
        before = self.store.snapshot_as_of("film-a", "2026-01-17T00:00:00Z")
        self.assertEqual(before["entities"], [])
        after = self.store.snapshot_as_of("film-a", "2026-01-20T00:00:00Z")
        kinds = {item["kind"] for item in after["entities"]}
        self.assertEqual(kinds, {"franchise", "studio"})
        self.assertIn("part_of_franchise", {item["relation_type"] for item in after["entities"]})

    def test_exact_alias_resolution_is_temporal_and_ambiguity_fails_closed(self):
        self.assertIsNone(
            self.store.resolve_title_as_of("Будущий фильм", "2026-01-01T00:00:00Z")
        )
        resolved = self.store.resolve_title_as_of(
            "  БУДУЩИЙ---ФИЛЬМ ", "2026-01-20T00:00:00Z"
        )
        self.assertEqual(resolved["project_id"], "film-a")

        self.store.upsert_project(
            {"project_id": "film-b", "canonical_title": "Other Future Film"}
        )
        self.store.add_alias(
            {
                "alias_id": "alias-b",
                "project_id": "film-b",
                "alias": "Будущий фильм",
                "known_at": "2026-01-10T00:00:00Z",
                "source_id": "press",
                "confidence": 0.6,
            }
        )
        with self.assertRaises(FutureReleaseError):
            self.store.resolve_title_as_of("Будущий фильм", "2026-01-20T00:00:00Z")

    def test_catalog_is_local_deterministic_and_filters_by_release_window(self):
        first = self.store.catalog_as_of(
            "2026-02-20T00:00:00Z",
            from_at="2027-06-01T00:00:00Z",
            to_at="2027-08-01T00:00:00Z",
        )
        second = self.store.catalog_as_of(
            "2026-02-20T00:00:00Z",
            from_at="2027-06-01T00:00:00Z",
            to_at="2027-08-01T00:00:00Z",
        )
        self.assertEqual(len(first["items"]), 1)
        self.assertEqual(first["items"][0]["project_id"], "film-a")
        self.assertFalse(first["network_required_for_inference"])
        self.assertEqual(
            first["catalog_fingerprint_sha256"], second["catalog_fingerprint_sha256"]
        )

    def test_release_validation_rejects_inverted_or_fake_exact_window(self):
        with self.assertRaises(FutureReleaseError):
            self.store.add_release_window(
                {
                    "project_id": "film-a",
                    "release_start_at": "2027-08-01T00:00:00Z",
                    "release_end_at": "2027-07-01T00:00:00Z",
                    "precision": "window",
                    "known_at": "2026-04-01T00:00:00Z",
                    "source_id": "official",
                }
            )
        with self.assertRaises(FutureReleaseError):
            self.store.add_release_window(
                {
                    "project_id": "film-a",
                    "release_start_at": "2027-07-01T00:00:00Z",
                    "release_end_at": "2027-07-02T00:00:00Z",
                    "precision": "exact",
                    "known_at": "2026-04-01T00:00:00Z",
                    "source_id": "official",
                }
            )


if __name__ == "__main__":
    unittest.main()
