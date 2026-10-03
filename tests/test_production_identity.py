from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from src.production_context import (
    ProductionContextStore,
    ProductionContextValidationError,
)
from src.production_identity import (
    ProductionIdentityHistory,
    normalize_identity_alias,
)


class ProductionIdentityHistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "production_context.duckdb"
        self.store = ProductionContextStore(self.path)
        self.identity = ProductionIdentityHistory(self.store)

        for source_id, date in (
            ("src-old", "2019-01-01T00:00:00Z"),
            ("src-mid", "2023-01-01T00:00:00Z"),
            ("src-target", "2024-01-01T00:00:00Z"),
            ("src-late", "2025-01-01T00:00:00Z"),
        ):
            self.store.upsert_source(
                {
                    "source_id": source_id,
                    "url": f"https://example.test/{source_id}",
                    "published_at": date,
                    "retrieved_at": date,
                    "confidence": 0.95,
                }
            )

        self.identity.upsert_group(
            {
                "group_id": "franchise-a",
                "kind": "franchise",
                "name": "Franchise A",
            }
        )
        self.identity.upsert_group(
            {
                "group_id": "universe-a",
                "kind": "shared_universe",
                "name": "Universe A",
            }
        )

        for payload in (
            {
                "entity_id": "studio-a",
                "kind": "studio",
                "name": "Marvel Studios",
                "external_id": "wikidata:Q434841",
            },
            {
                "entity_id": "producer-a",
                "kind": "producer",
                "name": "Producer A",
            },
            {
                "entity_id": "lead-a",
                "kind": "creative_lead",
                "name": "Creative Lead A",
            },
        ):
            self.store.upsert_entity(payload)

        self.identity.add_entity_alias(
            {
                "entity_id": "studio-a",
                "alias": "Marvel Studios, LLC",
                "known_at": "2020-01-01T00:00:00Z",
                "source_id": "src-old",
            }
        )
        self.identity.add_group_alias(
            {
                "group_id": "universe-a",
                "alias": "MCU",
                "known_at": "2024-01-01T00:00:00Z",
                "source_id": "src-target",
            }
        )

        projects = (
            ("prior-1", "Prior One", "2020-07-01T00:00:00Z", "2019-01-01T00:00:00Z"),
            ("prior-2", "Prior Two", "2022-07-01T00:00:00Z", "2021-01-01T00:00:00Z"),
            # Фильм уже вышел, но его production identity станет известна только после cutoff.
            ("hidden-old", "Hidden Old", "2021-07-01T00:00:00Z", "2025-01-01T00:00:00Z"),
            ("target", "Target Film", "2025-07-01T00:00:00Z", "2024-01-01T00:00:00Z"),
            # Production links известны, но сам фильм ещё не вышел на cutoff.
            ("future", "Future Film", "2026-07-01T00:00:00Z", "2024-01-01T00:00:00Z"),
        )
        for project_id, title, release_at, identity_known_at in projects:
            self.store.upsert_project(
                {
                    "project_id": project_id,
                    "title": title,
                    "release_at": release_at,
                    "identity_known_at": identity_known_at,
                }
            )

        self._group_link("prior-1", "franchise-a", "src-old", "2019-01-01T00:00:00Z")
        self._group_link("prior-1", "universe-a", "src-old", "2019-01-01T00:00:00Z")
        self._group_link("prior-2", "franchise-a", "src-mid", "2023-01-01T00:00:00Z")
        self._group_link("target", "franchise-a", "src-target", "2024-01-01T00:00:00Z", 3)
        self._group_link("target", "universe-a", "src-target", "2024-01-01T00:00:00Z")
        self._group_link("future", "franchise-a", "src-target", "2024-01-01T00:00:00Z")
        self._group_link("future", "universe-a", "src-target", "2024-01-01T00:00:00Z")
        self._group_link("hidden-old", "franchise-a", "src-late", "2025-01-01T00:00:00Z")
        self._group_link("hidden-old", "universe-a", "src-late", "2025-01-01T00:00:00Z")

        self._entity_link("prior-1", "studio-a", "studio", "src-old", "2019-01-01T00:00:00Z")
        self._entity_link("prior-2", "studio-a", "studio", "src-mid", "2023-01-01T00:00:00Z")
        self._entity_link("prior-2", "producer-a", "producer", "src-mid", "2023-01-01T00:00:00Z")
        self._entity_link("target", "studio-a", "studio", "src-target", "2024-01-01T00:00:00Z")
        self._entity_link("target", "producer-a", "producer", "src-target", "2024-01-01T00:00:00Z")
        self._entity_link("target", "lead-a", "creative_lead", "src-target", "2024-01-01T00:00:00Z")
        self._entity_link("future", "studio-a", "studio", "src-target", "2024-01-01T00:00:00Z")
        self._entity_link("future", "producer-a", "producer", "src-target", "2024-01-01T00:00:00Z")
        self._entity_link("hidden-old", "studio-a", "studio", "src-late", "2025-01-01T00:00:00Z")
        self._entity_link("hidden-old", "producer-a", "producer", "src-late", "2025-01-01T00:00:00Z")

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def _group_link(
        self,
        project_id: str,
        group_id: str,
        source_id: str,
        known_at: str,
        installment_index: int | None = None,
    ) -> None:
        self.identity.link_group(
            {
                "link_id": f"group-{project_id}-{group_id}",
                "project_id": project_id,
                "group_id": group_id,
                "known_at": known_at,
                "source_id": source_id,
                "installment_index": installment_index,
            }
        )

    def _entity_link(
        self,
        project_id: str,
        entity_id: str,
        role: str,
        source_id: str,
        known_at: str,
    ) -> None:
        self.store.link_entity(
            {
                "link_id": f"entity-{project_id}-{entity_id}-{role}",
                "project_id": project_id,
                "entity_id": entity_id,
                "role": role,
                "stage": "production",
                "known_at": known_at,
                "source_id": source_id,
            }
        )

    def test_alias_normalization_is_exact_and_explicit(self):
        self.assertEqual(
            normalize_identity_alias("  MARVEL Studios, LLC  "),
            "marvel studios llc",
        )
        resolved = self.identity.resolve_entity(
            "MARVEL Studios, LLC",
            kind="studio",
            cutoff="2024-06-01T00:00:00Z",
        )
        self.assertEqual(resolved["id"], "studio-a")
        self.assertEqual(resolved["kind"], "studio")
        # Не зарегистрированный укороченный вариант нельзя домыслить fuzzy-поиском.
        self.assertIsNone(self.identity.resolve_entity("Marvel"))

    def test_alias_is_not_visible_before_known_at(self):
        self.assertIsNone(
            self.identity.resolve_group(
                "MCU",
                kind="shared_universe",
                cutoff="2023-12-01T00:00:00Z",
            )
        )
        resolved = self.identity.resolve_group(
            "MCU",
            kind="shared_universe",
            cutoff="2024-06-01T00:00:00Z",
        )
        self.assertEqual(resolved["id"], "universe-a")

    def test_ambiguous_alias_requires_kind(self):
        self.store.upsert_entity(
            {
                "entity_id": "label-a",
                "kind": "production_label",
                "name": "Another Company",
            }
        )
        for entity_id in ("studio-a", "label-a"):
            self.identity.add_entity_alias(
                {
                    "entity_id": entity_id,
                    "alias": "Shared Alias",
                    "known_at": "2024-01-01T00:00:00Z",
                    "source_id": "src-target",
                }
            )
        with self.assertRaises(ProductionContextValidationError):
            self.identity.resolve_entity("Shared Alias")
        resolved = self.identity.resolve_entity("Shared Alias", kind="production_label")
        self.assertEqual(resolved["id"], "label-a")

    def test_history_counts_only_released_and_known_prior_projects(self):
        features = self.identity.history_features_as_of(
            "target",
            "2024-06-01T00:00:00Z",
        )
        self.assertEqual(features["production_franchise_prior_project_count"], 2.0)
        self.assertEqual(features["production_shared_universe_prior_project_count"], 1.0)

        self.assertEqual(features["production_studio_history_known_ratio"], 1.0)
        self.assertEqual(features["production_studio_prior_project_count_mean"], 2.0)
        self.assertEqual(features["production_studio_prior_project_count_max"], 2.0)

        self.assertEqual(features["production_producer_history_known_ratio"], 1.0)
        self.assertEqual(features["production_producer_prior_project_count_mean"], 1.0)
        self.assertEqual(features["production_producer_prior_project_count_max"], 1.0)

        self.assertEqual(features["production_creative_lead_history_known_ratio"], 0.0)
        self.assertEqual(features["production_entity_history_known_ratio"], 2 / 3)
        self.assertEqual(features["production_prior_shared_entity_project_count"], 2.0)
        self.assertEqual(features["production_key_team_repeat_project_count"], 1.0)
        self.assertEqual(features["production_prior_shared_entity_max"], 2.0)

    def test_later_cutoff_can_see_late_disclosed_old_links_but_not_future_release(self):
        features = self.identity.history_features_as_of(
            "target",
            "2025-02-01T00:00:00Z",
        )
        # hidden-old теперь известен и уже вышел; future всё ещё после target release limit.
        self.assertEqual(features["production_franchise_prior_project_count"], 3.0)
        self.assertEqual(features["production_shared_universe_prior_project_count"], 2.0)
        self.assertEqual(features["production_studio_prior_project_count_mean"], 3.0)
        self.assertEqual(features["production_producer_prior_project_count_mean"], 2.0)
        self.assertEqual(features["production_key_team_repeat_project_count"], 2.0)

    def test_group_identity_not_visible_before_target_link_known_at(self):
        features = self.identity.history_features_as_of(
            "target",
            "2023-12-01T00:00:00Z",
        )
        self.assertEqual(features["production_franchise_prior_project_count"], 0.0)
        self.assertEqual(features["production_shared_universe_prior_project_count"], 0.0)


if __name__ == "__main__":
    unittest.main()
