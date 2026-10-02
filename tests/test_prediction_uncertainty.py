from __future__ import annotations

import unittest

import numpy as np

from src.kinovanga import KinoVanga
from src.train_model import calibrate_absolute_error_quantiles


class PredictionUncertaintyTests(unittest.TestCase):
    def test_calibration_quantiles_use_absolute_temporal_errors(self):
        quantiles = calibrate_absolute_error_quantiles(
            np.asarray([5.0, 6.0, 7.0, 8.0], dtype=np.float32),
            np.asarray([5.0, 5.0, 9.0, 4.0], dtype=np.float32),
        )

        self.assertAlmostEqual(quantiles["q50"], 1.5, places=5)
        self.assertGreaterEqual(quantiles["q80"], quantiles["q50"])
        self.assertGreaterEqual(quantiles["q95"], quantiles["q90"])

    def _engine(self, metadata: dict) -> KinoVanga:
        engine = KinoVanga.__new__(KinoVanga)
        engine.metadata = metadata
        return engine

    def test_empirical_q80_interval_is_clipped_to_rating_scale(self):
        engine = self._engine(
            {
                "test_abs_error_quantiles": {
                    "q50": 0.62,
                    "q80": 1.18,
                    "q90": 1.65,
                    "q95": 2.01,
                },
                "uncertainty_default_coverage": 0.80,
                "uncertainty_method": "temporal_holdout_absolute_error",
                "test_year_from": 2024,
                "test_year_to": 2025,
                "test_rows": 14000,
            }
        )

        interval = engine.uncertainty_for_rating(9.40)

        self.assertEqual(interval["lower"], 8.22)
        self.assertEqual(interval["upper"], 10.0)
        self.assertEqual(interval["margin"], 1.18)
        self.assertEqual(interval["coverage"], 0.8)
        self.assertEqual(interval["test_rows"], 14000)

    def test_old_metadata_gracefully_has_no_uncertainty(self):
        engine = self._engine({"test_mae": 1.02})

        self.assertIsNone(engine.uncertainty_for_rating(7.3))

    def test_quality_summary_uses_temporal_metadata(self):
        engine = self._engine(
            {
                "test_mae": 1.02,
                "test_rmse": 1.34,
                "test_r2": 0.28,
                "test_year_from": 2024,
                "test_year_to": 2025,
                "test_rows": 13993,
                "train_year_from": 1900,
                "train_year_to": 2023,
                "train_rows": 298298,
            }
        )

        quality = engine.quality_summary()

        self.assertEqual(quality["mae"], 1.02)
        self.assertEqual(quality["rmse"], 1.34)
        self.assertEqual(quality["r2"], 0.28)
        self.assertEqual(quality["test_year_from"], 2024)
        self.assertEqual(quality["train_rows"], 298298)


if __name__ == "__main__":
    unittest.main()
