from __future__ import annotations

import unittest

from src.adaptation_analysis import AdaptationValidationError
from src.story_alignment import BilingualStoryMapMerger, SourceAdaptationAligner
from src.story_diff import StoryMap


class StoryAlignmentTests(unittest.TestCase):
    @staticmethod
    def _map(map_id: str, language: str, nodes: list[dict], relations: list[dict] | None = None) -> StoryMap:
        return StoryMap.from_dict(
            {
                "map_id": map_id,
                "language": language,
                "nodes": nodes,
                "relations": relations or [],
            }
        )

    def test_bilingual_merge_auto_merges_character_by_transliteration_but_not_events(self):
        ru = self._map(
            "ru-map",
            "ru",
            [
                {"key": "ru-anna", "kind": "character", "label": "Анна", "confidence": 0.8},
                {"key": "ru-event", "kind": "event", "label": "Анна входит в город", "confidence": 0.7},
            ],
            [{"kind": "relates_to", "source": "ru-anna", "target": "ru-event"}],
        )
        en = self._map(
            "en-map",
            "en",
            [
                {"key": "en-anna", "kind": "character", "label": "Anna", "confidence": 0.82},
                {"key": "en-event", "kind": "event", "label": "Anna enters the city", "confidence": 0.72},
            ],
            [{"kind": "relates_to", "source": "en-anna", "target": "en-event"}],
        )

        merged = BilingualStoryMapMerger.merge(
            [ru, en],
            canonical_map_id="canonical:source",
        )
        nodes = merged["story_map"]["nodes"]
        characters = [item for item in nodes if item["kind"] == "character"]
        events = [item for item in nodes if item["kind"] == "event"]

        self.assertEqual(len(characters), 1)
        self.assertEqual(len(events), 2)
        evidence = next(
            item for item in merged["merge_evidence"] if item["canonical_key"] == characters[0]["key"]
        )
        self.assertEqual({row["node_key"] for row in evidence["members"]}, {"ru-anna", "en-anna"})
        self.assertTrue(
            all(row["method"] == "character_transliteration" for row in evidence["members"])
        )

    def test_explicit_alias_handles_nontrivial_name_translation(self):
        ru = self._map(
            "ru-map",
            "ru",
            [{"key": "ru-jon", "kind": "character", "label": "Джон Сноу"}],
        )
        en = self._map(
            "en-map",
            "en",
            [{"key": "en-jon", "kind": "character", "label": "Jon Snow"}],
        )
        merged = BilingualStoryMapMerger.merge(
            [ru, en],
            canonical_map_id="canonical:source",
            aliases=[
                {
                    "alias_id": "jon-snow",
                    "kind": "character",
                    "labels": ["Джон Сноу", "Jon Snow"],
                    "canonical_label": "Jon Snow / Джон Сноу",
                }
            ],
        )

        self.assertEqual(len(merged["story_map"]["nodes"]), 1)
        self.assertEqual(merged["story_map"]["nodes"][0]["label"], "Jon Snow / Джон Сноу")

    def test_source_adaptation_alignment_auto_matches_character_only(self):
        source = self._map(
            "source",
            "canonical",
            [
                {"key": "source-anna", "kind": "character", "label": "Anna"},
                {"key": "source-event", "kind": "event", "label": "Anna enters the city"},
            ],
        )
        adaptation = self._map(
            "adaptation",
            "canonical",
            [
                {"key": "adapt-anna", "kind": "character", "label": "Анна"},
                {"key": "adapt-event", "kind": "event", "label": "Анна приезжает в город"},
            ],
        )

        result = SourceAdaptationAligner.align(source, adaptation)
        by_key = {item["key"]: item for item in result["story_map"]["nodes"]}

        self.assertEqual(by_key["adapt-anna"]["maps_from"], ["source-anna"])
        self.assertEqual(by_key["adapt-event"]["maps_from"], [])
        self.assertIn("source-event", result["unmatched_source_keys"])
        self.assertIn("adapt-event", result["unmatched_adaptation_keys"])

    def test_explicit_many_to_one_match_produces_storydiff_merge(self):
        source = self._map(
            "source",
            "canonical",
            [
                {"key": "friend-a", "kind": "character", "label": "Friend A", "importance": 0.6},
                {"key": "friend-b", "kind": "character", "label": "Friend B", "importance": 0.5},
            ],
        )
        adaptation = self._map(
            "adaptation",
            "canonical",
            [
                {"key": "friend-combined", "kind": "character", "label": "Combined Friend"},
            ],
        )

        result = SourceAdaptationAligner.align(
            source,
            adaptation,
            explicit_matches=[
                {
                    "adaptation_key": "friend-combined",
                    "source_keys": ["friend-a", "friend-b"],
                    "confidence": 0.95,
                    "note": "Ручная проверка merge персонажей",
                }
            ],
        )

        node = result["story_map"]["nodes"][0]
        self.assertEqual(node["maps_from"], ["friend-a", "friend-b"])
        merged = [item for item in result["story_diff"] if item["change_type"] == "merged"]
        self.assertEqual(len(merged), 2)

    def test_ambiguous_fuzzy_character_match_is_not_auto_accepted(self):
        source = self._map(
            "source",
            "canonical",
            [
                {"key": "anna-bell", "kind": "character", "label": "Anna Bell"},
                {"key": "anna-belle", "kind": "character", "label": "Anna Belle"},
            ],
        )
        adaptation = self._map(
            "adaptation",
            "canonical",
            [{"key": "anna-bele", "kind": "character", "label": "Anna Bele"}],
        )

        result = SourceAdaptationAligner.align(source, adaptation)

        self.assertEqual(result["story_map"]["nodes"][0]["maps_from"], [])
        self.assertEqual(len(result["ambiguous_matches"]), 1)
        self.assertEqual(result["ambiguous_matches"][0]["adaptation_key"], "anna-bele")

    def test_explicit_match_rejects_kind_mismatch(self):
        source = self._map(
            "source",
            "canonical",
            [{"key": "source-event", "kind": "event", "label": "Event"}],
        )
        adaptation = self._map(
            "adaptation",
            "canonical",
            [{"key": "adapt-character", "kind": "character", "label": "Event"}],
        )

        with self.assertRaises(AdaptationValidationError):
            SourceAdaptationAligner.align(
                source,
                adaptation,
                explicit_matches=[
                    {
                        "adaptation_key": "adapt-character",
                        "source_keys": ["source-event"],
                    }
                ],
            )


if __name__ == "__main__":
    unittest.main()
