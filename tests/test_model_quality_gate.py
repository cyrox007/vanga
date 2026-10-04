from __future__ import annotations

import math
import unittest

from src.train_model import evaluate_candidate_quality


def _meta(
    mae: float,
    *,
    year_from: int = 2024,
    year_to: int = 2025,
    rows: int = 100,
    test_fingerprint: str = "a" * 64,
    imdb_fingerprint: str = "b" * 64,
    override: bool = False,
) -> dict:
    return {
        "test_mae": mae,
        "test_year_from": year_from,
        "test_year_to": year_to,
        "test_rows": rows,
        "test_dataset_fingerprint_sha256": test_fingerprint,
        "imdb_data_freshness": {
            "logical_fingerprint_sha256": imdb_fingerprint,
        },
        "quality_gate_allow_uncomparable": override,
    }


class ModelQualityGateTests(unittest.TestCase):
    def test_same_holdout_rejects_mae_regression_above_tolerance(self):
        gate = evaluate_candidate_quality(
            _meta(1.09),
            _meta(1.02),
            max_mae_regression=0.03,
        )

        self.assertTrue(gate["comparable"])
        self.assertFalse(gate["passed"])
        self.assertEqual(gate["reason"], "mae_regression")
        self.assertAlmostEqual(gate["mae_regression"], 0.07, places=6)

    def test_same_holdout_accepts_small_regression_within_tolerance(self):
        gate = evaluate_candidate_quality(
            _meta(1.04),
            _meta(1.02),
            max_mae_regression=0.03,
        )

        self.assertTrue(gate["comparable"])
        self.assertTrue(gate["passed"])
        self.assertEqual(gate["reason"], "within_mae_gate")

    def test_better_candidate_passes(self):
        gate = evaluate_candidate_quality(
            _meta(0.98),
            _meta(1.02),
            max_mae_regression=0.0,
        )

        self.assertTrue(gate["passed"])
        self.assertLess(gate["mae_regression"], 0)

    def test_different_holdout_is_rejected_without_explicit_override(self):
        gate = evaluate_candidate_quality(
            _meta(1.20, year_from=2025, year_to=2026),
            _meta(1.02),
            max_mae_regression=0.03,
        )

        self.assertFalse(gate["comparable"])
        self.assertFalse(gate["passed"])
        self.assertEqual(gate["reason"], "different_temporal_holdout")

    def test_different_test_rows_are_rejected(self):
        gate = evaluate_candidate_quality(
            _meta(1.01, rows=200),
            _meta(1.02, rows=100),
            max_mae_regression=0.03,
        )
        self.assertFalse(gate["passed"])
        self.assertEqual(gate["reason"], "different_holdout_rows")

    def test_different_holdout_fingerprint_is_rejected(self):
        gate = evaluate_candidate_quality(
            _meta(1.01, test_fingerprint="c" * 64),
            _meta(1.02, test_fingerprint="a" * 64),
            max_mae_regression=0.03,
        )
        self.assertFalse(gate["passed"])
        self.assertEqual(gate["reason"], "different_holdout_dataset")

    def test_different_imdb_snapshot_is_rejected(self):
        gate = evaluate_candidate_quality(
            _meta(1.01, imdb_fingerprint="c" * 64),
            _meta(1.02, imdb_fingerprint="b" * 64),
            max_mae_regression=0.03,
        )
        self.assertFalse(gate["passed"])
        self.assertEqual(gate["reason"], "different_imdb_snapshot")

    def test_explicit_override_allows_only_uncomparable_baseline_transition(self):
        gate = evaluate_candidate_quality(
            _meta(1.01, imdb_fingerprint="c" * 64, override=True),
            _meta(1.02, imdb_fingerprint="b" * 64),
            max_mae_regression=0.03,
        )
        self.assertFalse(gate["comparable"])
        self.assertTrue(gate["passed"])
        self.assertTrue(gate["operator_override"])
        self.assertEqual(gate["reason"], "operator_override:different_imdb_snapshot")

        regression = evaluate_candidate_quality(
            _meta(1.20, override=True),
            _meta(1.02),
            max_mae_regression=0.03,
        )
        self.assertTrue(regression["comparable"])
        self.assertFalse(regression["passed"])
        self.assertEqual(regression["reason"], "mae_regression")

    def test_first_model_passes_without_baseline_when_metric_is_finite(self):
        gate = evaluate_candidate_quality(
            _meta(1.10),
            None,
            max_mae_regression=0.03,
        )

        self.assertTrue(gate["passed"])
        self.assertFalse(gate["comparable"])
        self.assertEqual(gate["reason"], "no_active_model")

    def test_candidate_without_mae_is_rejected(self):
        candidate = _meta(1.10)
        candidate.pop("test_mae")
        gate = evaluate_candidate_quality(
            candidate,
            None,
            max_mae_regression=0.03,
        )

        self.assertFalse(gate["passed"])
        self.assertEqual(gate["reason"], "candidate_mae_missing_or_non_finite")

    def test_non_finite_candidate_is_rejected_even_with_override(self):
        candidate = _meta(math.nan, override=True)
        gate = evaluate_candidate_quality(
            candidate,
            _meta(1.02),
            max_mae_regression=0.03,
        )
        self.assertFalse(gate["passed"])
        self.assertEqual(gate["reason"], "candidate_mae_missing_or_non_finite")

    def test_active_non_finite_requires_explicit_override(self):
        gate = evaluate_candidate_quality(
            _meta(1.01),
            _meta(math.inf),
            max_mae_regression=0.03,
        )
        self.assertFalse(gate["passed"])
        self.assertEqual(gate["reason"], "active_mae_missing_or_non_finite")

        override = evaluate_candidate_quality(
            _meta(1.01, override=True),
            _meta(math.inf),
            max_mae_regression=0.03,
        )
        self.assertTrue(override["passed"])
        self.assertEqual(
            override["reason"],
            "operator_override:active_mae_missing_or_non_finite",
        )


if __name__ == "__main__":
    unittest.main()
