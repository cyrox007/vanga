from __future__ import annotations

import os
import unittest
from unittest.mock import MagicMock, patch

import traning
from src.data_filtr import _creative_team_features_enabled


class CreativeTeamCliTests(unittest.TestCase):
    def test_feature_flag_is_off_by_default_and_explicit_when_enabled(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(_creative_team_features_enabled())
        with patch.dict(
            os.environ,
            {"VANGA_TRAIN_CREATIVE_TEAM_FEATURES": "1"},
            clear=True,
        ):
            self.assertTrue(_creative_team_features_enabled())

    def test_candidate_v7_cannot_publish_through_training_entrypoint(self):
        with patch.object(traning, "train_catboost_model") as train_mock:
            with self.assertRaises(SystemExit) as raised:
                traning.main(["--creative-team-features", "--iterations", "1"])

        self.assertIn("пока нельзя публиковать", str(raised.exception))
        train_mock.assert_not_called()

    def test_candidate_v7_requires_coverage_schema(self):
        with self.assertRaises(SystemExit) as raised:
            traning.main(
                [
                    "--creative-team-features",
                    "--without-coverage-features",
                    "--evaluation-only",
                    "--iterations",
                    "1",
                ]
            )
        self.assertIn("поверх coverage schema v6", str(raised.exception))

    def test_evaluation_marks_candidate_as_schema_v7_without_publication(self):
        metadata = {
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
            "_save_temporary_artifact",
            return_value=(MagicMock(parent=MagicMock()), 1024),
        ), patch.object(traning.shutil, "rmtree"), patch.object(
            traning,
            "save_trained_model",
        ) as save_mock:
            traning.main(
                [
                    "--creative-team-features",
                    "--evaluation-only",
                    "--iterations",
                    "1",
                ]
            )

        self.assertEqual(metadata["schema_version"], 7)
        self.assertEqual(metadata["coverage_features_version"], 1)
        self.assertEqual(metadata["creative_team_features_version"], 1)
        save_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
