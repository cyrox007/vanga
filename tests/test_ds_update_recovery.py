from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import ds_update


class DsUpdateRecoveryTests(unittest.TestCase):
    def test_stale_materialization_reason_requires_rebuild(self):
        target = Path("/tmp/imdb.duckdb")
        with mock.patch.object(
            ds_update,
            "_freshness_report",
            return_value={
                "blocking_reasons": [
                    "dataset_stale:title.basics:20.0d>14d",
                    "database_older_than_downloaded_datasets",
                ]
            },
        ):
            self.assertTrue(
                ds_update._database_is_older_than_downloaded_datasets(target)
            )

    def test_unrelated_freshness_problem_does_not_force_rebuild(self):
        target = Path("/tmp/imdb.duckdb")
        with mock.patch.object(
            ds_update,
            "_freshness_report",
            return_value={
                "blocking_reasons": ["dataset_stale:title.basics:20.0d>14d"]
            },
        ):
            self.assertFalse(
                ds_update._database_is_older_than_downloaded_datasets(target)
            )

    def test_second_run_rebuilds_when_archives_are_newer_but_downloads_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "imdb.duckdb"
            target.write_bytes(b"old-db")
            final_report = {
                "logical_fingerprint_sha256": "a" * 64,
                "ready_for_full_training": True,
            }

            with mock.patch.object(
                ds_update.config,
                "IMDB_DB_PATH",
                str(target),
            ), mock.patch.object(
                ds_update,
                "download_imdb_dataset",
                return_value=False,
            ) as download, mock.patch.object(
                ds_update,
                "_validate_database",
            ) as validate, mock.patch.object(
                ds_update,
                "_database_is_older_than_downloaded_datasets",
                return_value=True,
            ) as stale, mock.patch.object(
                ds_update,
                "_build_staged_database",
            ) as build, mock.patch.object(
                ds_update,
                "cleanup_temp",
            ) as cleanup, mock.patch.object(
                ds_update,
                "_write_freshness_manifest",
                return_value=final_report,
            ) as manifest, mock.patch.object(
                ds_update,
                "_capture_rating_history",
            ) as capture:
                code = ds_update.main()

            self.assertEqual(code, 0)
            self.assertEqual(download.call_count, len(ds_update.DATASETS))
            validate.assert_called_once_with(target)
            stale.assert_called_once_with(target)
            build.assert_called_once_with(target)
            cleanup.assert_called_once_with()
            manifest.assert_called_once_with(target)
            capture.assert_called_once_with(target, final_report)

    def test_unchanged_materialized_database_is_not_rebuilt(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "imdb.duckdb"
            target.write_bytes(b"current-db")
            report = {
                "logical_fingerprint_sha256": "b" * 64,
                "ready_for_full_training": True,
            }

            with mock.patch.object(
                ds_update.config,
                "IMDB_DB_PATH",
                str(target),
            ), mock.patch.object(
                ds_update,
                "download_imdb_dataset",
                return_value=False,
            ), mock.patch.object(
                ds_update,
                "_validate_database",
            ), mock.patch.object(
                ds_update,
                "_database_is_older_than_downloaded_datasets",
                return_value=False,
            ), mock.patch.object(
                ds_update,
                "_build_staged_database",
            ) as build, mock.patch.object(
                ds_update,
                "_write_freshness_manifest",
                return_value=report,
            ), mock.patch.object(
                ds_update,
                "_capture_rating_history",
            ) as capture:
                code = ds_update.main()

            self.assertEqual(code, 0)
            build.assert_not_called()
            capture.assert_called_once_with(target, report)


if __name__ == "__main__":
    unittest.main()
