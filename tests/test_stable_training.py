from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np
import pandas as pd

from settings import config
from src.stable_training import (
    evaluate_validation_candidate,
    make_year_limited_batches,
    train_final_refit_model,
)


FEATURE_NAMES = [
    "startYear",
    "genres_combined",
    "director_id",
    "writer_id",
    "actor_1_id",
    "actor_2_id",
    "actor_3_id",
]


def _frame(years: list[int]) -> tuple[pd.DataFrame, pd.Series, list[str], list[str]]:
    rows = len(years)
    X = pd.DataFrame(
        {
            "startYear": [(year - 1900) / 100.0 for year in years],
            "genres_combined": ["Drama"] * rows,
            "director_id": ["nm-d"] * rows,
            "writer_id": ["nm-w"] * rows,
            "actor_1_id": ["nm-a1"] * rows,
            "actor_2_id": ["nm-a2"] * rows,
            "actor_3_id": ["nm-a3"] * rows,
        }
    )
    y = pd.Series(np.linspace(6.0, 8.0, rows), dtype=np.float32)
    titles = [f"Film {i}" for i in range(rows)]
    ids = [f"tt{i:07d}" for i in range(rows)]
    return X, y, titles, ids


class StableTrainingTests(unittest.TestCase):
    def test_year_limited_batches_exclude_provisional_targets(self):
        batch = _frame([2024, 2025, 2026, 2023])

        def base(*_args, **_kwargs):
            yield batch

        limited = make_year_limited_batches(base, 2025)
        X, y, titles, ids = next(limited(["Drama"]))
        actual_years = np.rint(X["startYear"].to_numpy() * 100 + 1900).astype(int)
        self.assertEqual(actual_years.tolist(), [2024, 2025, 2023])
        self.assertEqual(len(y), 3)
        self.assertEqual(titles, ["Film 0", "Film 1", "Film 3"])
        self.assertEqual(ids, ["tt0000000", "tt0000001", "tt0000003"])

    def test_year_limited_batches_skip_batch_with_only_future_targets(self):
        batch = _frame([2026, 2027])

        def base(*_args, **_kwargs):
            yield batch

        limited = make_year_limited_batches(base, 2025)
        self.assertEqual(list(limited(["Drama"])), [])

    def test_final_refit_uses_all_stable_rows_and_same_feature_schema(self):
        years = [2024] * 60 + [2025] * 60 + [2026] * 20
        batch = _frame(years)

        def base(*_args, **_kwargs):
            yield batch

        old_abspath = config.ABSPATH
        with tempfile.TemporaryDirectory() as temp:
            config.ABSPATH = temp
            fake_model = Mock()
            try:
                with (
                    patch("src.stable_training._ensure_free_disk"),
                    patch("src.stable_training._pool_from_file", return_value=Mock()),
                    patch("src.stable_training._new_regressor", return_value=fake_model),
                ):
                    model, metadata = train_final_refit_model(
                        ["Drama"],
                        base_get_batches=base,
                        expected_feature_names=FEATURE_NAMES,
                        max_target_year=2025,
                        batch_size=1000,
                        iterations=123,
                    )
            finally:
                config.ABSPATH = old_abspath

        self.assertIs(model, fake_model)
        fake_model.fit.assert_called_once()
        self.assertEqual(metadata["refit_rows"], 120)
        self.assertEqual(metadata["refit_year_from"], 2024)
        self.assertEqual(metadata["refit_year_to"], 2025)
        self.assertEqual(metadata["refit_target_max_year"], 2025)
        self.assertEqual(metadata["refit_iterations"], 123)

    def test_final_refit_rejects_feature_schema_drift(self):
        X, y, titles, ids = _frame([2024] * 120)
        X["unexpected"] = 1.0

        def base(*_args, **_kwargs):
            yield X, y, titles, ids

        old_abspath = config.ABSPATH
        with tempfile.TemporaryDirectory() as temp:
            config.ABSPATH = temp
            try:
                with patch("src.stable_training._ensure_free_disk"):
                    with self.assertRaises(RuntimeError):
                        train_final_refit_model(
                            ["Drama"],
                            base_get_batches=base,
                            expected_feature_names=FEATURE_NAMES,
                            max_target_year=2025,
                            iterations=10,
                        )
            finally:
                config.ABSPATH = old_abspath

    def test_pre_refit_quality_gate_rejects_regression(self):
        active = {
            "test_mae": 1.00,
            "test_year_from": 2024,
            "test_year_to": 2025,
        }
        candidate = {
            "test_mae": 1.20,
            "test_year_from": 2024,
            "test_year_to": 2025,
        }
        with patch(
            "src.stable_training.train_model_module._read_active_metadata",
            return_value=active,
        ):
            with self.assertRaises(RuntimeError):
                evaluate_validation_candidate(candidate)


if __name__ == "__main__":
    unittest.main()
