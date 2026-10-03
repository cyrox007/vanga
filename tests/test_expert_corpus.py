from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from src.expert_corpus import ExpertCorpusStore, ExpertCorpusValidationError


class ExpertCorpusTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = ExpertCorpusStore(Path(self.temp.name) / "expert_corpus.duckdb")
        self.store.upsert_profile(
            {
                "expert_id": "red-cynic",
                "display_name": "Красный Циник",
                "focus": ["adaptation", "motivation", "worldbuilding"],
            }
        )
        self.store.upsert_profile(
            {
                "expert_id": "badcomedian",
                "display_name": "BadComedian",
                "focus": ["script_logic", "motivation", "setup_payoff"],
            }
        )
        self.store.upsert_case(
            {
                "case_id": "case-demo",
                "imdb_id": "tt1234567",
                "film_title": "Demo Film",
                "film_year": 2024,
                "source_work_id": "source-demo",
                "split": "blind",
            }
        )
        self.store.upsert_material(
            {
                "material_id": "red-review",
                "expert_id": "red-cynic",
                "title": "Разбор Demo Film",
                "source_url": "https://example.test/red-review",
                "media_type": "video",
                "published_at": "2025-01-02T12:00:00Z",
                "retrieved_at": "2026-10-03T12:00:00Z",
            }
        )
        self.store.upsert_material(
            {
                "material_id": "bad-review",
                "expert_id": "badcomedian",
                "title": "Обзор Demo Film",
                "source_url": "https://example.test/bad-review",
                "media_type": "video",
                "retrieved_at": "2026-10-03T12:00:00Z",
            }
        )
        self.store.link_case_material(
            {
                "case_id": "case-demo",
                "material_id": "red-review",
            }
        )
        self.store.link_case_material(
            {
                "case_id": "case-demo",
                "material_id": "bad-review",
            }
        )

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def _claim(self, *, claim_id: str = "claim-red", material_id: str = "red-review") -> str:
        return self.store.upsert_claim(
            {
                "claim_id": claim_id,
                "case_id": "case-demo",
                "material_id": material_id,
                "dimension": "motivation",
                "change_type": "removed",
                "timecode_or_section": "12:34-13:10",
                "claim_summary": "Удалено объяснение решения персонажа",
                "observation": "В первоисточнике решение предваряется объясняющей сценой, а в фильме её нет.",
                "structural_consequence": "Переход к следующему действию теряет причинную поддержку.",
                "expert_interpretation": "Эксперт считает поступок персонажа из-за этого необоснованным.",
                "confidence": 0.9,
                "story_map_id": "source:canonical",
                "story_diff_annotation_id": "auto-annotation-1",
                "tags": ["context_loss", "motivation_transition"],
            }
        )

    def test_profile_focus_and_claim_tags_accept_json_arrays(self):
        self._claim()
        stats = self.store.corpus_stats()
        self.assertEqual(stats["profiles"], 2)
        self.assertEqual(stats["claims"], 1)

    def test_claim_chain_keeps_support_contradiction_and_interpretation_separate(self):
        self._claim()
        self.store.upsert_evidence(
            {
                "evidence_id": "support-1",
                "claim_id": "claim-red",
                "polarity": "supporting",
                "evidence_kind": "storydiff",
                "description": "StoryDiff показывает удалённый объясняющий элемент.",
                "reference_id": "auto-annotation-1",
                "confidence": 0.95,
            }
        )
        self.store.upsert_evidence(
            {
                "evidence_id": "contradict-1",
                "claim_id": "claim-red",
                "polarity": "contradicting",
                "evidence_kind": "film_summary",
                "description": "Поздняя сцена частично проговаривает ту же мотивацию.",
                "locator": "summary:sentence:18",
                "confidence": 0.7,
            }
        )

        chain = self.store.claim_chain("claim-red")
        self.assertEqual(chain["expert_id"], "red-cynic")
        self.assertEqual(len(chain["evidence"]), 1)
        self.assertEqual(len(chain["contradicting_evidence"]), 1)
        self.assertNotEqual(chain["observation"], chain["expert_interpretation"])
        self.assertEqual(chain["story_diff_annotation_id"], "auto-annotation-1")

    def test_case_summary_does_not_duplicate_claim_count_per_evidence_row(self):
        self._claim()
        for evidence_id, polarity in (
            ("support-1", "supporting"),
            ("contradict-1", "contradicting"),
        ):
            self.store.upsert_evidence(
                {
                    "evidence_id": evidence_id,
                    "claim_id": "claim-red",
                    "polarity": polarity,
                    "evidence_kind": "material_reference",
                    "description": f"Evidence {evidence_id}",
                    "locator": "12:34",
                }
            )

        summary = self.store.case_summary("case-demo")
        row = summary["rows"][0]
        self.assertEqual(row["claim_count"], 1)
        self.assertEqual(row["supporting_evidence_count"], 1)
        self.assertEqual(row["contradicting_evidence_count"], 1)

    def test_expert_profiles_remain_separate(self):
        self._claim(claim_id="claim-red", material_id="red-review")
        self._claim(claim_id="claim-bad", material_id="bad-review")
        summary = self.store.case_summary("case-demo")
        self.assertEqual(set(summary["expert_profiles"]), {"red-cynic", "badcomedian"})
        self.assertEqual(len(summary["rows"]), 2)

    def test_full_review_or_transcript_payload_is_rejected(self):
        with self.assertRaises(ExpertCorpusValidationError):
            self.store.upsert_material(
                {
                    "material_id": "forbidden",
                    "expert_id": "red-cynic",
                    "title": "Forbidden",
                    "source_url": "https://example.test/forbidden",
                    "media_type": "video",
                    "retrieved_at": "2026-10-03T12:00:00Z",
                    "transcript": "Полный текст чужого видео здесь не должен храниться.",
                }
            )

        self._claim()
        with self.assertRaises(ExpertCorpusValidationError):
            self.store.upsert_evidence(
                {
                    "claim_id": "claim-red",
                    "polarity": "supporting",
                    "evidence_kind": "material_reference",
                    "description": "Короткая наша структурированная аннотация",
                    "review_text": "Полный чужой обзор",
                }
            )

    def test_story_references_require_reference_id(self):
        self._claim()
        with self.assertRaises(ExpertCorpusValidationError):
            self.store.upsert_evidence(
                {
                    "claim_id": "claim-red",
                    "polarity": "supporting",
                    "evidence_kind": "storymap",
                    "description": "Есть структурная ссылка, но забыли ID.",
                }
            )

    def test_claim_requires_material_link_to_case(self):
        self.store.upsert_case(
            {
                "case_id": "other-case",
                "film_title": "Other",
                "split": "development",
            }
        )
        with self.assertRaises(ExpertCorpusValidationError):
            self.store.upsert_claim(
                {
                    "claim_id": "bad-link",
                    "case_id": "other-case",
                    "material_id": "red-review",
                    "dimension": "plot",
                    "change_type": "removed",
                    "timecode_or_section": "1:00",
                    "claim_summary": "Claim",
                    "observation": "Observation",
                    "structural_consequence": "Consequence",
                    "expert_interpretation": "Interpretation",
                }
            )

    def test_corpus_stats_keep_blind_split_explicit(self):
        stats = self.store.corpus_stats()
        self.assertEqual(stats["cases_by_split"], {"blind": 1})
        self.assertEqual(stats["claims_by_expert"]["red-cynic"], 0)
        self.assertEqual(stats["claims_by_expert"]["badcomedian"], 0)


if __name__ == "__main__":
    unittest.main()
