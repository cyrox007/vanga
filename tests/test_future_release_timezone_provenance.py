from __future__ import annotations

import os
import tempfile
import time
import unittest
from pathlib import Path

from src.future_releases import FutureReleaseError, FutureReleaseStore


class FutureReleaseTimezoneProvenanceTests(unittest.TestCase):
    def _store(self, root: Path) -> FutureReleaseStore:
        return FutureReleaseStore(root / "future.duckdb")

    def test_store_forces_utc_even_when_process_timezone_is_moscow(self) -> None:
        previous = os.environ.get("TZ")
        try:
            os.environ["TZ"] = "Europe/Moscow"
            if hasattr(time, "tzset"):
                time.tzset()
            with tempfile.TemporaryDirectory() as tmp:
                with self._store(Path(tmp)) as store:
                    timezone_name = store.conn.execute(
                        "SELECT current_setting('TimeZone')"
                    ).fetchone()[0]
                    self.assertEqual(str(timezone_name), "UTC")
                    store.upsert_source(
                        {
                            "source_id": "source-1",
                            "provider": "test",
                            "usage_basis": "public_record",
                            "retrieved_at": "2026-10-04T08:00:00Z",
                        }
                    )
                    store.upsert_project(
                        {
                            "project_id": "project-1",
                            "canonical_title": "Film",
                        }
                    )
                    store.add_release_window(
                        {
                            "observation_id": "release-1",
                            "project_id": "project-1",
                            "release_start_at": "2027-05-20T00:00:00Z",
                            "release_end_at": "2027-05-20T00:00:00Z",
                            "precision": "exact",
                            "known_at": "2026-10-04T08:00:00Z",
                            "source_id": "source-1",
                            "confidence": 1.0,
                        }
                    )
                    snapshot = store.snapshot_as_of(
                        "project-1", "2026-10-04T09:00:00Z"
                    )
                    self.assertEqual(snapshot["cutoff_at"], "2026-10-04T09:00:00+00:00")
                    candidate = snapshot["release_candidates"][0]
                    self.assertEqual(
                        candidate["release_start_at"], "2027-05-20T00:00:00+00:00"
                    )
                    self.assertEqual(
                        candidate["evidence"][0]["known_at"],
                        "2026-10-04T08:00:00+00:00",
                    )
        finally:
            if previous is None:
                os.environ.pop("TZ", None)
            else:
                os.environ["TZ"] = previous
            if hasattr(time, "tzset"):
                time.tzset()

    def test_source_snapshot_cannot_be_rewritten_with_later_retrieval(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self._store(Path(tmp)) as store:
                original = {
                    "source_id": "source-immutable",
                    "provider": "test",
                    "url": "https://example.test/source",
                    "usage_basis": "public_record",
                    "retrieved_at": "2026-10-04T08:00:00Z",
                }
                self.assertEqual(store.upsert_source(original), "source-immutable")
                self.assertEqual(store.upsert_source(original), "source-immutable")
                changed = dict(original)
                changed["retrieved_at"] = "2026-10-05T08:00:00Z"
                with self.assertRaisesRegex(FutureReleaseError, "immutable snapshot"):
                    store.upsert_source(changed)

    def test_stable_alias_id_preserves_first_known_observation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self._store(Path(tmp)) as store:
                for suffix, retrieved_at in (
                    ("early", "2026-10-04T08:00:00Z"),
                    ("late", "2026-10-05T08:00:00Z"),
                ):
                    store.upsert_source(
                        {
                            "source_id": f"source-{suffix}",
                            "provider": "test",
                            "usage_basis": "public_record",
                            "retrieved_at": retrieved_at,
                        }
                    )
                store.upsert_project(
                    {"project_id": "project-1", "canonical_title": "Film"}
                )
                payload = {
                    "alias_id": "logical-alias",
                    "project_id": "project-1",
                    "alias": "Film",
                    "known_at": "2026-10-04T08:00:00Z",
                    "source_id": "source-early",
                    "confidence": 0.9,
                }
                store.add_alias(payload)
                later = dict(payload)
                later.update(
                    known_at="2026-10-05T08:00:00Z",
                    source_id="source-late",
                    confidence=1.0,
                )
                self.assertEqual(store.add_alias(later), "logical-alias")
                row = store.conn.execute(
                    """
                    SELECT known_at, source_id, confidence
                    FROM future_release_aliases WHERE alias_id = 'logical-alias'
                    """
                ).fetchone()
                self.assertEqual(row[0].isoformat(), "2026-10-04T08:00:00+00:00")
                self.assertEqual(str(row[1]), "source-early")
                self.assertAlmostEqual(float(row[2]), 0.9)


if __name__ == "__main__":
    unittest.main()
