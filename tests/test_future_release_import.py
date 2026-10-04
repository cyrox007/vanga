from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from src.future_release_import import FutureReleaseBatchImporter, FutureReleaseImportError


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class FutureReleaseImportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "future.duckdb"
        self.importer = FutureReleaseBatchImporter(self.db)

    def tearDown(self):
        self.importer.close()
        self.tmp.cleanup()

    def _batch(self, *, batch_id="batch-1", fingerprint=None):
        return {
            "version": 1,
            "batch_id": batch_id,
            "provider": "official-feed",
            "retrieved_at": "2026-10-04T08:00:00Z",
            "cursor": "cursor-10",
            "source_fingerprint_sha256": fingerprint or _sha("source-snapshot-1"),
            "bundle": {
                "sources": [
                    {
                        "source_id": "official",
                        "provider": "Official Feed",
                        "usage_basis": "public_record",
                        "retrieved_at": "2026-10-04T08:00:00Z",
                    }
                ],
                "projects": [
                    {
                        "project_id": "film-a",
                        "imdb_id": "tt1234567",
                        "canonical_title": "Future Film",
                    }
                ],
                "people": [
                    {
                        "person_id": "director-a",
                        "imdb_id": "nm1234567",
                        "canonical_name": "Director A",
                    }
                ],
                "entities": [],
                "aliases": [
                    {
                        "alias_id": "alias-a",
                        "project_id": "film-a",
                        "alias": "Future Film",
                        "known_at": "2026-10-04T07:50:00Z",
                        "source_id": "official",
                        "confidence": 1.0,
                    }
                ],
                "release_windows": [
                    {
                        "observation_id": "release-a",
                        "project_id": "film-a",
                        "release_start_at": "2027-07-01T00:00:00Z",
                        "precision": "exact",
                        "known_at": "2026-10-04T07:50:00Z",
                        "source_id": "official",
                        "confidence": 1.0,
                    }
                ],
                "statuses": [],
                "project_people": [
                    {
                        "link_id": "director-link-a",
                        "project_id": "film-a",
                        "person_id": "director-a",
                        "role": "director",
                        "known_at": "2026-10-04T07:50:00Z",
                        "source_id": "official",
                        "confidence": 1.0,
                    }
                ],
                "project_entities": [],
            },
        }

    def test_atomic_import_persists_bundle_and_history(self):
        result = self.importer.import_batch(self._batch())
        self.assertFalse(result["idempotent"])
        self.assertEqual(result["counts"]["projects"], 1)
        self.assertEqual(result["counts"]["release_windows"], 1)

        snapshot = self.importer.store.snapshot_as_of(
            "film-a", "2026-10-04T08:00:00Z"
        )
        self.assertEqual(snapshot["release_at"], "2027-07-01T00:00:00+00:00")
        self.assertEqual(snapshot["directors"][0]["person_id"], "director-a")
        history = self.importer.history("official-feed")
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["batch_id"], "batch-1")

    def test_same_batch_is_idempotent_without_duplicate_observations(self):
        first = self.importer.import_batch(self._batch())
        second = self.importer.import_batch(self._batch())
        self.assertFalse(first["idempotent"])
        self.assertTrue(second["idempotent"])
        count = self.importer.conn.execute(
            "SELECT COUNT(*) FROM future_release_windows"
        ).fetchone()[0]
        self.assertEqual(count, 1)

    def test_same_source_fingerprint_with_new_batch_id_is_idempotent(self):
        self.importer.import_batch(self._batch())
        result = self.importer.import_batch(self._batch(batch_id="batch-copy"))
        self.assertTrue(result["idempotent"])
        self.assertEqual(result["batch_id"], "batch-1")
        self.assertEqual(result["duplicate_batch_id"], "batch-copy")
        self.assertEqual(len(self.importer.history()), 1)

    def test_same_source_fingerprint_cannot_describe_different_bundle(self):
        first = self._batch()
        self.importer.import_batch(first)
        changed = self._batch(batch_id="batch-mutated")
        changed["bundle"]["projects"][0]["canonical_title"] = "Mutated"
        with self.assertRaises(FutureReleaseImportError):
            self.importer.import_batch(changed)

    def test_broken_reference_rolls_back_whole_bundle(self):
        payload = self._batch()
        payload["bundle"]["project_people"][0]["person_id"] = "missing-person"
        with self.assertRaises(Exception):
            self.importer.import_batch(payload)
        project_count = self.importer.conn.execute(
            "SELECT COUNT(*) FROM future_release_projects"
        ).fetchone()[0]
        source_count = self.importer.conn.execute(
            "SELECT COUNT(*) FROM future_release_sources"
        ).fetchone()[0]
        history_count = self.importer.conn.execute(
            "SELECT COUNT(*) FROM future_release_import_batches"
        ).fetchone()[0]
        self.assertEqual(project_count, 0)
        self.assertEqual(source_count, 0)
        self.assertEqual(history_count, 0)

    def test_source_retrieved_at_cannot_be_after_batch_retrieved_at(self):
        payload = self._batch()
        payload["bundle"]["sources"][0]["retrieved_at"] = "2026-10-04T09:00:00Z"
        with self.assertRaises(FutureReleaseImportError):
            self.importer.import_batch(payload)

    def test_unknown_bundle_array_is_rejected(self):
        payload = self._batch()
        payload["bundle"]["magic_quality"] = []
        with self.assertRaises(FutureReleaseImportError):
            self.importer.import_batch(payload)

    def test_batch_id_mutation_is_rejected(self):
        self.importer.import_batch(self._batch())
        mutated = self._batch(fingerprint=_sha("different-source"))
        with self.assertRaises(FutureReleaseImportError):
            self.importer.import_batch(mutated)


if __name__ == "__main__":
    unittest.main()
