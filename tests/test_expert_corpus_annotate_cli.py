from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from src.expert_corpus import ExpertCorpusStore


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "expert_corpus_annotate.py"


class ExpertCorpusAnnotateCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "expert.duckdb"
        store = ExpertCorpusStore(self.db_path)
        try:
            store.upsert_profile(
                {
                    "expert_id": "expert-a",
                    "display_name": "Эксперт А",
                    "focus": ["motivation"],
                }
            )
            store.upsert_case(
                {
                    "case_id": "case-train-1",
                    "film_title": "Тестовый фильм",
                    "film_year": 2024,
                    "split": "train",
                }
            )
            store.upsert_material(
                {
                    "material_id": "material-1",
                    "expert_id": "expert-a",
                    "title": "Тестовый разбор",
                    "source_url": "https://example.com/review",
                    "media_type": "video",
                }
            )
            store.link_case_material(
                {
                    "link_id": "link-1",
                    "case_id": "case-train-1",
                    "material_id": "material-1",
                }
            )
        finally:
            store.close()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def run_cli(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(SCRIPT), "--db", str(self.db_path), *args],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )

    def test_list_filters_train_cases(self) -> None:
        result = self.run_cli("list", "--split", "train")
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["count"], 1)
        self.assertEqual(payload["cases"][0]["case_id"], "case-train-1")
        self.assertEqual(payload["cases"][0]["claims"], 0)

    def test_template_uses_real_case_and_material_ids(self) -> None:
        result = self.run_cli("template", "case-train-1")
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["meta"]["material_id"], "material-1")
        self.assertEqual(payload["claims"][0]["case_id"], "case-train-1")
        self.assertEqual(payload["claims"][0]["material_id"], "material-1")
        self.assertEqual(payload["evidence"][0]["claim_id"], payload["claims"][0]["claim_id"])

    def test_validate_does_not_write_to_database(self) -> None:
        bundle = {
            "claims": [
                {
                    "claim_id": "case-train-1-claim-001",
                    "case_id": "case-train-1",
                    "material_id": "material-1",
                    "dimension": "motivation",
                    "change_type": "rewrite",
                    "timecode_or_section": "01:23",
                    "claim_summary": "Мотивационный переход не подготовлен",
                    "observation": "Перед решением отсутствует новое причинное событие",
                    "structural_consequence": "Переход между состояниями персонажа разорван",
                    "expert_interpretation": "Эксперт считает поступок немотивированным",
                    "confidence": 0.9,
                    "tags": ["motivation"],
                }
            ],
            "evidence": [
                {
                    "evidence_id": "evidence-1",
                    "claim_id": "case-train-1-claim-001",
                    "polarity": "supporting",
                    "evidence_kind": "material_reference",
                    "description": "Эксперт указывает на отсутствие подготовительной сцены",
                    "locator": "01:23",
                    "confidence": 0.9,
                }
            ],
        }
        bundle_path = Path(self.tmp.name) / "annotation.json"
        bundle_path.write_text(json.dumps(bundle, ensure_ascii=False), encoding="utf-8")

        result = self.run_cli("validate", str(bundle_path))
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["validated"], {"claims": 1, "evidence": 1})

        store = ExpertCorpusStore(self.db_path)
        try:
            claims = store.conn.execute("SELECT COUNT(*) FROM expert_claims").fetchone()[0]
            evidence = store.conn.execute("SELECT COUNT(*) FROM expert_evidence").fetchone()[0]
            self.assertEqual(claims, 0)
            self.assertEqual(evidence, 0)
        finally:
            store.close()

    def test_validate_rejects_metadata_mutation(self) -> None:
        bundle_path = Path(self.tmp.name) / "bad.json"
        bundle_path.write_text(
            json.dumps({"cases": [], "claims": [], "evidence": []}),
            encoding="utf-8",
        )
        result = self.run_cli("validate", str(bundle_path))
        self.assertEqual(result.returncode, 2)
        self.assertIn("не должен менять metadata pilot", result.stderr)


if __name__ == "__main__":
    unittest.main()
