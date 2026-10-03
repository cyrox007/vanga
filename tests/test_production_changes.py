from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from src.production_changes import ProductionChangeContext
from src.production_context import ProductionContextStore


class ProductionChangeContextTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "production_context.duckdb"
        self.store = ProductionContextStore(self.path)
        self.changes = ProductionChangeContext(self.store)

        for source_id, date in (
            ("src-old", "2024-01-01T00:00:00Z"),
            ("src-mid", "2024-06-01T00:00:00Z"),
            ("src-late", "2026-01-01T00:00:00Z"),
        ):
            self.store.upsert_source(
                {
                    "source_id": source_id,
                    "url": f"https://example.test/{source_id}",
                    "published_at": date,
                    "retrieved_at": date,
                    "confidence": 0.95,
                }
            )
        self.store.upsert_project(
            {
                "project_id": "target",
                "title": "Target",
                "release_at": "2025-07-01T00:00:00Z",
                "identity_known_at": "2024-01-01T00:00:00Z",
            }
        )

        self._event(
            "director-change",
            "director_change",
            "production",
            "2024-02-01T00:00:00Z",
            "src-old",
            event_at="2024-01-20T00:00:00Z",
        )
        self._event(
            "writer-change",
            "writer_change",
            "writing",
            "2024-02-10T00:00:00Z",
            "src-old",
            event_at="2024-02-01T00:00:00Z",
        )
        self._event(
            "rewrite",
            "rewrite",
            "writing",
            "2024-03-01T00:00:00Z",
            "src-old",
        )
        self._event(
            "additional-photo",
            "reshoot",
            "post_production",
            "2024-04-01T00:00:00Z",
            "src-old",
            details={"activity": "additional_photography"},
        )
        self._event(
            "recut",
            "recut",
            "post_production",
            "2024-04-15T00:00:00Z",
            "src-old",
        )
        self._event(
            "delay-1",
            "release_date_change",
            "release",
            "2024-05-01T00:00:00Z",
            "src-old",
            details={
                "old_release_at": "2025-05-01T00:00:00Z",
                "new_release_at": "2025-07-01T00:00:00Z",
            },
        )
        self._event(
            "advance-1",
            "release_date_change",
            "release",
            "2024-05-15T00:00:00Z",
            "src-mid",
            details={
                "old_release_at": "2025-08-01T00:00:00Z",
                "new_release_at": "2025-07-01T00:00:00Z",
            },
        )
        # Generic release change без обеих дат остаётся raw event, но не shift.
        self._event(
            "release-unknown-shift",
            "release_date_change",
            "release",
            "2024-05-20T00:00:00Z",
            "src-mid",
            details={"note": "date changed"},
        )
        # Поздний post-cutoff reshoot не должен быть виден раннему snapshot.
        self._event(
            "late-reshoot",
            "reshoot",
            "post_production",
            "2026-01-01T00:00:00Z",
            "src-late",
            details={"activity": "reshoot"},
        )

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def _event(
        self,
        event_id,
        event_type,
        stage,
        known_at,
        source_id,
        *,
        event_at=None,
        details=None,
    ):
        self.store.add_event(
            {
                "event_id": event_id,
                "project_id": "target",
                "event_type": event_type,
                "event_at": event_at,
                "known_at": known_at,
                "stage": stage,
                "source_id": source_id,
                "details": details or {},
            }
        )

    def test_team_change_and_rework_are_transparent_aggregates(self):
        f = self.changes.features_as_of("target", "2024-06-15T00:00:00Z")
        self.assertEqual(f["production_team_change_count"], 2.0)
        self.assertEqual(f["production_rework_count"], 3.0)
        self.assertEqual(f["production_additional_photography_count"], 1.0)

    def test_release_delay_and_advance_are_derived_from_structured_dates(self):
        f = self.changes.features_as_of("target", "2024-06-15T00:00:00Z")
        self.assertEqual(f["production_release_delay_count"], 1.0)
        self.assertEqual(f["production_release_advance_count"], 1.0)
        self.assertEqual(f["production_release_delay_days_total"], 61.0)
        self.assertEqual(f["production_release_delay_days_max"], 61.0)
        self.assertEqual(f["production_release_advance_days_total"], 31.0)
        self.assertAlmostEqual(f["production_release_shift_known_ratio"], 2 / 3)

    def test_stage_counts_and_event_date_coverage_are_explicit(self):
        f = self.changes.features_as_of("target", "2024-06-15T00:00:00Z")
        self.assertEqual(f["production_change_stage_writing_count"], 2.0)
        self.assertEqual(f["production_change_stage_post_production_count"], 2.0)
        self.assertEqual(f["production_change_stage_release_count"], 3.0)
        self.assertAlmostEqual(f["production_change_event_date_known_ratio"], 2 / 8)

    def test_late_event_is_hidden_until_known_at(self):
        early = self.changes.features_as_of("target", "2024-06-15T00:00:00Z")
        late = self.changes.features_as_of("target", "2026-02-01T00:00:00Z")
        self.assertEqual(early["production_rework_count"], 3.0)
        self.assertEqual(late["production_rework_count"], 4.0)

    def test_changes_as_of_exposes_derived_metadata_without_quality_sign(self):
        events = self.changes.changes_as_of("target", "2024-06-15T00:00:00Z")
        by_id = {event["event_id"]: event for event in events}
        self.assertEqual(by_id["delay-1"]["release_shift_days"], 61.0)
        self.assertTrue(by_id["additional-photo"]["additional_photography"])
        self.assertNotIn("quality", by_id["delay-1"])


if __name__ == "__main__":
    unittest.main()
