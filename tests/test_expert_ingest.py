from __future__ import annotations

import unittest

from src.expert_ingest import SourceSegment, build_annotation_draft, candidate_segments, parse_vtt


class ExpertIngestTests(unittest.TestCase):
    def test_parse_vtt_extracts_locator_and_deduplicates_adjacent_cues(self) -> None:
        payload = """WEBVTT

00:00:01.000 --> 00:00:03.000
Персонаж действует нелогично.

00:00:03.000 --> 00:00:04.000
Персонаж действует нелогично.

00:01:05.000 --> 00:01:07.000
Потому что мотивация не показана.
"""
        rows = parse_vtt(payload)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0].locator, "00:01")
        self.assertEqual(rows[1].locator, "01:05")
        self.assertIn("мотивация", rows[1].text)

    def test_candidate_selection_prefers_structural_criticism(self) -> None:
        rows = [
            SourceSegment(locator="00:01", text="Сегодня мы обсуждаем этот фильм и его актёров."),
            SourceSegment(locator="01:10", text="Проблема в том, что мотивация героя вообще не показана, поэтому решение выглядит нелогично."),
            SourceSegment(locator="02:00", text="Спасибо всем, кто досмотрел этот выпуск до конца."),
        ]
        selected = candidate_segments(rows, max_candidates=5)
        self.assertEqual([row.locator for row in selected], ["01:10"])

    def test_draft_is_not_directly_applyable_without_human_review(self) -> None:
        rows = [
            SourceSegment(locator="03:15", text="Проблема сценария в том, что мотивация героя не показана, поэтому поступок выглядит нелогично."),
        ]
        payload = build_annotation_draft(
            case_id="case-1",
            material_id="material-1",
            source_url="https://www.youtube.com/watch?v=test",
            segments=rows,
            max_candidates=10,
        )
        self.assertTrue(payload["meta"]["requires_human_review"])
        self.assertEqual(len(payload["claims"]), 1)
        self.assertTrue(payload["claims"][0]["claim_summary"].startswith("ЗАПОЛНИТЬ:"))
        self.assertEqual(payload["claims"][0]["dimension"], "motivation")
        self.assertEqual(payload["claims"][0]["change_type"], "unknown")
        self.assertTrue(payload["evidence"][0]["description"].startswith("ЗАПОЛНИТЬ:"))

    def test_candidate_limit_is_respected(self) -> None:
        rows = [
            SourceSegment(locator=f"00:{index:02d}", text=f"Проблема сюжета {index}: потому что причинная связь не показана и это нелогично.")
            for index in range(10)
        ]
        selected = candidate_segments(rows, max_candidates=3)
        self.assertEqual(len(selected), 3)


if __name__ == "__main__":
    unittest.main()
