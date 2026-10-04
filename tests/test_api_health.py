from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from settings import config
from src.runtime_descriptor import resolve_model_runtime_descriptor


class ApiHealthGenerationTests(unittest.TestCase):
    def test_runtime_descriptor_returns_generation_id_not_raw_pointer_json(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            models = root / "models"
            generation = "20261002T125203Z-52864a3c"
            release = models / "releases" / generation
            release.mkdir(parents=True)
            (release / "model.cbm").write_bytes(b"model")
            (release / "metadata.pkl").write_bytes(b"metadata")
            (models / "current.json").write_text(
                json.dumps(
                    {
                        "generation": generation,
                        "published_at": "2026-10-02T12:52:03+00:00",
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )

            with patch.object(config, "ABSPATH", str(root)):
                descriptor = resolve_model_runtime_descriptor()

        self.assertEqual(descriptor.generation, generation)
        self.assertEqual(descriptor.model_path, release / "model.cbm")
        self.assertEqual(descriptor.metadata_path, release / "metadata.pkl")
        self.assertEqual(descriptor.source, "pointer")
        self.assertNotIn("published_at", descriptor.generation)


if __name__ == "__main__":
    unittest.main()
