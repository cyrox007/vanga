from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from src.expert_corpus import ExpertCorpusStore
from src.expert_corpus_gate import evaluate_expert_corpus


class ExpertCorpusGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "expert.duckdb"
        self.store = ExpertCorpusStore(self.path)

    def tearDown(self) -> None:
        self.store.close()
        self.tmp.cleanup()

    def test_empty_corpus_is_blocked(self) -> None:
        result = evaluate_expert_corpus(
            self.path,
            min_train_cases=1,
            min_blind_cases=1,
            min_external_cases=1,
            min_claims=1,
        )
        self.assertFalse(result.passed)
        self.assertTrue(result.blockers)

    def test_structured_corpus_can_pass_gate(self) -> None:
        for expert in ("red-cynic", "badcomedian"):
            self.store.upsert_profile(
                {
                    "expert_id": expert,
                    "display_name": expert,
                    "focus": ["script_logic"],
                }
            )
            self.store.upsert_material(
                {
                    "material_id": f"material-{expert}",
                    "expert_id": expert,
                    "title": f"Review {expert}",
                    "source_url": f"https://example.org/{expert}",
                    "media_type": "video",
                    "retrieved_at": "2026-01-01T00:00:00Z",
                }
            )

        splits = ["train", "blind", "external_transfer"]
        for idx, split in enumerate(splits):
            case_id = f"case-{idx}"
            material_id = "material-red-cynic" if idx != 2 else "material-badcomedian"
            self.store.upsert_case(
                {
                    "case_id": case_id,
                    "film_title": f"Film {idx}",
                    "film_year": 2020 + idx,
                    "split": split,
                }
            )
            self.store.link_case_material({"case_id": case_id, "material_id": material_id})
            claim_id = f"claim-{idx}"
            self.store.upsert_claim(
                {
                    "claim_id": claim_id,
                    "case_id": case_id,
                    "material_id": material_id,
                    "dimension": "script_logic",
                    "change_type": "contradiction",
                    "timecode_or_section": "00:10",
                    "claim_summary": "Есть структурная проблема",
                    "observation": "Сцены противоречат друг другу",
                    "structural_consequence": "Нарушается причинная связность",
                    "expert_interpretation": "Сценарная логика ослаблена",
                    "confidence": 0.9,
                    "tags": ["logic"],
                }
            )
            self.store.upsert_evidence(
                {
                    "evidence_id": f"evidence-{idx}",
                    "claim_id": claim_id,
                    "polarity": "supporting",
                    "evidence_kind": "storydiff",
                    "description": "Структурированное подтверждение",
                    "reference_id": f"storydiff-{idx}",
                    "confidence": 0.9,
                }
            )
        result = evaluate_expert_corpus(
            self.path,
            min_train_cases=1,
            min_blind_cases=1,
            min_external_cases=1,
            min_claims=3,
        )
        self.assertTrue(result.passed, result.blockers)
        self.assertEqual(result.metrics["experts"], 2)
        self.assertEqual(result.metrics["claims_with_supporting_evidence"], 3)


if __name__ == "__main__":
    unittest.main()
