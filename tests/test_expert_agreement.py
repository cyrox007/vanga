from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from src.expert_agreement import ExpertAgreementAnalyzer
from src.expert_corpus import ExpertCorpusStore


class ExpertAgreementTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store = ExpertCorpusStore(Path(self.temp_dir.name) / "expert.duckdb")
        for expert_id, name in (
            ("expert-a", "Эксперт A"),
            ("expert-b", "Эксперт B"),
            ("expert-c", "Эксперт C"),
        ):
            self.store.upsert_profile(
                {
                    "expert_id": expert_id,
                    "display_name": name,
                    "focus": ["structure"],
                }
            )
        for case_id in ("case-1", "case-2"):
            self.store.upsert_case(
                {
                    "case_id": case_id,
                    "film_title": case_id,
                    "film_year": 2020,
                    "split": "development",
                }
            )
        for expert_id in ("expert-a", "expert-b", "expert-c"):
            material_id = f"material-{expert_id}"
            self.store.upsert_material(
                {
                    "material_id": material_id,
                    "expert_id": expert_id,
                    "title": material_id,
                    "source_url": f"https://example.com/{material_id}",
                    "media_type": "video",
                }
            )
            for case_id in ("case-1", "case-2"):
                self.store.link_case_material(
                    {"case_id": case_id, "material_id": material_id}
                )

    def tearDown(self) -> None:
        self.store.close()
        self.temp_dir.cleanup()

    def _claim(
        self,
        *,
        expert_id: str,
        case_id: str,
        claim_id: str,
        dimension: str,
        change_type: str,
        supporting: bool = True,
    ) -> None:
        material_id = f"material-{expert_id}"
        self.store.upsert_claim(
            {
                "claim_id": claim_id,
                "case_id": case_id,
                "material_id": material_id,
                "dimension": dimension,
                "change_type": change_type,
                "timecode_or_section": "00:10:00",
                "claim_summary": "Краткое утверждение",
                "observation": "Наблюдение",
                "structural_consequence": "Структурное следствие",
                "expert_interpretation": "Интерпретация, не должна попадать в report",
                "confidence": 0.9,
            }
        )
        if supporting:
            self.store.upsert_evidence(
                {
                    "evidence_id": f"evidence-{claim_id}",
                    "claim_id": claim_id,
                    "polarity": "supporting",
                    "evidence_kind": "material_reference",
                    "description": "Опорный фрагмент",
                    "confidence": 0.9,
                }
            )

    def test_pairwise_agreement_and_disagreement_only_on_shared_scope(self):
        self._claim(
            expert_id="expert-a",
            case_id="case-1",
            claim_id="a-plot",
            dimension="plot",
            change_type="removed",
        )
        self._claim(
            expert_id="expert-b",
            case_id="case-1",
            claim_id="b-plot",
            dimension="plot",
            change_type="removed",
        )
        self._claim(
            expert_id="expert-a",
            case_id="case-2",
            claim_id="a-char",
            dimension="characters",
            change_type="merged",
        )
        self._claim(
            expert_id="expert-b",
            case_id="case-2",
            claim_id="b-char",
            dimension="characters",
            change_type="removed",
        )
        # Эксперт B не размечал worldbuilding: это не disagreement.
        self._claim(
            expert_id="expert-a",
            case_id="case-2",
            claim_id="a-world",
            dimension="worldbuilding",
            change_type="removed",
        )

        report = ExpertAgreementAnalyzer(self.store).analyze(split="development")

        pair = next(
            item
            for item in report["pairwise"]
            if item["left_expert_id"] == "expert-a"
            and item["right_expert_id"] == "expert-b"
        )
        self.assertEqual(pair["shared_scope_count"], 2)
        self.assertEqual(pair["exact_agreement_count"], 1)
        self.assertEqual(pair["disagreement_scope_count"], 1)
        self.assertEqual(pair["exact_agreement_rate"], 0.5)
        self.assertEqual(report["annotated_scope_count"], 3)
        self.assertEqual(report["compared_scope_count"], 2)
        self.assertFalse(report["policy"]["silence_is_negative"])
        self.assertFalse(report["contains_expert_interpretation"])
        self.assertNotIn("Интерпретация", str(report))

    def test_leave_one_out_uses_peer_majority(self):
        for expert_id in ("expert-a", "expert-b", "expert-c"):
            self._claim(
                expert_id=expert_id,
                case_id="case-1",
                claim_id=f"{expert_id}-plot",
                dimension="plot",
                change_type="removed",
            )
        self._claim(
            expert_id="expert-a",
            case_id="case-2",
            claim_id="a-char",
            dimension="characters",
            change_type="merged",
        )
        self._claim(
            expert_id="expert-b",
            case_id="case-2",
            claim_id="b-char",
            dimension="characters",
            change_type="merged",
        )
        self._claim(
            expert_id="expert-c",
            case_id="case-2",
            claim_id="c-char",
            dimension="characters",
            change_type="removed",
        )

        report = ExpertAgreementAnalyzer(self.store).analyze(split="development")
        by_expert = {
            item["expert_id"]: item for item in report["leave_one_expert_out"]
        }

        self.assertEqual(by_expert["expert-a"]["shared_scope_count"], 2)
        self.assertEqual(
            by_expert["expert-a"]["exact_match_with_peer_majority_count"], 1
        )
        self.assertEqual(
            by_expert["expert-c"]["exact_match_with_peer_majority_count"], 1
        )
        self.assertEqual(
            by_expert["expert-c"]["exact_match_with_peer_majority_rate"], 0.5
        )

    def test_supporting_evidence_policy_filters_claims(self):
        self._claim(
            expert_id="expert-a",
            case_id="case-1",
            claim_id="a-plot",
            dimension="plot",
            change_type="removed",
            supporting=True,
        )
        self._claim(
            expert_id="expert-b",
            case_id="case-1",
            claim_id="b-plot",
            dimension="plot",
            change_type="removed",
            supporting=False,
        )

        strict = ExpertAgreementAnalyzer(self.store).analyze(
            split="development", require_supporting_evidence=True
        )
        relaxed = ExpertAgreementAnalyzer(self.store).analyze(
            split="development", require_supporting_evidence=False
        )

        self.assertEqual(strict["compared_scope_count"], 0)
        self.assertEqual(relaxed["compared_scope_count"], 1)


if __name__ == "__main__":
    unittest.main()
