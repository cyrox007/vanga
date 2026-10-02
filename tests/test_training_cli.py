from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from settings import config
import traning


class TrainingCliTests(unittest.TestCase):
    def test_smoke_mode_does_not_publish_current_model(self):
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
                ):
                    traning.main(["--smoke"])

                train_mock.assert_called_once_with(
                    ["Drama"],
                    batch_size=10000,
                    max_batches=None,
                    iterations=50,
                )
                publish_mock.assert_not_called()
                self.assertFalse((Path(tmp) / "models" / "current.json").exists())
                self.assertFalse((Path(tmp) / "temp" / "smoke").exists())
            finally:
                config.ABSPATH = old_abspath

    def test_full_mode_keeps_default_iterations(self):
        fake_model = Mock()
        with (
            patch("traning.get_all_genres", return_value=["Drama"]),
            patch(
                "traning.train_catboost_model",
                return_value=(fake_model, {"feature_names": ["x"]}),
            ) as train_mock,
            patch("traning.interpret_model"),
            patch("traning.save_trained_model") as publish_mock,
        ):
            traning.main([])

        train_mock.assert_called_once_with(
            ["Drama"],
            batch_size=10000,
            max_batches=None,
            iterations=1500,
        )
        publish_mock.assert_called_once()


if __name__ == "__main__":
    unittest.main()
