from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from src.rating_dataset import RatingMilestoneDatasetBuilder
from src.rating_history import RatingHistoryError, RatingHistoryStore


class RatingDatasetTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = RatingHistoryStore(Path(self.tmp.name) / "history.duckdb")
        self.builder = RatingMilestoneDatasetBuilder(self.store)

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def _point(self, imdb_id: str, snapshot_id: str, observed_at: str, rating: float):
        dt = datetime.fromisoformat(observed_at.replace("Z", "+00:00")).astimezone(timezone.utc)
        self.store.conn.execute(
            """
            INSERT INTO rating_snapshots(
                snapshot_id, snapshot_day, observed_at, source_fingerprint_sha256,
                tracked_count, captured_count, missing_count, created_at
            ) VALUES (?, ?, ?, ?, 1, 1, 0, ?)
            """,
            [snapshot_id, dt.date(), dt, "a" * 64, dt],
        )
        self.store.conn.execute(
            "INSERT INTO rating_points VALUES (?, ?, ?, ?)",
            [snapshot_id, imdb_id, rating, 100],
        )

    def _cases(self):
        return [
            {"case_id": "a", "imdb_id": "tt0000001", "release_at": "2025-01-01T00:00:00Z"},
            {"case_id": "b", "imdb_id": "tt0000002", "release_at": "2025-01-01T00:00:00Z"},
        ]

    def test_summary_and_thresholds_use_only_ready_due_rows(self):
        self._point("tt0000001", "s1", "2025-02-01T00:00:00Z", 7.1)
        report = self.builder.build(
            self._cases(),
            as_of="2025-02-10T00:00:00Z",
            min_ready_rows=1,
            min_due_coverage=0.5,
        )
        summary = report["milestone_summary"]["rating_30d"]
        self.assertEqual(summary["due_count"], 2)
        self.assertEqual(summary["ready_count"], 1)
        self.assertEqual(summary["no_observation_count"], 1)
        self.assertEqual(summary["due_ready_ratio"], 0.5)
        self.assertTrue(summary["ready_for_research_target"])
        self.assertFalse(report["model_training_started"])

    def test_no_thresholds_never_claim_training_readiness(self):
        report = self.builder.build(self._cases(), as_of="2025-01-10T00:00:00Z")
        summary = report["milestone_summary"]["rating_early"]
        self.assertFalse(summary["readiness_evaluated"])
        self.assertIsNone(summary["ready_for_research_target"])

    def test_export_contains_only_usable_rows_and_is_deterministic(self):
        self._point("tt0000001", "s1", "2025-02-01T00:00:00Z", 7.1)
        report = self.builder.build(self._cases(), as_of="2025-02-10T00:00:00Z")
        first = self.builder.export_target_rows(report, "rating_30d")
        second = self.builder.export_target_rows(report, "rating_30d")
        self.assertEqual(first["row_count"], 1)
        self.assertEqual(first["rows"][0]["imdb_id"], "tt0000001")
        self.assertEqual(
            first["target_dataset_fingerprint_sha256"],
            second["target_dataset_fingerprint_sha256"],
        )
        self.assertFalse(first["model_training_started"])

    def test_not_due_cases_do_not_hurt_due_coverage(self):
        cases = [
            {"imdb_id": "tt0000001", "release_at": "2025-01-01T00:00:00Z"},
            {"imdb_id": "tt0000002", "release_at": "2025-02-01T00:00:00Z"},
        ]
        self._point("tt0000001", "s1", "2025-02-01T00:00:00Z", 7.1)
        report = self.builder.build(cases, as_of="2025-02-05T00:00:00Z")
        summary = report["milestone_summary"]["rating_30d"]
        self.assertEqual(summary["due_count"], 1)
        self.assertEqual(summary["ready_count"], 1)
        self.assertEqual(summary["not_due_count"], 1)
        self.assertEqual(summary["due_ready_ratio"], 1.0)

    def test_duplicate_imdb_id_is_rejected(self):
        cases = [
            {"case_id": "a", "imdb_id": "tt0000001", "release_at": "2025-01-01T00:00:00Z"},
            {"case_id": "b", "imdb_id": "tt0000001", "release_at": "2025-01-02T00:00:00Z"},
        ]
        with self.assertRaises(RatingHistoryError):
            self.builder.build(cases, as_of="2025-02-01T00:00:00Z")

    def test_thresholds_fail_when_no_milestone_is_due(self):
        report = self.builder.build(
            self._cases(),
            as_of="2025-01-10T00:00:00Z",
            min_ready_rows=1,
            min_due_coverage=0.5,
        )
        summary = report["milestone_summary"]["rating_30d"]
        self.assertEqual(summary["due_count"], 0)
        self.assertFalse(summary["ready_for_research_target"])


if __name__ == "__main__":
    unittest.main()
