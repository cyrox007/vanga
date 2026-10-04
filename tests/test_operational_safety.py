from __future__ import annotations

import fcntl
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import retrain_job
from settings import config
from src.database import cleanup_temp


class OperationalSafetyTests(unittest.TestCase):
    def test_cleanup_temp_ne_udaliaet_aktivnyi_training_dataset(self):
        with tempfile.TemporaryDirectory() as tmp:
            old_abspath = config.ABSPATH
            config.ABSPATH = tmp
            try:
                marker = Path(tmp) / "temp" / "training" / "active" / "train.tsv"
                marker.parent.mkdir(parents=True)
                marker.write_text("active", encoding="utf-8")

                cleanup_temp()

                self.assertTrue(marker.exists())
                self.assertEqual(marker.read_text(encoding="utf-8"), "active")
            finally:
                config.ABSPATH = old_abspath

    def test_retrain_job_derzhit_odin_lock_dlia_update_i_training(self):
        with tempfile.TemporaryDirectory() as tmp:
            lock_path = Path(tmp) / "orchestration.lock"
            with mock.patch.dict(
                os.environ,
                {retrain_job.LOCK_PATH_ENV: str(lock_path)},
                clear=False,
            ), mock.patch("retrain_job._run") as run:
                code = retrain_job.main([])

            self.assertEqual(code, 0)
            self.assertEqual(run.call_count, 2)
            self.assertEqual(run.call_args_list[0].args[0], "ds_update.py")
            self.assertEqual(run.call_args_list[1].args[0], "traning.py")
            first_env = run.call_args_list[0].kwargs["env"]
            second_env = run.call_args_list[1].kwargs["env"]
            self.assertEqual(first_env[retrain_job.LOCK_HELD_ENV], "1")
            self.assertEqual(second_env[retrain_job.LOCK_HELD_ENV], "1")
            self.assertEqual(first_env[retrain_job.LOCK_PATH_ENV], str(lock_path))

    def test_retrain_job_ne_startuet_vtoroi_process_pri_zaniatom_lock(self):
        with tempfile.TemporaryDirectory() as tmp:
            lock_path = Path(tmp) / "orchestration.lock"
            lock_path.touch()
            with lock_path.open("a+", encoding="utf-8") as handle:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                try:
                    with mock.patch.dict(
                        os.environ,
                        {retrain_job.LOCK_PATH_ENV: str(lock_path)},
                        clear=False,
                    ), mock.patch("retrain_job._run") as run:
                        code = retrain_job.main([])
                finally:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

            self.assertEqual(code, 75)
            run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
