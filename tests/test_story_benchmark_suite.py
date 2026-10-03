from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from src.adaptation_analysis import AdaptationValidationError
from src.story_benchmark_suite import StoryBenchmarkSuiteAggregator
from src.story_benchmark_runner import StoryBenchmarkSuiteRunner


class StoryBenchmarkSuiteTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def _write(self, name: str, payload: dict) -> str:
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return name

    @staticmethod
    def _source_map() -> dict:
        return {
            "map_id": "source-map",
            "language": "canonical",
            "nodes": [
                {"key": "s-char", "kind": "character", "label": "Alice"},
                {"key": "s-event", "kind": "event", "label": "Gate opens"},
            ],
            "relations": [],
        }

    @staticmethod
    def _adaptation_map() -> dict:
        return {
            "map_id": "adaptation-map",
            "language": "canonical",
            "nodes": [
                {"key": "a-char", "kind": "character", "label": "Alice"},
                {"key": "a-event", "kind": "event", "label": "Gate opens"},
            ],
            "relations": [],
        }

    def _case_files(self, prefix: str, *, split: str, predicted_source_key: str = "s-event") -> dict:
        source = self._write(f"{prefix}/source.json", self._source_map())
        adaptation = self._write(f"{prefix}/adaptation.json", self._adaptation_map())
        gold = self._write(
            f"{prefix}/gold.json",
            {
                "case_id": prefix,
                "split": split,
                "source_map_id": "source-map",
                "adaptation_map_id": "adaptation-map",
                "matches": [
                    {"adaptation_key": "a-event", "source_keys": ["s-event"]}
                ],
            },
        )
        predicted = self._write(
            f"{prefix}/predicted.json",
            {
                "map_id": f"predicted-{prefix}",
                "language": "canonical",
                "nodes": [
                    {"key": "a-char", "kind": "character", "label": "Alice"},
                    {
                        "key": "a-event",
                        "kind": "event",
                        "label": "Gate opens",
                        "maps_from": [predicted_source_key],
                    },
                ],
                "relations": [],
            },
        )
        return {
            "case_id": prefix,
            "split": split,
            "source": source,
            "adaptation": adaptation,
            "gold": gold,
            "predicted": predicted,
        }

    def test_runner_aggregates_multiple_splits_and_content_fingerprint(self):
        manifest = {
            "suite_id": "suite-demo",
            "version": 1,
            "cases": [
                self._case_files("dev-case", split="development"),
                self._case_files("blind-case", split="blind"),
            ],
        }
        report = StoryBenchmarkSuiteRunner(self.root).run(manifest)

        self.assertEqual(report["case_count"], 2)
        self.assertEqual(report["overall"]["micro"]["tp"], 2)
        self.assertEqual(report["overall"]["micro"]["fp"], 0)
        self.assertEqual(report["overall"]["micro"]["fn"], 0)
        self.assertEqual(report["overall"]["micro"]["f1"], 1.0)
        self.assertEqual(set(report["by_split"]), {"development", "blind"})
        self.assertEqual(len(report["input_hashes"]), 8)
        self.assertEqual(len(report["suite_fingerprint_sha256"]), 64)
        self.assertEqual(len(report["manifest_fingerprint_sha256"]), 64)

    def test_only_blind_split_does_not_include_development_case(self):
        manifest = {
            "suite_id": "suite-demo",
            "version": 1,
            "cases": [
                self._case_files("dev-case", split="development"),
                self._case_files("blind-case", split="blind"),
            ],
        }
        report = StoryBenchmarkSuiteRunner(self.root).run(
            manifest,
            only_split="blind",
        )

        self.assertEqual(report["case_count"], 1)
        self.assertEqual(report["case_ids"], ["blind-case"])
        self.assertEqual(set(report["by_split"]), {"blind"})
        self.assertEqual(report["only_split"], "blind")

    def test_input_content_change_changes_suite_fingerprint(self):
        case = self._case_files("blind-case", split="blind")
        manifest = {"suite_id": "suite-demo", "version": 1, "cases": [case]}
        runner = StoryBenchmarkSuiteRunner(self.root)
        first = runner.run(manifest)

        predicted_path = self.root / case["predicted"]
        payload = json.loads(predicted_path.read_text(encoding="utf-8"))
        payload["nodes"][1]["label"] = "Gate opens now"
        predicted_path.write_text(json.dumps(payload) + "\n", encoding="utf-8")
        second = runner.run(manifest)

        self.assertNotEqual(
            first["suite_fingerprint_sha256"],
            second["suite_fingerprint_sha256"],
        )
        self.assertEqual(
            first["manifest_fingerprint_sha256"],
            second["manifest_fingerprint_sha256"],
        )

    def test_runner_rejects_maps_from_on_unknown_adaptation_node(self):
        case = self._case_files("bad-case", split="development")
        predicted_path = self.root / case["predicted"]
        payload = json.loads(predicted_path.read_text(encoding="utf-8"))
        payload["nodes"].append(
            {
                "key": "invented-event",
                "kind": "event",
                "label": "Invented",
                "maps_from": ["s-event"],
            }
        )
        predicted_path.write_text(json.dumps(payload) + "\n", encoding="utf-8")

        manifest = {"suite_id": "suite-demo", "version": 1, "cases": [case]}
        with self.assertRaises(AdaptationValidationError):
            StoryBenchmarkSuiteRunner(self.root).run(manifest)

    def test_runner_rejects_kind_mismatch_in_predicted_maps_from(self):
        case = self._case_files(
            "bad-kind",
            split="development",
            predicted_source_key="s-char",
        )
        manifest = {"suite_id": "suite-demo", "version": 1, "cases": [case]}
        with self.assertRaises(AdaptationValidationError):
            StoryBenchmarkSuiteRunner(self.root).run(manifest)

    def test_manifest_rejects_path_escape(self):
        case = self._case_files("safe", split="development")
        case["source"] = "../outside.json"
        manifest = {"suite_id": "suite-demo", "version": 1, "cases": [case]}
        with self.assertRaises(AdaptationValidationError):
            StoryBenchmarkSuiteRunner(self.root).run(manifest)

    def test_aggregator_micro_and_macro_are_different_when_case_sizes_differ(self):
        rows = [
            {
                "case_id": "large",
                "split": "development",
                "overall": {"tp": 9, "fp": 1, "fn": 1, "precision": 0.9, "recall": 0.9, "f1": 0.9},
                "by_kind": {},
            },
            {
                "case_id": "small",
                "split": "development",
                "overall": {"tp": 0, "fp": 1, "fn": 1, "precision": 0.0, "recall": 0.0, "f1": 0.0},
                "by_kind": {},
            },
        ]
        report = StoryBenchmarkSuiteAggregator.aggregate(
            rows,
            suite_id="aggregation-demo",
        )
        self.assertEqual(report["overall"]["macro"]["f1"], 0.45)
        self.assertGreater(report["overall"]["micro"]["f1"], 0.7)


if __name__ == "__main__":
    unittest.main()
