from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import api


class ApiHealthGenerationTests(unittest.TestCase):
    def test_generation_key_returns_generation_id_not_raw_pointer_json(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            models = root / "models"
            models.mkdir()
            (models / "current.json").write_text(
                json.dumps(
                    {
                        "generation": "20261002T125203Z-52864a3c",
                        "published_at": "2026-10-02T12:52:03+00:00",
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )

            with patch.object(api.config, "ABSPATH", str(root)):
                generation = api._generation_key(
                    models / "releases" / "20261002T125203Z-52864a3c" / "model.cbm"
                )

        self.assertEqual(generation, "20261002T125203Z-52864a3c")
        self.assertNotIn("published_at", generation)


if __name__ == "__main__":
    unittest.main()
