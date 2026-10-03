from __future__ import annotations

import unittest

from src.story_diff import StoryMap
from src.story_transformations import LexicalToneAnalyzer, StoryTransformationAnalyzer


class StoryTransformationTests(unittest.TestCase):
    @staticmethod
    def _map(map_id: str, nodes: list[dict], relations: list[dict] | None = None) -> StoryMap:
        return StoryMap.from_dict(
            {
                "map_id": map_id,
                "language": "canonical",
                "nodes": nodes,
                "relations": relations or [],
            }
        )

    def test_many_to_one_confirmed_mapping_is_compression(self):
        source = self._map(
            "source",
            [
                {"key": "e1", "kind": "event", "label": "Первое событие", "importance": 0.7},
                {"key": "e2", "kind": "event", "label": "Второе событие", "importance": 0.8},
            ],
        )
        adaptation = self._map(
            "film",
            [
                {
                    "key": "combined",
                    "kind": "event",
                    "label": "Объединённое событие",
                    "importance": 0.75,
                    "maps_from": ["e1", "e2"],
                }
            ],
        )

        result = StoryTransformationAnalyzer.compare(source, adaptation)
        compressed = [x for x in result["annotations"] if x["change_type"] == "compressed"]

        self.assertEqual(len(compressed), 1)
        self.assertEqual(compressed[0]["dimension"], "plot")
        self.assertEqual(result["summary"]["compressed"], 1)
        self.assertEqual(compressed[0]["polarity"], 0.0)

    def test_similar_labels_without_alignment_are_not_compression(self):
        source = self._map(
            "source",
            [
                {"key": "e1", "kind": "event", "label": "Герой приходит в город"},
                {"key": "e2", "kind": "event", "label": "Герой входит в город"},
            ],
        )
        adaptation = self._map(
            "film",
            [{"key": "x", "kind": "event", "label": "Герой приходит в город"}],
        )

        result = StoryTransformationAnalyzer.compare(source, adaptation)
        self.assertEqual(result["summary"]["compressed"], 0)
        self.assertEqual(result["annotations"], [])

    def test_relation_neighborhood_change_marks_confirmed_node_as_rewritten(self):
        source = self._map(
            "source",
            [
                {"key": "a", "kind": "event", "label": "A"},
                {"key": "b", "kind": "event", "label": "B", "importance": 0.9},
                {"key": "c", "kind": "motivation", "label": "C"},
            ],
            [
                {"kind": "causes", "source": "a", "target": "b"},
                {"kind": "explains", "source": "b", "target": "c"},
            ],
        )
        adaptation = self._map(
            "film",
            [
                {"key": "fa", "kind": "event", "label": "A film", "maps_from": ["a"]},
                {"key": "fb", "kind": "event", "label": "B film", "maps_from": ["b"]},
                {"key": "fc", "kind": "motivation", "label": "C film", "maps_from": ["c"]},
            ],
            [
                {"kind": "causes", "source": "fa", "target": "fb"},
                {"kind": "motivates", "source": "fc", "target": "fb"},
            ],
        )

        result = StoryTransformationAnalyzer.compare(source, adaptation)
        rewritten = [x for x in result["annotations"] if x["change_type"] == "rewritten"]

        self.assertTrue(any("B" in x["claim"] for x in rewritten))
        self.assertGreaterEqual(result["summary"]["rewritten"], 1)

    def test_relation_loss_without_replacement_is_not_mislabeled_rewrite(self):
        source = self._map(
            "source",
            [
                {"key": "a", "kind": "event", "label": "A"},
                {"key": "b", "kind": "event", "label": "B"},
            ],
            [{"kind": "causes", "source": "a", "target": "b"}],
        )
        adaptation = self._map(
            "film",
            [
                {"key": "fa", "kind": "event", "label": "A", "maps_from": ["a"]},
                {"key": "fb", "kind": "event", "label": "B", "maps_from": ["b"]},
            ],
            [],
        )

        result = StoryTransformationAnalyzer.compare(source, adaptation)
        self.assertEqual(result["summary"]["rewritten"], 0)

    def test_confirmed_event_order_inversion_is_reordered(self):
        source = self._map(
            "source",
            [
                {"key": "e1", "kind": "event", "label": "1"},
                {"key": "e2", "kind": "event", "label": "2"},
                {"key": "e3", "kind": "event", "label": "3"},
            ],
        )
        adaptation = self._map(
            "film",
            [
                {"key": "f3", "kind": "event", "label": "3", "maps_from": ["e3"]},
                {"key": "f1", "kind": "event", "label": "1", "maps_from": ["e1"]},
                {"key": "f2", "kind": "event", "label": "2", "maps_from": ["e2"]},
            ],
        )

        result = StoryTransformationAnalyzer.compare(source, adaptation)
        reordered = [x for x in result["annotations"] if x["change_type"] == "reordered"]

        self.assertGreaterEqual(len(reordered), 2)
        self.assertEqual(result["summary"]["reordered"], len(reordered))

    def test_two_events_are_not_enough_for_reorder_claim(self):
        source = self._map(
            "source",
            [
                {"key": "e1", "kind": "event", "label": "1"},
                {"key": "e2", "kind": "event", "label": "2"},
            ],
        )
        adaptation = self._map(
            "film",
            [
                {"key": "f2", "kind": "event", "label": "2", "maps_from": ["e2"]},
                {"key": "f1", "kind": "event", "label": "1", "maps_from": ["e1"]},
            ],
        )

        result = StoryTransformationAnalyzer.compare(source, adaptation)
        self.assertEqual(result["summary"]["reordered"], 0)


class LexicalToneTests(unittest.TestCase):
    def test_profile_is_measurement_not_quality_score(self):
        profile = LexicalToneAnalyzer.profile(
            "The family faces danger and grief but keeps hope and seeks peace.",
            language="en",
        ).as_dict()

        self.assertEqual(profile["quality_sign"], None)
        self.assertGreater(profile["marker_counts"]["threat"], 0)
        self.assertGreater(profile["marker_counts"]["hope"], 0)
        self.assertGreater(profile["word_count"], 0)
        self.assertEqual(len(profile["text_sha256"]), 64)

    def test_compare_keeps_category_deltas_and_coverage_warning(self):
        source = LexicalToneAnalyzer.profile(
            "Семья и дружба помогают герою сохранить надежду и мир.",
            language="ru",
        )
        adaptation = LexicalToneAnalyzer.profile(
            "Враг несёт угрозу, страх и смерть.",
            language="ru",
        )
        result = LexicalToneAnalyzer.compare(source, adaptation)

        self.assertEqual(result["quality_sign"], None)
        self.assertGreater(result["category_rate_delta_per_100_words"]["threat"], 0)
        self.assertGreater(result["l1_distance_per_100_words"], 0)

    def test_unsupported_language_is_rejected(self):
        with self.assertRaises(ValueError):
            LexicalToneAnalyzer.profile("Texto de prueba", language="es")


if __name__ == "__main__":
    unittest.main()
