from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import ds_update


class DsUpdateStagingTests(unittest.TestCase):
    def test_freshness_manifest_path_can_be_redirected_to_stage(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "candidate-manifest.json"
            with mock.patch.dict(
                os.environ,
                {"VANGA_FRESHNESS_MANIFEST_PATH": str(target)},
                clear=False,
            ):
                self.assertEqual(ds_update._freshness_manifest_path(), target)

    def test_staging_can_skip_rating_history_until_database_is_published(self):
        report = {"logical_fingerprint_sha256": "a" * 64}
        with mock.patch.dict(
            os.environ,
            {"VANGA_SKIP_RATING_HISTORY": "1"},
            clear=False,
        ), mock.patch.object(ds_update, "RatingHistoryStore") as history:
            ds_update._capture_rating_history(Path("/tmp/candidate.duckdb"), report)

        history.assert_not_called()

    def test_normal_runtime_still_captures_rating_history(self):
        report = {"logical_fingerprint_sha256": "b" * 64}
        store = mock.MagicMock()
        store.capture_daily.return_value = {
            "snapshot_day": "2026-10-04",
            "tracked_count": 0,
            "captured_count": 0,
            "missing_count": 0,
            "skipped_no_watchlist": True,
        }
        context = mock.MagicMock()
        context.__enter__.return_value = store
        context.__exit__.return_value = False

        env = dict(os.environ)
        env.pop("VANGA_SKIP_RATING_HISTORY", None)
        with mock.patch.dict(os.environ, env, clear=True), mock.patch.object(
            ds_update,
            "RatingHistoryStore",
            return_value=context,
        ):
            ds_update._capture_rating_history(Path("/tmp/imdb.duckdb"), report)

        store.capture_daily.assert_called_once_with(
            Path("/tmp/imdb.duckdb"),
            source_fingerprint_sha256="b" * 64,
        )


if __name__ == "__main__":
    unittest.main()
