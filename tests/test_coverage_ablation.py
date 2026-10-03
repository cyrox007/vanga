from __future__ import annotations

import os
import unittest
from unittest.mock import MagicMock, patch

import traning
from scripts.coverage_ablation import compare_results
from src.data_filtr import _coverage_features_enabled


class CoverageAblationTests(unittest.TestCase):
    def test_environment_switch_disables_only_candidate_coverage_mode(self):
        with patch.dict(os.environ, {"VANGA_TRAIN_COVERAGE_FEATURES": "1"}, clear=False):
            self.assertTrue(_coverage_features_enabled())
        with patch.dict(os.environ, {"VANGA_TRAIN_COVERAGE_FEATURES": "0"}, clear=False):
            self.assertFalse(_coverage_features_enabled())
        with patch.dict(os.environ, {"VANGA_TRAIN_COVERAGE_FEATURES": "false"}, clear=False):
            self.assertFalse(_coverage_features_enabled())

    def test_compare_results_requires_same_temporal_dataset(self):
        baseline = {
            "test_mae": 1.0,
            "test_rmse": 1.3,
            "test_r2": 0.2,
            "train_year_from": 2000,
            "train_year_to": 2023,
            "test_year_from": 2024,
            "test_year_to": 2025,
            "train_rows": 1000,
            "test_rows": 100,
            "total_rows": 1100,
        }
        candidate = dict(baseline)
        candidate.update(test_mae=0.98, test_rmse=1.28, test_r2=0.22)

        result = compare_results(
            baseline,
            candidate,
            max_mae_regression=0.03,
        )

        self.assertAlmostEqual(result["delta_mae"], -0.02)
        self.assertTrue(result["candidate_improves_mae"])
        self.assertTrue(result["non_regression_passed"])

        mismatched = dict(candidate)
        mismatched["test_rows"] = 101
        with self.assertRaises(RuntimeError):
            compare_results(
                baseline,
                mismatched,
                max_mae_regression=0.03,
            )

    def test_baseline_mode_cannot_publish_through_training_entrypoint(self):
        metadata = {
            "schema_version": 5,
            "test_mae": 1.0,
            "test_rmse": 1.3,
            "test_r2": 0.2,
            "feature_names": ["startYear"],
            "test_year_from": 2024,
            "test_year_to": 2025,
            "test_rows": 100,
        }
        fake_model = MagicMock()

        with patch.object(traning, "get_all_genres", return_value=["Drama"]), patch.object(
            traning,
            "train_catboost_model",
            return_value=(fake_model, metadata),
        ), patch.object(traning, "interpret_model"), patch.object(
            traning,
            "save_trained_model",
        ) as save_mock:
            with self.assertRaises(SystemExit) as raised:
                traning.main(
                    [
                        "--without-coverage-features",
                        "--iterations",
                        "1",
                    ]
                )

        self.assertIn("нельзя публиковать", str(raised.exception))
        save_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
