from __future__ import annotations

import logging
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import src.logger as logger_module


class LoggerPathTests(unittest.TestCase):
    def tearDown(self):
        # Удаляем обработчики тестового логгера, чтобы не держать открытые файлы.
        logger = logging.getLogger("tests.logger.path")
        for handler in list(logger.handlers):
            handler.close()
            logger.removeHandler(handler)

    def test_logger_does_not_depend_on_current_working_directory(self):
        project_root = Path(logger_module.__file__).resolve().parents[1]
        expected_logs = project_root / ".logs"

        with tempfile.TemporaryDirectory() as tmp:
            old_cwd = Path.cwd()
            try:
                os.chdir(tmp)
                logger = logger_module.setup_logger("tests.logger.path")
                logger.info("Проверка пути логирования")
            finally:
                os.chdir(old_cwd)

        self.assertTrue(expected_logs.is_dir())
        self.assertTrue((expected_logs / "app.log").exists())


if __name__ == "__main__":
    unittest.main()
