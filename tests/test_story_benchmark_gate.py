from __future__ import annotations

import unittest

from src.adaptation_analysis import AdaptationValidationError
from src.story_benchmark_gate import StoryBenchmarkQualityGate, StoryBenchmarkQualityPolicy


class StoryBenchmarkGateTests(unittest.TestCase):
    def _report(self):
        return {
            "suite_id": "suite-demo",
            "suite_fingerprint_sha256": "a" * 64,
            "by_split": {
                "development": {
                    "case_count": 4,
                    "micro": {"precision": 0.8, "recall": 0.75, "f1": 0.7742},
                    "macro": {"precision": 0.79, "recall": 0.72, "f1": 0.75},
                    "by_kind": {
                        "event": {
                            "case_count": 4,
                            "micro": {"precision": 0.8, "recall": 0.8, "f1": 0.8},
                            "macro": {"f1": 0.78},
                        }
                    },
                },
                "blind": {
                    "case_count": 3,
                    "micro": {"precision": 0.72, "recall": 0.70, "f1": 0.7099},
                    "macro": {"precision": 0.7, "recall": 0.68, "f1": 0.69},
                    "by_kind": {
                        "event": {
                            "case_count": 3,
                            "micro": {"precision": 0.75, "recall": 0.72, "f1": 0.7347},
                            "macro": {"f1": 0.71},
                        }
                    },
                },
            },
        }

    def test_passes_preregistered_blind_policy(self):
        policy = {
            "policy_id": "p4-blind-v1",
            "version": 1,
            "split": "blind",
            "min_case_count": 3,
            "min_micro_precision": 0.7,
            "min_micro_recall": 0.68,
            "min_micro_f1": 0.70,
            "min_macro_f1": 0.68,
            "kind_requirements": {
                "event": {"min_case_count": 3, "min_micro_f1": 0.72}
            },
            "expected_suite_fingerprint_sha256": "a" * 64,
        }
        verdict = StoryBenchmarkQualityGate.evaluate(self._report(), policy)
        self.assertTrue(verdict["passed"])
        self.assertEqual(verdict["split"], "blind")
        self.assertEqual(verdict["failures"], [])

    def test_train_split_is_rejected_for_acceptance(self):
        with self.assertRaises(AdaptationValidationError):
            StoryBenchmarkQualityPolicy.from_dict(
                {
                    "policy_id": "bad",
                    "version": 1,
                    "split": "train",
                    "min_case_count": 1,
                }
            )

    def test_missing_blind_split_fails_instead_of_falling_back(self):
        report = self._report()
        del report["by_split"]["blind"]
        verdict = StoryBenchmarkQualityGate.evaluate(
            report,
            {
                "policy_id": "blind",
                "version": 1,
                "split": "blind",
                "min_case_count": 1,
            },
        )
        self.assertFalse(verdict["passed"])
        self.assertEqual(verdict["failures"][0]["code"], "required_split_missing")

    def test_threshold_regression_fails(self):
        verdict = StoryBenchmarkQualityGate.evaluate(
            self._report(),
            {
                "policy_id": "strict",
                "version": 1,
                "split": "blind",
                "min_case_count": 3,
                "min_micro_precision": 0.75,
                "min_micro_recall": 0.75,
                "min_micro_f1": 0.75,
                "min_macro_f1": 0.75,
            },
        )
        self.assertFalse(verdict["passed"])
        codes = {row["code"] for row in verdict["failures"]}
        self.assertIn("micro_f1", codes)
        self.assertIn("macro_f1", codes)

    def test_missing_required_kind_fails(self):
        verdict = StoryBenchmarkQualityGate.evaluate(
            self._report(),
            {
                "policy_id": "kind",
                "version": 1,
                "split": "blind",
                "min_case_count": 1,
                "kind_requirements": {
                    "worldbuilding": {"min_case_count": 1, "min_micro_f1": 0.5}
                },
            },
        )
        self.assertFalse(verdict["passed"])
        self.assertTrue(any(row["code"] == "kind_missing" for row in verdict["failures"]))

    def test_suite_fingerprint_mismatch_fails(self):
        verdict = StoryBenchmarkQualityGate.evaluate(
            self._report(),
            {
                "policy_id": "fingerprint",
                "version": 1,
                "split": "development",
                "min_case_count": 1,
                "expected_suite_fingerprint_sha256": "b" * 64,
            },
        )
        self.assertFalse(verdict["passed"])
        self.assertTrue(
            any(row["code"] == "suite_fingerprint_mismatch" for row in verdict["failures"])
        )

    def test_policy_fingerprint_is_reproducible(self):
        payload = {
            "policy_id": "repro",
            "version": 1,
            "split": "development",
            "min_case_count": 2,
            "min_micro_f1": 0.6,
        }
        first = StoryBenchmarkQualityPolicy.from_dict(payload)
        second = StoryBenchmarkQualityPolicy.from_dict(dict(payload))
        self.assertEqual(first.fingerprint(), second.fingerprint())


if __name__ == "__main__":
    unittest.main()
