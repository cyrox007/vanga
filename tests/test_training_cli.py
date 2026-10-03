from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from settings import config
import traning


FRESHNESS_REPORT = {
    "as_of": "2026-10-03T12:00:00+00:00",
    "stable_history_through_year": 2025,
    "recommended_training_target_max_year": 2025,
    "current_year_status": "provisional",
    "logical_fingerprint_sha256": "a" * 64,
}


class TrainingCliTests(unittest.TestCase):
    def test_smoke_mode_does_not_publish_current_model_or_require_freshness(self):
        old_abspath = config.ABSPATH
        with tempfile.TemporaryDirectory() as tmp:
            config.ABSPATH = tmp
            try:
                fake_model = Mock()

                def fake_save_model(path, format="cbm"):
                    self.assertEqual(format, "cbm")
                    Path(path).write_bytes(b"smoke-model")

                fake_model.save_model.side_effect = fake_save_model

                with (
                    patch("traning.get_all_genres", return_value=["Drama"]),
                    patch(
                        "traning.train_catboost_model",
                        return_value=(fake_model, {"feature_names": ["x"]}),
                    ) as train_mock,
                    patch("traning.interpret_model"),
                    patch("traning.save_trained_model") as publish_mock,
                    patch("traning.require_fresh_imdb_data") as freshness_mock,
                ):
                    traning.main(["--smoke"])

                train_mock.assert_called_once_with(
                    ["Drama"],
                    batch_size=10000,
                    max_batches=None,
                    iterations=50,
                )
                freshness_mock.assert_not_called()
                publish_mock.assert_not_called()
                self.assertFalse((Path(tmp) / "models" / "current.json").exists())
                self.assertFalse((Path(tmp) / "temp" / "smoke").exists())
            finally:
                config.ABSPATH = old_abspath

    def test_evaluation_only_does_not_require_freshness(self):
        old_abspath = config.ABSPATH
        with tempfile.TemporaryDirectory() as tmp:
            config.ABSPATH = tmp
            try:
                fake_model = Mock()

                def fake_save_model(path, format="cbm"):
                    self.assertEqual(format, "cbm")
                    Path(path).write_bytes(b"evaluation-model")

                fake_model.save_model.side_effect = fake_save_model

                with (
                    patch("traning.get_all_genres", return_value=["Drama"]),
                    patch(
                        "traning.train_catboost_model",
                        return_value=(fake_model, {"feature_names": ["x"]}),
                    ),
                    patch("traning.interpret_model"),
                    patch("traning.save_trained_model") as publish_mock,
                    patch("traning.require_fresh_imdb_data") as freshness_mock,
                ):
                    traning.main(["--evaluation-only"])

                freshness_mock.assert_not_called()
                publish_mock.assert_not_called()
            finally:
                config.ABSPATH = old_abspath

    def test_full_mode_keeps_default_iterations_and_requires_freshness(self):
        fake_model = Mock()
        with (
            patch("traning.require_fresh_imdb_data", return_value=FRESHNESS_REPORT) as freshness_mock,
            patch("traning.get_all_genres", return_value=["Drama"]),
            patch(
                "traning.train_catboost_model",
                return_value=(fake_model, {"feature_names": ["x"]}),
            ) as train_mock,
            patch("traning.interpret_model"),
            patch("traning.save_trained_model") as publish_mock,
        ):
            traning.main([])

        freshness_mock.assert_called_once_with()
        train_mock.assert_called_once_with(
            ["Drama"],
            batch_size=10000,
            max_batches=None,
            iterations=1500,
        )
        publish_mock.assert_called_once()
        published_metadata = publish_mock.call_args.args[1]
        self.assertEqual(
            published_metadata["imdb_data_freshness"]["stable_history_through_year"],
            2025,
        )
        self.assertEqual(
            published_metadata["imdb_data_freshness"]["logical_fingerprint_sha256"],
            "a" * 64,
        )

    def test_full_mode_stops_before_training_when_freshness_fails(self):
        with (
            patch(
                "traning.require_fresh_imdb_data",
                side_effect=RuntimeError("IMDb Data Freshness guard: stale"),
            ),
            patch("traning.get_all_genres") as genres_mock,
            patch("traning.train_catboost_model") as train_mock,
        ):
            with self.assertRaises(SystemExit):
                traning.main([])

        genres_mock.assert_not_called()
        train_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
