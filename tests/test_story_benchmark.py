from __future__ import annotations

import unittest

from src.adaptation_analysis import AdaptationValidationError
from src.story_benchmark import (
    AlignmentGoldCase,
    StoryAlignmentBenchmark,
    StoryMatchCandidateGenerator,
)
from src.story_diff import StoryMap


class StoryBenchmarkTests(unittest.TestCase):
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

    def test_character_context_ranks_correct_event_candidate(self):
        source = self._map(
            "source",
            [
                {"key": "s-alice", "kind": "character", "label": "Alice"},
                {"key": "s-bob", "kind": "character", "label": "Bob"},
                {"key": "s-city", "kind": "event", "label": "Alice arrives in the capital"},
                {"key": "s-cave", "kind": "event", "label": "Bob enters a dark cave"},
            ],
            [
                {"kind": "relates_to", "source": "s-alice", "target": "s-city"},
                {"kind": "relates_to", "source": "s-bob", "target": "s-cave"},
            ],
        )
        adaptation = self._map(
            "adaptation",
            [
                {"key": "a-alice", "kind": "character", "label": "Алиса"},
                {"key": "a-event", "kind": "event", "label": "Героиня приезжает в столицу"},
            ],
            [{"kind": "relates_to", "source": "a-alice", "target": "a-event"}],
        )

        result = StoryMatchCandidateGenerator.generate(
            source,
            adaptation,
            known_character_matches={"a-alice": "s-alice"},
            min_score=0.1,
        )
        group = next(row for row in result["candidate_groups"] if row["adaptation_key"] == "a-event")

        self.assertFalse(result["auto_accept"])
        self.assertEqual(group["candidates"][0]["source_key"], "s-city")
        self.assertEqual(group["candidates"][0]["components"]["participant_overlap"], 1.0)

    def test_candidate_generation_never_changes_maps_from(self):
        source = self._map(
            "source",
            [{"key": "s-event", "kind": "event", "label": "The gate opens"}],
        )
        adaptation = self._map(
            "adaptation",
            [{"key": "a-event", "kind": "event", "label": "The gate opens"}],
        )

        before = adaptation.nodes[0].maps_from
        result = StoryMatchCandidateGenerator.generate(source, adaptation)

        self.assertEqual(adaptation.nodes[0].maps_from, before)
        self.assertFalse(result["auto_accept"])
        self.assertEqual(result["candidate_groups"][0]["candidates"][0]["score"], 0.75)

    def test_equal_top_candidates_are_marked_ambiguous(self):
        source = self._map(
            "source",
            [
                {"key": "s-a", "kind": "event", "label": "The gate opens"},
                {"key": "s-b", "kind": "event", "label": "The gate opens"},
            ],
        )
        adaptation = self._map(
            "adaptation",
            [{"key": "a-event", "kind": "event", "label": "The gate opens"}],
        )

        result = StoryMatchCandidateGenerator.generate(source, adaptation)
        group = result["candidate_groups"][0]
        self.assertTrue(group["ambiguous"])
        self.assertEqual(group["candidates"][0]["score"], group["candidates"][1]["score"])

    def test_benchmark_counts_true_false_and_missing_pairs(self):
        source = self._map(
            "source",
            [
                {"key": "s-char-a", "kind": "character", "label": "A"},
                {"key": "s-char-b", "kind": "character", "label": "B"},
                {"key": "s-event", "kind": "event", "label": "Event"},
            ],
        )
        adaptation = self._map(
            "adaptation",
            [
                {"key": "a-combined", "kind": "character", "label": "Combined"},
                {"key": "a-event", "kind": "event", "label": "Event"},
            ],
        )
        predicted = self._map(
            "predicted",
            [
                {
                    "key": "a-combined",
                    "kind": "character",
                    "label": "Combined",
                    "maps_from": ["s-char-a"],
                },
                {
                    "key": "a-event",
                    "kind": "event",
                    "label": "Event",
                    "maps_from": ["s-event"],
                },
            ],
        )
        gold = {
            "case_id": "case-1",
            "split": "blind",
            "source_map_id": "source",
            "adaptation_map_id": "adaptation",
            "matches": [
                {
                    "adaptation_key": "a-combined",
                    "source_keys": ["s-char-a", "s-char-b"],
                },
                {
                    "adaptation_key": "a-event",
                    "source_keys": ["s-event"],
                },
            ],
        }

        result = StoryAlignmentBenchmark.evaluate(
            source=source,
            adaptation=adaptation,
            gold=gold,
            predicted_map=predicted,
        )

        self.assertEqual(result["overall"]["tp"], 2)
        self.assertEqual(result["overall"]["fp"], 0)
        self.assertEqual(result["overall"]["fn"], 1)
        self.assertEqual(result["by_kind"]["event"]["recall"], 1.0)
        self.assertEqual(result["by_kind"]["character"]["recall"], 0.5)
        self.assertEqual(result["split"], "blind")

    def test_false_positive_is_penalized(self):
        source = self._map(
            "source",
            [
                {"key": "s-event-a", "kind": "event", "label": "A"},
                {"key": "s-event-b", "kind": "event", "label": "B"},
            ],
        )
        adaptation = self._map(
            "adaptation",
            [{"key": "a-event", "kind": "event", "label": "A"}],
        )
        predicted = self._map(
            "predicted",
            [
                {
                    "key": "a-event",
                    "kind": "event",
                    "label": "A",
                    "maps_from": ["s-event-a", "s-event-b"],
                }
            ],
        )
        gold = {
            "case_id": "case-fp",
            "split": "development",
            "matches": [
                {"adaptation_key": "a-event", "source_keys": ["s-event-a"]}
            ],
        }

        result = StoryAlignmentBenchmark.evaluate(
            source=source,
            adaptation=adaptation,
            gold=gold,
            predicted_map=predicted,
        )
        self.assertEqual(result["overall"]["tp"], 1)
        self.assertEqual(result["overall"]["fp"], 1)
        self.assertEqual(result["overall"]["precision"], 0.5)

    def test_gold_rejects_kind_mismatch_and_unknown_split(self):
        source = self._map(
            "source",
            [{"key": "s-event", "kind": "event", "label": "Event"}],
        )
        adaptation = self._map(
            "adaptation",
            [{"key": "a-char", "kind": "character", "label": "Character"}],
        )

        with self.assertRaises(AdaptationValidationError):
            AlignmentGoldCase.from_dict(
                {
                    "case_id": "bad-kind",
                    "split": "blind",
                    "matches": [
                        {"adaptation_key": "a-char", "source_keys": ["s-event"]}
                    ],
                },
                source=source,
                adaptation=adaptation,
            )

        with self.assertRaises(AdaptationValidationError):
            AlignmentGoldCase.from_dict(
                {
                    "case_id": "bad-split",
                    "split": "production",
                    "matches": [],
                },
                source=source,
                adaptation=adaptation,
            )


if __name__ == "__main__":
    unittest.main()
