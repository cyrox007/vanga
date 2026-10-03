from __future__ import annotations

import unittest

from src.story_semantics import RuleBasedStorySemanticEnricher


class StorySemanticEnrichmentTests(unittest.TestCase):
    def setUp(self):
        self.enricher = RuleBasedStorySemanticEnricher()

    def _extract(self, text: str, language: str = "en"):
        return self.enricher.extract(
            text,
            source_id=f"test:{language}",
            language=language,
        )

    def test_extracts_worldbuilding_theme_and_ending_only_from_explicit_markers(self):
        result = self._extract(
            "The kingdom follows a magical rule that forbids travel. "
            "The story explores the theme of responsibility. "
            "In the end, Alice Carter returns home."
        )
        kinds = [node.kind for node in result.story_map.nodes]

        self.assertIn("worldbuilding", kinds)
        self.assertIn("theme", kinds)
        self.assertIn("ending", kinds)
        rules = {item.rule for item in result.evidence}
        self.assertIn("worldbuilding_lexical_marker", rules)
        self.assertIn("theme_explicit_marker", rules)
        self.assertIn("ending_explicit_marker", rules)

    def test_relationship_requires_two_explicit_characters_and_marker(self):
        result = self._extract(
            "Alice Carter is the sister of Bob Miller. "
            "Alice Carter leaves the house."
        )
        relationships = [node for node in result.story_map.nodes if node.kind == "relationship"]
        self.assertEqual(len(relationships), 1)

        relation_node = relationships[0]
        linked_characters = {
            relation.target
            for relation in result.story_map.relations
            if relation.kind == "relates_to" and relation.source == relation_node.key
        }
        self.assertEqual(len(linked_characters), 2)

    def test_because_clause_adds_explanation_event_and_relation(self):
        result = self._extract(
            "Alice Carter stays home because the storm blocks the road."
        )
        explanations = [
            relation for relation in result.story_map.relations if relation.kind == "explains"
        ]
        self.assertEqual(len(explanations), 1)
        by_key = {node.key: node for node in result.story_map.nodes}
        self.assertIn("storm blocks the road", by_key[explanations[0].source].label.lower())
        self.assertIn(
            "because_clause",
            {item.rule for item in result.evidence},
        )

    def test_dependency_and_goal_are_separate_structural_relations(self):
        result = self._extract(
            "Alice Carter escapes only if the gate opens. "
            "Alice Carter enters the tower in order to rescue Bob Miller."
        )
        kinds = [relation.kind for relation in result.story_map.relations]
        self.assertIn("depends_on", kinds)
        self.assertIn("motivates", kinds)
        semantic_rules = {item.rule for item in result.evidence}
        self.assertIn("dependency_marker", semantic_rules)
        self.assertIn("explicit_goal_marker", semantic_rules)

    def test_single_recent_character_coreference_is_conservative(self):
        result = self._extract(
            "Alice Carter enters the station. She finds a hidden letter."
        )
        coref = [
            item
            for item in result.evidence
            if item.rule == "single_recent_character_coreference"
        ]
        self.assertEqual(len(coref), 1)
        self.assertLess(coref[0].confidence, 0.6)

    def test_ambiguous_previous_characters_disable_coreference(self):
        result = self._extract(
            "Alice Carter meets Bob Miller at the station. She finds a hidden letter."
        )
        coref = [
            item
            for item in result.evidence
            if item.rule == "single_recent_character_coreference"
        ]
        self.assertEqual(coref, [])

    def test_russian_markers_are_supported(self):
        result = self._extract(
            "Анна Петрова остаётся дома, потому что магическое правило запрещает выход. "
            "Она идёт в башню, чтобы спасти Бориса Смирнова. "
            "В финале Анна Петрова возвращается домой.",
            language="ru",
        )
        kinds = {node.kind for node in result.story_map.nodes}
        relation_kinds = {relation.kind for relation in result.story_map.relations}
        self.assertIn("worldbuilding", kinds)
        self.assertIn("ending", kinds)
        self.assertIn("motivation", kinds)
        self.assertIn("explains", relation_kinds)
        self.assertIn("motivates", relation_kinds)

    def test_same_input_produces_same_derived_map(self):
        text = "Alice Carter stays home because the storm blocks the road."
        first = self._extract(text)
        second = self._extract(text)
        self.assertEqual(first.story_map, second.story_map)
        self.assertEqual(first.evidence, second.evidence)


if __name__ == "__main__":
    unittest.main()
