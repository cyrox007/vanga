from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from src.expert_blind_validation import ExpertBlindValidator
from src.expert_corpus import ExpertCorpusStore, ExpertCorpusValidationError


class ExpertBlindValidationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = ExpertCorpusStore(Path(self.tmp.name) / "expert.duckdb")
        self.store.upsert_profile(
            {
                "expert_id": "red-cynic",
                "display_name": "Красный Циник",
                "focus": ["adaptation", "source_context"],
            }
        )
        self.store.upsert_profile(
            {
                "expert_id": "badcomedian",
                "display_name": "BadComedian",
                "focus": ["script_logic", "continuity"],
            }
        )
        for case_id, split in (
            ("blind-1", "blind"),
            ("blind-2", "blind"),
            ("dev-1", "development"),
        ):
            self.store.upsert_case(
                {
                    "case_id": case_id,
                    "imdb_id": "tt1234567" if case_id == "blind-1" else None,
                    "film_title": f"Film {case_id}",
                    "film_year": 2020,
                    "source_work_id": f"source:{case_id}",
                    "split": split,
                }
            )

        self._add_claim(
            expert_id="red-cynic",
            case_id="blind-1",
            claim_id="red-worldbuilding",
            dimension="worldbuilding",
            change_type="removed",
            interpretation="SECRET_RED_INTERPRETATION",
        )
        self._add_claim(
            expert_id="badcomedian",
            case_id="blind-1",
            claim_id="bad-logic",
            dimension="script_logic",
            change_type="contradiction",
            interpretation="SECRET_BAD_INTERPRETATION",
        )
        self._add_claim(
            expert_id="badcomedian",
            case_id="blind-2",
            claim_id="bad-motivation",
            dimension="motivation",
            change_type="rewritten",
            interpretation="ANOTHER_SECRET_INTERPRETATION",
        )
        self.validator = ExpertBlindValidator(self.store)

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def _add_claim(
        self,
        *,
        expert_id: str,
        case_id: str,
        claim_id: str,
        dimension: str,
        change_type: str,
        interpretation: str,
        add_supporting_evidence: bool = True,
    ) -> None:
        material_id = f"material:{claim_id}"
        self.store.upsert_material(
            {
                "material_id": material_id,
                "expert_id": expert_id,
                "title": f"Review {claim_id}",
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
                "timecode_or_section": "00:10:00",
                "claim_summary": f"Claim {claim_id}",
                "observation": f"Observation {claim_id}",
                "structural_consequence": f"Consequence {claim_id}",
                "expert_interpretation": interpretation,
                "confidence": 0.9,
            }
        )
        if add_supporting_evidence:
            self.store.upsert_evidence(
                {
                    "evidence_id": f"evidence:{claim_id}",
                    "claim_id": claim_id,
                    "polarity": "supporting",
                    "evidence_kind": "storydiff",
                    "description": "Автоматически проверяемая структурная ссылка",
                    "reference_id": f"storydiff:{claim_id}",
                    "confidence": 0.9,
                }
            )

    def _prediction_payload(self, manifest: dict) -> dict:
        return {
            "version": 1,
            "run_id": "run-001",
            "manifest_fingerprint_sha256": manifest["manifest_fingerprint_sha256"],
            "cases": [
                {
                    "case_id": "blind-1",
                    "findings": [
                        {
                            "dimension": "worldbuilding",
                            "change_type": "removed",
                            "reference_id": "storydiff:auto-worldbuilding",
                            "confidence": 0.95,
                        },
                        {
                            "dimension": "script_logic",
                            "change_type": "contradiction",
                            "reference_id": "storydiff:auto-logic",
                            "confidence": 0.85,
                        },
                    ],
                },
                {
                    "case_id": "blind-2",
                    "findings": [
                        {
                            "dimension": "motivation",
                            "change_type": "rewritten",
                            "reference_id": "story-transform:auto-motivation",
                            "confidence": 0.8,
                        }
                    ],
                },
            ],
        }

    def test_manifest_contains_only_case_metadata(self):
        manifest = self.validator.export_manifest(split="blind")
        text = json.dumps(manifest, ensure_ascii=False)

        self.assertEqual(manifest["case_count"], 2)
        self.assertFalse(manifest["contains_expert_claims"])
        self.assertFalse(manifest["contains_expert_interpretation"])
        self.assertNotIn("SECRET_RED_INTERPRETATION", text)
        self.assertNotIn("red-worldbuilding", text)
        self.assertEqual(len(manifest["manifest_fingerprint_sha256"]), 64)
        self.assertEqual(
            manifest["manifest_fingerprint_sha256"],
            self.validator.export_manifest(split="blind")["manifest_fingerprint_sha256"],
        )

    def test_perfect_structural_classes_score_separately_per_expert(self):
        manifest = self.validator.export_manifest(split="blind")
        result = self.validator.evaluate(self._prediction_payload(manifest), split="blind")

        self.assertEqual(result["overall"]["f1"], 1.0)
        self.assertEqual(result["per_expert"]["red-cynic"]["metrics"]["f1"], 1.0)
        self.assertEqual(result["per_expert"]["badcomedian"]["metrics"]["f1"], 1.0)
        self.assertFalse(result["scoring_policy"]["silence_is_negative"])
        self.assertTrue(
            result["scoring_policy"]["score_only_dimensions_annotated_by_expert_for_case"]
        )
        self.assertTrue(result["expert_profiles_kept_separate"])
        self.assertFalse(result["expert_interpretation_exposed_to_predictor"])
        self.assertIsNone(result["preference_score"])

        serialized = json.dumps(result, ensure_ascii=False)
        self.assertNotIn("SECRET_RED_INTERPRETATION", serialized)
        self.assertNotIn("SECRET_BAD_INTERPRETATION", serialized)
        self.assertNotIn("ANOTHER_SECRET_INTERPRETATION", serialized)

    def test_false_positive_inside_annotated_dimension_penalizes_only_that_expert(self):
        manifest = self.validator.export_manifest(split="blind")
        payload = self._prediction_payload(manifest)
        payload["cases"][1]["findings"].append(
            {
                "dimension": "motivation",
                "change_type": "added",
                "reference_id": "storydiff:false-positive",
                "confidence": 0.9,
            }
        )
        result = self.validator.evaluate(payload, split="blind")

        self.assertLess(result["per_expert"]["badcomedian"]["metrics"]["precision"], 1.0)
        self.assertEqual(result["per_expert"]["red-cynic"]["metrics"]["f1"], 1.0)

    def test_prediction_in_dimension_not_annotated_by_expert_is_unscored_not_false_positive(self):
        manifest = self.validator.export_manifest(split="blind")
        baseline = self.validator.evaluate(self._prediction_payload(manifest), split="blind")
        payload = self._prediction_payload(manifest)
        payload["cases"][1]["findings"].append(
            {
                "dimension": "worldbuilding",
                "change_type": "added",
                "reference_id": "storydiff:unscored-worldbuilding",
                "confidence": 0.9,
            }
        )
        result = self.validator.evaluate(payload, split="blind")

        bad = result["per_expert"]["badcomedian"]
        baseline_bad = baseline["per_expert"]["badcomedian"]
        self.assertEqual(bad["metrics"]["f1"], 1.0)
        self.assertEqual(bad["metrics"]["fp"], 0)
        self.assertEqual(
            bad["unscored_prediction_count"],
            baseline_bad["unscored_prediction_count"] + 1,
        )
        self.assertEqual(
            result["unscored_prediction_count"],
            baseline["unscored_prediction_count"] + 1,
        )

    def test_manifest_fingerprint_mismatch_is_rejected(self):
        manifest = self.validator.export_manifest(split="blind")
        payload = self._prediction_payload(manifest)
        payload["manifest_fingerprint_sha256"] = "0" * 64

        with self.assertRaises(ExpertCorpusValidationError):
            self.validator.evaluate(payload, split="blind")

    def test_missing_manifest_case_is_rejected_even_when_no_findings(self):
        manifest = self.validator.export_manifest(split="blind")
        payload = self._prediction_payload(manifest)
        payload["cases"] = payload["cases"][:1]

        with self.assertRaises(ExpertCorpusValidationError):
            self.validator.evaluate(payload, split="blind")

    def test_duplicate_class_and_missing_reference_are_rejected(self):
        manifest = self.validator.export_manifest(split="blind")
        payload = self._prediction_payload(manifest)
        payload["cases"][0]["findings"].append(
            {
                "dimension": "worldbuilding",
                "change_type": "removed",
                "reference_id": "duplicate",
                "confidence": 0.7,
            }
        )
        with self.assertRaises(ExpertCorpusValidationError):
            self.validator.evaluate(payload, split="blind")

        payload = self._prediction_payload(manifest)
        payload["cases"][0]["findings"][0]["reference_id"] = ""
        with self.assertRaises(ExpertCorpusValidationError):
            self.validator.evaluate(payload, split="blind")

    def test_train_split_is_never_a_blind_acceptance_split(self):
        with self.assertRaises(ExpertCorpusValidationError):
            self.validator.export_manifest(split="train")

    def test_supporting_evidence_policy_filters_unsubstantiated_gold(self):
        self._add_claim(
            expert_id="red-cynic",
            case_id="blind-2",
            claim_id="red-without-evidence",
            dimension="themes",
            change_type="removed",
            interpretation="SECRET_UNSUPPORTED",
            add_supporting_evidence=False,
        )
        manifest = self.validator.export_manifest(split="blind")
        payload = self._prediction_payload(manifest)

        strict = self.validator.evaluate(
            payload,
            split="blind",
            require_supporting_evidence=True,
        )
        loose = self.validator.evaluate(
            payload,
            split="blind",
            require_supporting_evidence=False,
        )

        self.assertEqual(strict["per_expert"]["red-cynic"]["metrics"]["f1"], 1.0)
        self.assertLess(loose["per_expert"]["red-cynic"]["metrics"]["recall"], 1.0)


if __name__ == "__main__":
    unittest.main()
