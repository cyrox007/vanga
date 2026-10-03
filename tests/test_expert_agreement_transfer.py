from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from src.expert_agreement_transfer import (
    ExpertAgreementAnalyzer,
    HeldOutExpertTransferEvaluator,
)
from src.expert_corpus import ExpertCorpusStore, ExpertCorpusValidationError


class ExpertAgreementTransferTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = ExpertCorpusStore(Path(self.tmp.name) / "expert.duckdb")
        for expert_id, display_name in (
            ("red-cynic", "Красный Циник"),
            ("badcomedian", "BadComedian"),
            ("third-expert", "Третий эксперт"),
        ):
            self.store.upsert_profile(
                {
                    "expert_id": expert_id,
                    "display_name": display_name,
                    "focus": ["structural_analysis"],
                }
            )

        for case_id, split in (
            ("blind-exact", "blind"),
            ("blind-disagree", "blind"),
            ("blind-partial", "blind"),
            ("blind-single", "blind"),
            ("transfer-1", "external_transfer"),
            ("transfer-2", "external_transfer"),
        ):
            self.store.upsert_case(
                {
                    "case_id": case_id,
                    "film_title": case_id,
                    "film_year": 2021,
                    "source_work_id": f"source:{case_id}",
                    "split": split,
                }
            )

        # Exact agreement: оба эксперта явно размечали одну dimension одинаково.
        self._add_claim("red-cynic", "blind-exact", "red-exact", "worldbuilding", "removed")
        self._add_claim("badcomedian", "blind-exact", "bad-exact", "worldbuilding", "removed")

        # Explicit disagreement: одна dimension, непересекающиеся change types.
        self._add_claim("red-cynic", "blind-disagree", "red-disagree", "motivation", "rewritten")
        self._add_claim("badcomedian", "blind-disagree", "bad-disagree", "motivation", "added")

        # Partial overlap: оба видят merge, но дополнительно фиксируют разные изменения.
        self._add_claim("red-cynic", "blind-partial", "red-partial-merge", "characters", "merged")
        self._add_claim("red-cynic", "blind-partial", "red-partial-remove", "characters", "removed")
        self._add_claim("badcomedian", "blind-partial", "bad-partial-merge", "characters", "merged")
        self._add_claim("badcomedian", "blind-partial", "bad-partial-add", "characters", "added")

        # Молчание остальных экспертов по themes не является disagreement.
        self._add_claim("third-expert", "blind-single", "third-single", "themes", "removed")

        # Held-out transfer gold только у red-cynic.
        self._add_claim("red-cynic", "transfer-1", "red-transfer-1", "worldbuilding", "removed")
        self._add_claim("red-cynic", "transfer-2", "red-transfer-2", "motivation", "rewritten")
        # Другой профиль может иметь собственную разметку, но она не является gold held-out run.
        self._add_claim("badcomedian", "transfer-1", "bad-transfer-1", "script_logic", "contradiction")

        self.agreement = ExpertAgreementAnalyzer(self.store)
        self.transfer = HeldOutExpertTransferEvaluator(self.store)

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def _add_claim(
        self,
        expert_id: str,
        case_id: str,
        claim_id: str,
        dimension: str,
        change_type: str,
        *,
        confidence: float = 0.9,
        supporting: bool = True,
    ) -> None:
        material_id = f"material:{claim_id}"
        self.store.upsert_material(
            {
                "material_id": material_id,
                "expert_id": expert_id,
                "title": f"Material {claim_id}",
                "source_url": f"https://example.org/{claim_id}",
                "media_type": "video",
            }
        )
        self.store.link_case_material(
            {"case_id": case_id, "material_id": material_id}
        )
        self.store.upsert_claim(
            {
                "claim_id": claim_id,
                "case_id": case_id,
                "material_id": material_id,
                "dimension": dimension,
                "change_type": change_type,
                "timecode_or_section": "00:01:00",
                "claim_summary": f"Claim {claim_id}",
                "observation": f"Observation {claim_id}",
                "structural_consequence": f"Consequence {claim_id}",
                "expert_interpretation": f"SECRET {claim_id}",
                "confidence": confidence,
            }
        )
        if supporting:
            self.store.upsert_evidence(
                {
                    "evidence_id": f"evidence:{claim_id}",
                    "claim_id": claim_id,
                    "polarity": "supporting",
                    "evidence_kind": "storydiff",
                    "description": "Структурная опора",
                    "reference_id": f"storydiff:{claim_id}",
                    "confidence": 0.9,
                }
            )

    def test_agreement_requires_shared_case_dimension(self):
        result = self.agreement.analyze(split="blind")

        states = {
            (item["case_id"], item["dimension"]): item["state"]
            for item in result["comparisons"]
            if {item["left_expert_id"], item["right_expert_id"]}
            == {"red-cynic", "badcomedian"}
        }
        self.assertEqual(states[("blind-exact", "worldbuilding")], "exact_agreement")
        self.assertEqual(states[("blind-disagree", "motivation")], "explicit_disagreement")
        self.assertEqual(states[("blind-partial", "characters")], "partial_overlap")
        self.assertFalse(result["scoring_policy"]["silence_is_disagreement"])
        self.assertTrue(result["scoring_policy"]["compare_only_shared_case_dimensions"])

    def test_single_expert_dimension_is_not_comparable_not_disagreement(self):
        result = self.agreement.analyze(split="blind")
        row = next(
            item
            for item in result["not_comparable"]
            if item["case_id"] == "blind-single" and item["dimension"] == "themes"
        )

        self.assertEqual(row["expert_id"], "third-expert")
        self.assertEqual(row["state"], "not_comparable")
        self.assertEqual(result["explicit_disagreement_count"], 1)
        self.assertIsNone(result["winner"])
        self.assertIsNone(result["preference_score"])

    def test_pairwise_metrics_keep_profiles_separate(self):
        result = self.agreement.analyze(split="blind")
        pair = result["per_expert_pair"]["badcomedian::red-cynic"]

        self.assertEqual(pair["comparable_dimension_count"], 3)
        self.assertEqual(pair["exact_agreement_count"], 1)
        self.assertEqual(pair["partial_overlap_count"], 1)
        self.assertEqual(pair["explicit_disagreement_count"], 1)
        self.assertGreater(pair["mean_jaccard"], 0.0)
        self.assertLess(pair["mean_jaccard"], 1.0)

    def test_transfer_manifest_contains_cases_but_not_held_out_gold(self):
        manifest = self.transfer.export_manifest(
            held_out_expert_id="red-cynic",
            split="external_transfer",
        )
        serialized = json.dumps(manifest, ensure_ascii=False)

        self.assertEqual(manifest["case_count"], 2)
        self.assertFalse(manifest["contains_held_out_claims"])
        self.assertFalse(manifest["contains_held_out_interpretation"])
        self.assertNotIn("red-transfer-1", serialized)
        self.assertNotIn("worldbuilding", serialized)
        self.assertEqual(len(manifest["manifest_fingerprint_sha256"]), 64)

    def _transfer_run(self, manifest: dict) -> dict:
        return {
            "version": 1,
            "run_id": "transfer-run-001",
            "manifest_fingerprint_sha256": manifest["manifest_fingerprint_sha256"],
            "excluded_expert_ids": ["red-cynic"],
            "cases": [
                {
                    "case_id": "transfer-1",
                    "findings": [
                        {
                            "dimension": "worldbuilding",
                            "change_type": "removed",
                            "reference_id": "storydiff:auto-transfer-1",
                            "confidence": 0.9,
                        },
                        {
                            "dimension": "script_logic",
                            "change_type": "contradiction",
                            "reference_id": "storydiff:unscored-other-focus",
                            "confidence": 0.9,
                        },
                    ],
                },
                {
                    "case_id": "transfer-2",
                    "findings": [
                        {
                            "dimension": "motivation",
                            "change_type": "added",
                            "reference_id": "storydiff:auto-transfer-wrong",
                            "confidence": 0.9,
                        }
                    ],
                },
            ],
        }

    def test_transfer_scores_only_held_out_annotated_dimensions(self):
        manifest = self.transfer.export_manifest(
            held_out_expert_id="red-cynic",
            split="external_transfer",
        )
        result = self.transfer.evaluate(
            self._transfer_run(manifest),
            held_out_expert_id="red-cynic",
            split="external_transfer",
        )

        self.assertEqual(result["metrics"]["tp"], 1)
        self.assertEqual(result["metrics"]["fp"], 1)
        self.assertEqual(result["metrics"]["fn"], 1)
        self.assertEqual(result["metrics"]["f1"], 0.5)
        self.assertEqual(result["unscored_prediction_count"], 1)
        self.assertTrue(result["policy"]["held_out_expert_exclusion_declared"])
        self.assertTrue(result["policy"]["exclusion_is_declarative_not_audit_proof"])
        self.assertFalse(result["policy"]["silence_is_negative"])
        self.assertTrue(result["coverage_warning"])
        self.assertIsNone(result["preference_score"])

    def test_transfer_requires_held_out_exclusion_declaration(self):
        manifest = self.transfer.export_manifest(
            held_out_expert_id="red-cynic",
            split="external_transfer",
        )
        payload = self._transfer_run(manifest)
        payload["excluded_expert_ids"] = ["badcomedian"]

        with self.assertRaises(ExpertCorpusValidationError):
            self.transfer.evaluate(
                payload,
                held_out_expert_id="red-cynic",
                split="external_transfer",
            )

    def test_transfer_rejects_fingerprint_or_missing_case(self):
        manifest = self.transfer.export_manifest(
            held_out_expert_id="red-cynic",
            split="external_transfer",
        )
        payload = self._transfer_run(manifest)
        payload["manifest_fingerprint_sha256"] = "0" * 64
        with self.assertRaises(ExpertCorpusValidationError):
            self.transfer.evaluate(
                payload,
                held_out_expert_id="red-cynic",
                split="external_transfer",
            )

        payload = self._transfer_run(manifest)
        payload["cases"] = payload["cases"][:1]
        with self.assertRaises(ExpertCorpusValidationError):
            self.transfer.evaluate(
                payload,
                held_out_expert_id="red-cynic",
                split="external_transfer",
            )

    def test_train_split_is_rejected_for_agreement_and_transfer(self):
        with self.assertRaises(ExpertCorpusValidationError):
            self.agreement.analyze(split="train")
        with self.assertRaises(ExpertCorpusValidationError):
            self.transfer.export_manifest(
                held_out_expert_id="red-cynic",
                split="train",
            )


if __name__ == "__main__":
    unittest.main()
