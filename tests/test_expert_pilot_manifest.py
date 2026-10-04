from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from src.expert_corpus import ExpertCorpusStore
from src.expert_corpus_gate import evaluate_expert_corpus


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "data" / "expert" / "pilot_sources.json"


class ExpertPilotManifestTests(unittest.TestCase):
    def test_manifest_imports_and_keeps_sealed_splits_without_fake_claims(self) -> None:
        payload = json.loads(MANIFEST.read_text(encoding="utf-8"))
        self.assertEqual(len(payload["profiles"]), 2)
        self.assertEqual(len(payload["cases"]), 11)
        self.assertEqual(len(payload["materials"]), 11)
        self.assertEqual(payload["claims"], [])
        self.assertEqual(payload["evidence"], [])

        counts = {}
        for row in payload["cases"]:
            counts[row["split"]] = counts.get(row["split"], 0) + 1
        self.assertEqual(
            counts,
            {"train": 5, "development": 2, "blind": 2, "external_transfer": 2},
        )

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "expert.duckdb"
            store = ExpertCorpusStore(path)
            try:
                for row in payload["profiles"]:
                    store.upsert_profile(row)
                for row in payload["cases"]:
                    store.upsert_case(row)
                for row in payload["materials"]:
                    store.upsert_material(row)
                for row in payload["case_materials"]:
                    store.link_case_material(row)
                stats = store.corpus_stats()
                self.assertEqual(stats["profiles"], 2)
                self.assertEqual(stats["cases"], 11)
                self.assertEqual(stats["materials"], 11)
            finally:
                store.close()

            gate = evaluate_expert_corpus(path)
            self.assertFalse(gate.passed)
            self.assertEqual(gate.metrics["claims"], 0)
            self.assertEqual(gate.metrics["annotated_train_cases"], 0)
            self.assertTrue(any("claims" in blocker for blocker in gate.blockers))


if __name__ == "__main__":
    unittest.main()
