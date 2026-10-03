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

VALIDATION_METADATA = {
    "feature_names": ["x"],
    "test_mae": 1.0,
    "test_rmse": 1.2,
    "test_r2": 0.2,
    "test_year_from": 2024,
    "test_year_to": 2025,
    "test_rows": 50,
}


class TrainingCliTests(unittest.TestCase):
    def test_smoke_mode_does_not_publish_or_refit_or_require_freshness(self):
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
                    patch("traning.train_final_refit_model") as refit_mock,
                    patch("traning.evaluate_validation_candidate") as gate_mock,
                ):
                    traning.main(["--smoke"])

                train_mock.assert_called_once_with(
                    ["Drama"],
                    batch_size=10000,
                    max_batches=None,
                    iterations=50,
                )
                freshness_mock.assert_not_called()
                gate_mock.assert_not_called()
                refit_mock.assert_not_called()
                publish_mock.assert_not_called()
                self.assertFalse((Path(tmp) / "models" / "current.json").exists())
                self.assertFalse((Path(tmp) / "temp" / "smoke").exists())
            finally:
                config.ABSPATH = old_abspath

    def test_evaluation_only_does_not_require_freshness_or_refit(self):
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
                    patch("traning.train_final_refit_model") as refit_mock,
                    patch("traning.evaluate_validation_candidate") as gate_mock,
                ):
                    traning.main(["--evaluation-only"])

                freshness_mock.assert_not_called()
                gate_mock.assert_not_called()
                refit_mock.assert_not_called()
                publish_mock.assert_not_called()
            finally:
                config.ABSPATH = old_abspath

    def test_full_mode_validates_stable_year_then_refits_and_publishes_final_model(self):
        validation_model = Mock(name="validation_model")
        final_model = Mock(name="final_model")
        gate = {
            "passed": True,
            "comparable": True,
            "reason": "within_mae_gate",
        }
        refit_metadata = {
            "refit_rows": 1234,
            "refit_year_from": 1910,
            "refit_year_to": 2025,
            "refit_target_max_year": 2025,
        }

        with (
            patch("traning.require_fresh_imdb_data", return_value=FRESHNESS_REPORT) as freshness_mock,
            patch("traning.get_all_genres", return_value=["Drama"]),
            patch(
                "traning.train_catboost_model",
                return_value=(validation_model, dict(VALIDATION_METADATA)),
            ) as train_mock,
            patch("traning.interpret_model") as interpret_mock,
            patch("traning.evaluate_validation_candidate", return_value=gate) as gate_mock,
            patch(
                "traning.train_final_refit_model",
                return_value=(final_model, refit_metadata),
            ) as refit_mock,
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
        gate_mock.assert_called_once()
        refit_mock.assert_called_once()
        refit_kwargs = refit_mock.call_args.kwargs
        self.assertEqual(refit_kwargs["max_target_year"], 2025)
        self.assertEqual(refit_kwargs["expected_feature_names"], ["x"])
        self.assertEqual(refit_kwargs["iterations"], 1500)

        publish_mock.assert_called_once()
        self.assertIs(publish_mock.call_args.args[0], final_model)
        published_metadata = publish_mock.call_args.args[1]
        self.assertEqual(published_metadata["training_mode"], "temporal_validation_then_stable_refit")
        self.assertEqual(published_metadata["published_model_fit"], "all_stable_targets")
        self.assertEqual(published_metadata["published_metrics_source"], "separate_temporal_validation_model")
        self.assertEqual(published_metadata["refit_year_to"], 2025)
        self.assertEqual(
            published_metadata["imdb_data_freshness"]["stable_history_through_year"],
            2025,
        )
        self.assertEqual(
            published_metadata["imdb_data_freshness"]["logical_fingerprint_sha256"],
            "a" * 64,
        )
        self.assertEqual(interpret_mock.call_count, 2)

    def test_failed_validation_gate_does_not_start_refit_or_publish(self):
        validation_model = Mock()
        with (
            patch("traning.require_fresh_imdb_data", return_value=FRESHNESS_REPORT),
            patch("traning.get_all_genres", return_value=["Drama"]),
            patch(
                "traning.train_catboost_model",
                return_value=(validation_model, dict(VALIDATION_METADATA)),
            ),
            patch("traning.interpret_model"),
            patch(
                "traning.evaluate_validation_candidate",
                side_effect=RuntimeError("mae_regression"),
            ),
            patch("traning.train_final_refit_model") as refit_mock,
            patch("traning.save_trained_model") as publish_mock,
        ):
            with self.assertRaises(SystemExit):
                traning.main([])

        refit_mock.assert_not_called()
        publish_mock.assert_not_called()

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
