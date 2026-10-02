from __future__ import annotations

import unittest

from src.train_model import evaluate_candidate_quality


class ModelQualityGateTests(unittest.TestCase):
    def test_same_holdout_rejects_mae_regression_above_tolerance(self):
        candidate = {
            "test_mae": 1.09,
            "test_year_from": 2024,
            "test_year_to": 2025,
        }
        active = {
            "test_mae": 1.02,
            "test_year_from": 2024,
            "test_year_to": 2025,
        }

        gate = evaluate_candidate_quality(
            candidate,
            active,
            max_mae_regression=0.03,
        )

        self.assertTrue(gate["comparable"])
        self.assertFalse(gate["passed"])
        self.assertEqual(gate["reason"], "mae_regression")
        self.assertAlmostEqual(gate["mae_regression"], 0.07, places=6)

    def test_same_holdout_accepts_small_regression_within_tolerance(self):
        candidate = {
            "test_mae": 1.04,
            "test_year_from": 2024,
            "test_year_to": 2025,
        }
        active = {
            "test_mae": 1.02,
            "test_year_from": 2024,
            "test_year_to": 2025,
        }

        gate = evaluate_candidate_quality(
            candidate,
            active,
            max_mae_regression=0.03,
        )

        self.assertTrue(gate["passed"])
        self.assertEqual(gate["reason"], "within_mae_gate")

    def test_better_candidate_passes(self):
        gate = evaluate_candidate_quality(
            {
                "test_mae": 0.98,
                "test_year_from": 2024,
                "test_year_to": 2025,
            },
            {
                "test_mae": 1.02,
                "test_year_from": 2024,
                "test_year_to": 2025,
            },
            max_mae_regression=0.0,
        )

        self.assertTrue(gate["passed"])
        self.assertLess(gate["mae_regression"], 0)

    def test_different_holdout_is_not_compared_directly(self):
        gate = evaluate_candidate_quality(
            {
                "test_mae": 1.20,
                "test_year_from": 2025,
                "test_year_to": 2026,
            },
            {
                "test_mae": 1.02,
                "test_year_from": 2024,
                "test_year_to": 2025,
            },
            max_mae_regression=0.03,
        )

        self.assertFalse(gate["comparable"])
        self.assertTrue(gate["passed"])
        self.assertEqual(gate["reason"], "different_temporal_holdout")

    def test_first_model_passes_without_baseline(self):
        gate = evaluate_candidate_quality(
            {
                "test_mae": 1.10,
                "test_year_from": 2024,
                "test_year_to": 2025,
            },
            None,
            max_mae_regression=0.03,
        )

        self.assertTrue(gate["passed"])
        self.assertFalse(gate["comparable"])
        self.assertEqual(gate["reason"], "no_active_model")

    def test_candidate_without_mae_is_rejected(self):
        gate = evaluate_candidate_quality(
            {
                "test_year_from": 2024,
                "test_year_to": 2025,
            },
            None,
            max_mae_regression=0.03,
        )

        self.assertFalse(gate["passed"])
        self.assertEqual(gate["reason"], "candidate_mae_missing")


if __name__ == "__main__":
    unittest.main()
