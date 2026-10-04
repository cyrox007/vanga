from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from settings import config
from src.runtime_identity import build_runtime_identity


class _Engine:
    def __init__(self, model_path: Path, db_path: Path) -> None:
        self.model_path = model_path
        self.db_path = db_path
        self.metadata = {
            "schema_version": 15,
            "training_mode": "temporal_validation_then_stable_refit",
            "published_model_fit": "all_stable_targets",
            "published_metrics_source": "separate_temporal_validation_model",
            "validation_target_max_year": 2025,
            "refit_year_from": 1900,
            "refit_year_to": 2025,
            "refit_rows": 12345,
            "model_size_bytes": 456,
            "test_dataset_fingerprint_sha256": "a" * 64,
            "imdb_data_freshness": {
                "logical_fingerprint_sha256": "b" * 64,
            },
        }


class RuntimeIdentityTests(unittest.TestCase):
    def test_descriptor_binds_generation_to_exact_database_snapshot(self) -> None:
        old_abspath = config.ABSPATH
        try:
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                config.ABSPATH = str(root)
                model = root / "models" / "releases" / "g1" / "model.cbm"
                model.parent.mkdir(parents=True)
                model.write_bytes(b"model")
                db = root / "imdb.duckdb"
                db.write_bytes(b"db-v1")
                stat = db.stat()
                manifest_dir = root / "data" / "imdb"
                manifest_dir.mkdir(parents=True)
                manifest = {
                    "database": str(db),
                    "database_size_bytes": stat.st_size,
                    "database_mtime": datetime.fromtimestamp(
                        stat.st_mtime, tz=timezone.utc
                    ).isoformat(),
                    "logical_fingerprint_sha256": "c" * 64,
                    "as_of": "2026-10-04T10:00:00+00:00",
                }
                (manifest_dir / "freshness-manifest.json").write_text(
                    json.dumps(manifest), encoding="utf-8"
                )

                engine = _Engine(model, db)
                first = build_runtime_identity(engine, "g1")
                self.assertEqual(first["model"]["generation"], "g1")
                self.assertEqual(first["model"]["schema_version"], 15)
                self.assertEqual(
                    first["model"]["published_metrics_source"],
                    "separate_temporal_validation_model",
                )
                self.assertTrue(first["database"]["freshness_manifest_verified"])
                self.assertEqual(
                    first["database"]["logical_fingerprint_sha256"],
                    "c" * 64,
                )

                # Та же generation с другой фактически открытой DB обязана иметь
                # другой runtime fingerprint и перестать доверять старому manifest.
                db.write_bytes(b"db-v2-is-different")
                second = build_runtime_identity(engine, "g1")
                self.assertEqual(second["model"]["generation"], "g1")
                self.assertFalse(second["database"]["freshness_manifest_verified"])
                self.assertIsNone(second["database"]["logical_fingerprint_sha256"])
                self.assertNotEqual(
                    first["runtime_fingerprint_sha256"],
                    second["runtime_fingerprint_sha256"],
                )
        finally:
            config.ABSPATH = old_abspath


if __name__ == "__main__":
    unittest.main()
