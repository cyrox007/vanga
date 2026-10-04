from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from src.rating_history import RatingHistoryStore
from src.rating_milestones import RatingMilestoneExtractor


class RatingMilestoneExtractorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = RatingHistoryStore(Path(self.tmp.name) / "history.duckdb")
        self.extractor = RatingMilestoneExtractor(self.store)

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def _point(self, snapshot_id: str, observed_at: str, rating: float, votes: int):
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
            [snapshot_id, "tt0000001", rating, votes],
        )

    def test_extracts_first_post_target_point_for_each_milestone(self):
        self._point("s-early", "2025-01-02T00:00:00Z", 7.0, 100)
        self._point("s-30", "2025-02-01T00:00:00Z", 7.2, 200)
        self._point("s-180", "2025-07-02T00:00:00Z", 7.4, 500)
        self._point("s-long", "2026-01-05T00:00:00Z", 7.5, 1000)

        result = self.extractor.extract(
            "tt0000001",
            "2025-01-01T00:00:00Z",
            as_of="2026-02-01T00:00:00Z",
        )
        milestones = result["milestones"]
        self.assertTrue(milestones["rating_early"]["ready"])
        self.assertEqual(milestones["rating_early"]["average_rating"], 7.0)
        self.assertEqual(milestones["rating_30d"]["average_rating"], 7.2)
        self.assertEqual(milestones["rating_180d"]["average_rating"], 7.4)
        self.assertEqual(milestones["rating_long_term"]["average_rating"], 7.5)
        self.assertEqual(result["rating_deltas"]["early_to_30d"], 0.2)
        self.assertEqual(result["rating_deltas"]["early_to_long_term"], 0.5)
        self.assertTrue(result["readiness"]["all_milestones_ready"])
        self.assertFalse(result["pre_release_feature"])

    def test_pre_target_point_is_never_used_for_30d(self):
        self._point("s-before", "2025-01-20T00:00:00Z", 9.9, 999)
        self._point("s-after", "2025-02-02T00:00:00Z", 7.1, 120)
        result = self.extractor.extract(
            "tt0000001",
            "2025-01-01T00:00:00Z",
            as_of="2025-02-10T00:00:00Z",
        )
        self.assertEqual(result["milestones"]["rating_30d"]["snapshot_id"], "s-after")
        self.assertEqual(result["milestones"]["rating_30d"]["average_rating"], 7.1)

    def test_not_due_yet_is_distinct_from_missing(self):
        self._point("s-early", "2025-01-02T00:00:00Z", 7.0, 100)
        result = self.extractor.extract(
            "tt0000001",
            "2025-01-01T00:00:00Z",
            as_of="2025-01-15T00:00:00Z",
        )
        self.assertTrue(result["milestones"]["rating_early"]["ready"])
        self.assertEqual(
            result["milestones"]["rating_30d"]["missing_reason"],
            "not_due_yet",
        )
        self.assertFalse(result["milestones"]["rating_30d"]["due"])
        self.assertEqual(result["readiness"]["due_count"], 1)
        self.assertEqual(result["readiness"]["ready_count"], 1)

    def test_due_without_point_is_explicit_missing(self):
        result = self.extractor.extract(
            "tt0000001",
            "2025-01-01T00:00:00Z",
            as_of="2025-02-10T00:00:00Z",
        )
        self.assertEqual(
            result["milestones"]["rating_early"]["missing_reason"],
            "no_observation_after_target",
        )
        self.assertEqual(
            result["milestones"]["rating_30d"]["missing_reason"],
            "no_observation_after_target",
        )

    def test_too_late_observation_is_not_usable_target(self):
        # 30d target = 2025-01-31; наблюдение 20 февраля выходит за 7-дневный лаг.
        self._point("s-late", "2025-02-20T00:00:00Z", 7.3, 150)
        result = self.extractor.extract(
            "tt0000001",
            "2025-01-01T00:00:00Z",
            as_of="2025-03-01T00:00:00Z",
        )
        item = result["milestones"]["rating_30d"]
        self.assertFalse(item["ready"])
        self.assertFalse(item["usable_as_target"])
        self.assertEqual(item["missing_reason"], "observation_too_late")
        self.assertIsNone(item["average_rating"])
        self.assertEqual(item["candidate_snapshot_id"], "s-late")

    def test_custom_policy_is_supported_without_changing_default_contract(self):
        self._point("s-custom", "2025-01-11T00:00:00Z", 6.8, 80)
        result = self.extractor.extract(
            "tt0000001",
            "2025-01-01T00:00:00Z",
            as_of="2025-01-20T00:00:00Z",
            policy={"rating_10d": {"offset_days": 10, "max_lag_days": 1}},
        )
        self.assertTrue(result["milestones"]["rating_10d"]["ready"])
        self.assertEqual(result["readiness"]["milestone_count"], 1)


if __name__ == "__main__":
    unittest.main()
