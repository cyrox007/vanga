from __future__ import annotations

import unittest

from src.story_diff import StoryDiffAnalyzer, StoryMap


class StoryDiffTests(unittest.TestCase):
    def source_map(self) -> StoryMap:
        return StoryMap.from_dict(
            {
                "map_id": "source:demo",
                "language": "canonical",
                "nodes": [
                    {
                        "key": "lore-rule",
                        "kind": "worldbuilding",
                        "label": "Правило мира объясняет запрет",
                        "importance": 0.9,
                        "confidence": 0.95,
                    },
                    {
                        "key": "hero-motive",
                        "kind": "motivation",
                        "label": "Герой принимает запрет",
                        "importance": 0.8,
                        "confidence": 0.9,
                    },
                    {
                        "key": "friend-a",
                        "kind": "character",
                        "label": "Друг героя A",
                        "importance": 0.5,
                    },
                    {
                        "key": "friend-b",
                        "kind": "character",
                        "label": "Друг героя B",
                        "importance": 0.4,
                    },
                ],
                "relations": [
                    {
                        "kind": "explains",
                        "source": "lore-rule",
                        "target": "hero-motive",
                        "importance": 0.85,
                        "confidence": 0.9,
                    }
                ],
            }
        )

    def adaptation_map(self) -> StoryMap:
        return StoryMap.from_dict(
            {
                "map_id": "film:demo",
                "language": "canonical",
                "nodes": [
                    {
                        "key": "hero-motive",
                        "kind": "motivation",
                        "label": "Герой принимает запрет",
                        "importance": 0.8,
                        "confidence": 0.9,
                    },
                    {
                        "key": "friend-combined",
                        "kind": "character",
                        "label": "Объединённый друг героя",
                        "importance": 0.6,
                        "confidence": 0.85,
                        "maps_from": ["friend-a", "friend-b"],
                    },
                    {
                        "key": "new-villain-scene",
                        "kind": "event",
                        "label": "Новая сцена злодея",
                        "importance": 0.5,
                        "confidence": 0.8,
                    },
                ],
                "relations": [],
            }
        )

    def test_detects_removed_lore_and_broken_explanation_link(self):
        annotations = StoryDiffAnalyzer.compare(
            self.source_map(),
            self.adaptation_map(),
        )

        removed_lore = next(
            item
            for item in annotations
            if item["change_type"] == "removed"
            and item["dimension"] == "worldbuilding"
            and item["layer"] == "observation"
        )
        relation_loss = next(
            item
            for item in annotations
            if item["dimension"] == "causal_coherence"
            and item["layer"] == "structural_consequence"
        )

        self.assertIn("Правило мира", removed_lore["claim"])
        self.assertEqual(relation_loss["parent_id"], removed_lore["annotation_id"])
        self.assertIn("explains", relation_loss["tags"])

    def test_detects_character_merge_and_new_material(self):
        annotations = StoryDiffAnalyzer.compare(
            self.source_map(),
            self.adaptation_map(),
        )

        merged = [
            item
            for item in annotations
            if item["change_type"] == "merged"
            and item["dimension"] == "characters"
        ]
        added = [
            item
            for item in annotations
            if item["change_type"] == "added"
            and item["dimension"] == "plot"
        ]

        self.assertEqual(len(merged), 2)
        self.assertEqual(len(added), 1)
        self.assertIn("Новая сцена злодея", added[0]["claim"])

    def test_preserved_relation_produces_no_causal_warning(self):
        film = StoryMap.from_dict(
            {
                "map_id": "film:preserved",
                "nodes": [
                    {
                        "key": "lore-rule",
                        "kind": "worldbuilding",
                        "label": "Правило мира объясняет запрет",
                    },
                    {
                        "key": "hero-motive",
                        "kind": "motivation",
                        "label": "Герой принимает запрет",
                    },
                    {
                        "key": "friend-a",
                        "kind": "character",
                        "label": "Друг героя A",
                    },
                    {
                        "key": "friend-b",
                        "kind": "character",
                        "label": "Друг героя B",
                    },
                ],
                "relations": [
                    {
                        "kind": "explains",
                        "source": "lore-rule",
                        "target": "hero-motive",
                    }
                ],
            }
        )

        annotations = StoryDiffAnalyzer.compare(self.source_map(), film)

        self.assertFalse(
            any(item["dimension"] == "causal_coherence" for item in annotations)
        )


if __name__ == "__main__":
    unittest.main()
