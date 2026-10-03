from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from src.production_context import (
    ProductionContextStore,
    ProductionContextValidationError,
)


class ProductionContextStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "production_context.duckdb"
        self.store = ProductionContextStore(self.path)

        self.store.upsert_source(
            {
                "source_id": "src-early",
                "url": "https://example.test/early",
                "title": "Ранний источник",
                "publisher": "Example",
                "published_at": "2024-02-01T10:00:00Z",
                "retrieved_at": "2024-02-02T10:00:00Z",
                "confidence": 0.9,
            }
        )
        self.store.upsert_source(
            {
                "source_id": "src-late",
                "url": "https://example.test/late",
                "title": "Поздний источник",
                "publisher": "Example",
                "published_at": "2025-02-01T10:00:00Z",
                "retrieved_at": "2025-02-02T10:00:00Z",
                "confidence": 0.8,
            }
        )
        self.store.upsert_project(
            {
                "project_id": "project-1",
                "imdb_id": "tt1234567",
                "title": "Test Film",
                "release_at": "2025-07-01T00:00:00Z",
                "franchise_id": "franchise-x",
                "shared_universe_id": "universe-x",
                "installment_index": 4,
                "identity_known_at": "2024-01-01T00:00:00Z",
            }
        )
        self.store.upsert_entity(
            {
                "entity_id": "studio-1",
                "kind": "studio",
                "name": "Studio One",
            }
        )
        self.store.upsert_entity(
            {
                "entity_id": "producer-1",
                "kind": "producer",
                "name": "Producer One",
            }
        )
        self.store.upsert_entity(
            {
                "entity_id": "consultancy-1",
                "kind": "consultancy",
                "name": "Narrative Consultancy",
            }
        )

        self.store.link_entity(
            {
                "link_id": "link-studio",
                "project_id": "project-1",
                "entity_id": "studio-1",
                "role": "studio",
                "stage": "development",
                "known_at": "2024-02-10T00:00:00Z",
                "source_id": "src-early",
            }
        )
        self.store.link_entity(
            {
                "link_id": "link-producer",
                "project_id": "project-1",
                "entity_id": "producer-1",
                "role": "producer",
                "stage": "production",
                "known_at": "2025-02-10T00:00:00Z",
                "source_id": "src-late",
            }
        )
        self.store.add_event(
            {
                "event_id": "event-rewrite",
                "project_id": "project-1",
                "event_type": "rewrite",
                "event_at": "2024-01-15T00:00:00Z",
                "known_at": "2024-03-01T00:00:00Z",
                "stage": "writing",
                "source_id": "src-early",
                "details": {"summary": "Подтверждённое переписывание"},
            }
        )
        # Событие произошло до раннего cutoff, но стало известно только в 2025.
        self.store.add_event(
            {
                "event_id": "event-director-change",
                "project_id": "project-1",
                "event_type": "director_change",
                "event_at": "2024-04-01T00:00:00Z",
                "known_at": "2025-02-01T00:00:00Z",
                "stage": "production",
                "source_id": "src-late",
                "details": {"from": "A", "to": "B"},
            }
        )
        self.store.add_consultancy(
            {
                "engagement_id": "consult-story",
                "project_id": "project-1",
                "entity_id": "consultancy-1",
                "scope": "story",
                "stage": "writing",
                "known_at": "2024-03-15T00:00:00Z",
                "source_id": "src-early",
            }
        )
        self.store.add_consultancy(
            {
                "engagement_id": "consult-sensitivity",
                "project_id": "project-1",
                "entity_id": "consultancy-1",
                "scope": "sensitivity",
                "stage": "post_production",
                "known_at": "2025-03-15T00:00:00Z",
                "source_id": "src-late",
            }
        )

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def test_features_as_of_excludes_facts_known_later(self):
        features = self.store.features_as_of(
            "project-1", "2024-06-01T00:00:00Z"
        )
        self.assertEqual(features["production_franchise_known"], 1.0)
        self.assertEqual(features["production_shared_universe_known"], 1.0)
        self.assertEqual(features["production_installment_index"], 4.0)
        self.assertEqual(features["production_studio_count"], 1.0)
        self.assertEqual(features["production_producer_count"], 0.0)
        self.assertEqual(features["production_change_count"], 1.0)
        self.assertEqual(features["production_rewrite_count"], 1.0)
        self.assertEqual(features["production_director_change_count"], 0.0)
        self.assertEqual(features["production_consultancy_count"], 1.0)
        self.assertEqual(features["production_external_consultancy_present"], 1.0)
        self.assertEqual(features["production_consultancy_story_count"], 1.0)
        self.assertEqual(features["production_consultancy_sensitivity_count"], 0.0)

    def test_identity_is_hidden_before_it_was_known(self):
        features = self.store.features_as_of(
            "project-1", "2023-12-01T00:00:00Z"
        )
        self.assertEqual(features["production_franchise_known"], 0.0)
        self.assertEqual(features["production_shared_universe_known"], 0.0)
        self.assertEqual(features["production_installment_index"], 0.0)
        self.assertEqual(features["production_change_count"], 0.0)
        self.assertEqual(features["production_consultancy_count"], 0.0)

    def test_late_snapshot_sees_late_facts_without_assigning_polarity(self):
        features = self.store.features_as_of(
            "project-1", "2025-04-01T00:00:00Z"
        )
        self.assertEqual(features["production_producer_count"], 1.0)
        self.assertEqual(features["production_director_change_count"], 1.0)
        self.assertEqual(features["production_consultancy_sensitivity_count"], 1.0)
        self.assertTrue(all("bad" not in key and "good" not in key for key in features))

    def test_timeline_uses_known_at_not_event_at(self):
        early = self.store.timeline_as_of(
            "project-1", "2024-06-01T00:00:00Z"
        )
        self.assertEqual([item["event_id"] for item in early], ["event-rewrite"])
        self.assertEqual(early[0]["source"]["url"], "https://example.test/early")

        late = self.store.timeline_as_of(
            "project-1", "2025-04-01T00:00:00Z"
        )
        self.assertEqual(
            [item["event_id"] for item in late],
            ["event-rewrite", "event-director-change"],
        )

    def test_provenance_is_required_for_facts(self):
        with self.assertRaises(ProductionContextValidationError):
            self.store.add_event(
                {
                    "project_id": "project-1",
                    "event_type": "rewrite",
                    "known_at": "2024-05-01T00:00:00Z",
                    "stage": "writing",
                    "source_id": "missing-source",
                }
            )

    def test_consultancy_entity_must_be_neutral_consultancy_kind(self):
        with self.assertRaises(ProductionContextValidationError):
            self.store.add_consultancy(
                {
                    "project_id": "project-1",
                    "entity_id": "studio-1",
                    "scope": "story",
                    "stage": "writing",
                    "known_at": "2024-05-01T00:00:00Z",
                    "source_id": "src-early",
                }
            )


if __name__ == "__main__":
    unittest.main()
