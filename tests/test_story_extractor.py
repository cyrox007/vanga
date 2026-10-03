from __future__ import annotations

import unittest

from src.adaptation_analysis import AdaptationValidationError
from src.story_extractor import RuleBasedStoryExtractor
from src.story_diff import StoryMap


class StoryExtractorTests(unittest.TestCase):
    def test_russian_summary_builds_events_character_motivation_and_causal_relation(self):
        text = (
            "Анна встречает Бориса Иванова в старом городе. "
            "Анна хочет спасти город от надвигающейся угрозы. "
            "Поэтому Анна отправляется в путь и ищет союзников."
        )
        result = RuleBasedStoryExtractor().extract(
            text,
            source_id="ru-summary",
            language="ru",
        )
        nodes = list(result.story_map.nodes)
        relations = list(result.story_map.relations)

        self.assertEqual(result.metadata["event_count"], 3)
        self.assertGreaterEqual(result.metadata["character_count"], 2)
        self.assertEqual(result.metadata["motivation_count"], 1)
        self.assertEqual(result.metadata["alignment_status"], "not_aligned")
        self.assertTrue(any(node.kind == "character" and node.label == "Анна" for node in nodes))
        self.assertTrue(
            any(node.kind == "character" and node.label == "Бориса Иванова" for node in nodes)
        )
        self.assertTrue(any(node.kind == "motivation" for node in nodes))
        self.assertTrue(any(rel.kind == "causes" for rel in relations))
        self.assertTrue(any(rel.kind == "motivates" for rel in relations))

    def test_english_summary_is_deterministic(self):
        text = (
            "Alice Carter meets Robert Stone in London. "
            "Alice Carter wants to find her missing brother. "
            "Therefore Alice Carter follows Robert Stone across the city."
        )
        extractor = RuleBasedStoryExtractor()
        first = extractor.extract(text, source_id="en-summary", language="en")
        second = extractor.extract(text, source_id="en-summary", language="en")
        self.assertEqual(first.as_dict(), second.as_dict())
        self.assertEqual(first.story_map.map_id, second.story_map.map_id)

    def test_every_node_has_evidence_and_hash_not_raw_excerpt(self):
        text = (
            "Alice Carter arrives in London. "
            "Alice Carter wants to meet Robert Stone."
        )
        result = RuleBasedStoryExtractor().extract(
            text,
            source_id="evidence-test",
            language="en",
        )
        node_keys = {node.key for node in result.story_map.nodes}
        evidence_keys = {item.node_key for item in result.evidence}
        self.assertTrue(node_keys.issubset(evidence_keys))
        for item in result.evidence:
            payload = item.as_dict()
            self.assertEqual(len(payload["text_sha256"]), 64)
            self.assertNotIn("excerpt", payload)
            self.assertLess(item.char_start, item.char_end)
            self.assertTrue(item.locator.startswith("sentence:"))

    def test_raw_story_map_roundtrips_through_canonical_validator(self):
        result = RuleBasedStoryExtractor().extract(
            "Alice Carter enters the station. Alice Carter leaves the station.",
            source_id="roundtrip",
            language="en",
        )
        restored = StoryMap.from_dict(result.as_dict()["story_map"])
        self.assertEqual(restored, result.story_map)

    def test_sentence_limit_is_explicitly_reported(self):
        text = " ".join(
            f"Alice Carter visits location number {index}." for index in range(5)
        )
        result = RuleBasedStoryExtractor(max_sentences=2).extract(
            text,
            source_id="truncated",
            language="en",
        )
        self.assertTrue(result.metadata["truncated"])
        self.assertEqual(result.metadata["sentence_count_total"], 5)
        self.assertEqual(result.metadata["sentence_count_used"], 2)
        self.assertEqual(result.metadata["event_count"], 2)

    def test_empty_or_non_sentence_text_is_rejected(self):
        extractor = RuleBasedStoryExtractor()
        with self.assertRaises(AdaptationValidationError):
            extractor.extract("", source_id="empty", language="ru")
        with self.assertRaises(AdaptationValidationError):
            extractor.extract("Да.", source_id="too-short", language="ru")


if __name__ == "__main__":
    unittest.main()
