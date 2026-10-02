from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd

from settings import config
from src.train_model import (
    CATEGORICAL_FEATURES,
    _pool_from_file,
    prepare_training_dataset,
)


def _build_batches():
    rows = []
    for year, count in [(2020, 60), (2021, 60), (2024, 20), (2025, 20)]:
        for index in range(count):
            rows.append(
                {
                    "startYear": (year - 1900) / 100.0,
                    "runtimeMinutes": 1.2,
                    "director_avg_rating": 6.5,
                    "actor_1_avg_rating": 6.5,
                    "actor_2_avg_rating": 6.5,
                    "actor_3_avg_rating": 6.5,
                    "title_len": 10.0,
                    "genres_combined": "Drama",
                    "director_id": f"nm_d_{index % 3}",
                    "actor_1_id": f"nm_a1_{index % 5}",
                    "actor_2_id": f"nm_a2_{index % 5}",
                    "actor_3_id": f"nm_a3_{index % 5}",
                    "label": 6.0 + (index % 10) / 10.0,
                }
            )

    frame = pd.DataFrame(rows)
    feature_names = [col for col in frame.columns if col != "label"]

    batches = []
    for start in range(0, len(frame), 40):
        part = frame.iloc[start:start + 40].reset_index(drop=True)
        X = part[feature_names].copy()
        y = part["label"].astype(np.float32)
        titles = [f"Фильм {start + i}" for i in range(len(part))]
        tconsts = [f"tt{start + i:07d}" for i in range(len(part))]
        batches.append((X, y, titles, tconsts))
    return batches


class DiskFirstTrainingTests(unittest.TestCase):
    def test_prepared_dataset_is_split_and_readable_by_catboost(self):
        batches = _build_batches()
        old_abspath = config.ABSPATH

        with tempfile.TemporaryDirectory() as tmp:
            config.ABSPATH = tmp
            try:
                def fake_get_batches(*_args, **_kwargs):
                    for X, y, titles, tconsts in batches:
                        yield X.copy(), y.copy(), list(titles), list(tconsts)

                with patch("src.train_model.get_batches", side_effect=fake_get_batches):
                    prepared = prepare_training_dataset(
                        [],
                        batch_size=40,
                    )

                try:
                    self.assertEqual(prepared.total_rows, 160)
                    self.assertEqual(prepared.train_rows, 120)
                    self.assertEqual(prepared.test_rows, 40)
                    self.assertEqual(prepared.test_from_year, 2024)
                    self.assertEqual(
                        prepared.categorical_features,
                        CATEGORICAL_FEATURES,
                    )

                    # На диске должны лежать реальные train/test, а не общий DataFrame.
                    self.assertTrue(prepared.train_path.is_file())
                    self.assertTrue(prepared.test_path.is_file())
                    self.assertTrue(prepared.column_description_path.is_file())

                    with prepared.train_path.open("r", encoding="utf-8") as handle:
                        self.assertEqual(sum(1 for _ in handle), 120)
                    with prepared.test_path.open("r", encoding="utf-8") as handle:
                        self.assertEqual(sum(1 for _ in handle), 40)

                    train_pool = _pool_from_file(
                        prepared.train_path,
                        prepared.column_description_path,
                    )
                    test_pool = _pool_from_file(
                        prepared.test_path,
                        prepared.column_description_path,
                    )
                    self.assertEqual(train_pool.num_row(), 120)
                    self.assertEqual(test_pool.num_row(), 40)
                    self.assertEqual(
                        train_pool.num_col(),
                        len(prepared.feature_names),
                    )
                finally:
                    prepared.cleanup()
                    self.assertFalse(prepared.root.exists())
            finally:
                config.ABSPATH = old_abspath

    def test_training_source_does_not_accumulate_all_batches(self):
        source = (
            Path(__file__).parents[1] / "src" / "train_model.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn("all_X", source)
        self.assertNotIn("all_y", source)
        self.assertNotIn("pd.concat(", source)

    def test_training_releases_train_pool_before_test_pool(self):
        source = (
            Path(__file__).parents[1] / "src" / "train_model.py"
        ).read_text(encoding="utf-8")

        # Train и test Pool не должны одновременно удерживать большой набор данных.
        train_release = source.index("del train_pool")
        test_create = source.index("test_pool = _pool_from_file")
        test_release = source.index("del test_pool, y_test, y_pred")

        self.assertLess(train_release, test_create)
        self.assertGreater(test_release, test_create)
        self.assertGreaterEqual(source.count("gc.collect()"), 2)


if __name__ == "__main__":
    unittest.main()
