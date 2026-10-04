from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from src.actor_persona import ActorPersonaStore
from src.actor_persona_storymap import ActorPersonaStoryMapLinks


class ActorPersonaStoryMapTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.store = ActorPersonaStore(Path(self.tmp.name) / "actor.duckdb")
        self.store.add_source(
            {
                "source_id": "manual",
                "provider": "manual",
                "retrieved_at": "2026-01-01T00:00:00Z",
                "usage_basis": "structured_annotation",
            }
        )
        self.store.upsert_character(
            {"character_id": "char-1", "canonical_name": "Hero"}
        )
        self.store.add_role_appearance(
            {
                "appearance_id": "role-1",
                "actor_id": "nm1",
                "work_id": "tt1",
                "character_id": "char-1",
                "character_name": "Hero",
                "work_release_at": "2025-12-01T00:00:00Z",
                "known_at": "2025-01-01T00:00:00Z",
                "source_id": "manual",
            }
        )
        self.links = ActorPersonaStoryMapLinks(self.store)

    def tearDown(self) -> None:
        self.links.close()
        self.store.close()
        self.tmp.cleanup()

    def test_storymap_link_respects_cutoff(self) -> None:
        self.links.add_link(
            {
                "link_id": "link-1",
                "appearance_id": "role-1",
                "story_map_id": "story-1",
                "story_node_id": "character-node-1",
                "known_at": "2025-06-01T00:00:00Z",
                "source_id": "manual",
                "confidence": 0.9,
            }
        )
        self.assertEqual(self.links.links_as_of("role-1", "2025-05-01T00:00:00Z"), [])
        rows = self.links.links_as_of("role-1", "2025-07-01T00:00:00Z")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["story_node_id"], "character-node-1")


if __name__ == "__main__":
    unittest.main()
