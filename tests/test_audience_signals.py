from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from src.audience_signals import AudienceSignalError, AudienceSignalStore


class AudienceSignalStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = AudienceSignalStore(Path(self.tmp.name) / "audience.duckdb")
        self.store.upsert_source(
            {
                "source_id": "yt-public",
                "provider": "YouTube public aggregate",
                "url": "https://example.test/trailer",
                "usage_basis": "public_aggregate",
                "retrieved_at": "2026-06-01T12:00:00Z",
            }
        )
        self.store.upsert_project(
            {
                "project_id": "film-1",
                "imdb_id": "tt0000001",
                "title": "Future Film",
            }
        )

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def _observe(self, **overrides):
        payload = {
            "observation_id": "obs-1",
            "project_id": "film-1",
            "signal_type": "trailer_views",
            "value": 100000,
            "unit": "count",
            "observed_at": "2026-06-01T10:00:00Z",
            "known_at": "2026-06-01T12:00:00Z",
            "planned_release_at": "2026-08-01T00:00:00Z",
            "method": "youtube_public_stats",
            "method_version": "1",
            "source_id": "yt-public",
            "sample_size": 1,
            "coverage_fraction": 1.0,
        }
        payload.update(overrides)
        return self.store.add_observation(payload)

    def test_pre_release_observation_is_visible_only_after_known_at(self):
        self._observe()
        before = self.store.observations_as_of(
            "film-1",
            "2026-06-01T11:00:00Z",
            release_at="2026-08-01T00:00:00Z",
        )
        after = self.store.observations_as_of(
            "film-1",
            "2026-06-02T00:00:00Z",
            release_at="2026-08-01T00:00:00Z",
        )
        self.assertEqual(before, [])
        self.assertEqual(len(after), 1)
        self.assertEqual(after[0]["value"], 100000.0)

    def test_post_release_or_late_known_observation_is_rejected(self):
        with self.assertRaises(AudienceSignalError):
            self._observe(
                observation_id="post",
                observed_at="2026-08-02T00:00:00Z",
                known_at="2026-08-02T00:00:00Z",
            )
        with self.assertRaises(AudienceSignalError):
            self._observe(
                observation_id="late-known",
                observed_at="2026-07-20T00:00:00Z",
                known_at="2026-08-02T00:00:00Z",
            )

    def test_features_require_exact_method_version_and_unit(self):
        self._observe()
        common = {
            "project_id": "film-1",
            "cutoff": "2026-06-02T00:00:00Z",
            "release_at": "2026-08-01T00:00:00Z",
        }
        exact = self.store.features_as_of(
            **common,
            protocols=[
                {
                    "signal_type": "trailer_views",
                    "method": "youtube_public_stats",
                    "method_version": "1",
                    "unit": "count",
                    "feature_name": "audience_trailer_views",
                }
            ],
        )
        wrong_version = self.store.features_as_of(
            **common,
            protocols=[
                {
                    "signal_type": "trailer_views",
                    "method": "youtube_public_stats",
                    "method_version": "2",
                    "unit": "count",
                    "feature_name": "audience_trailer_views",
                }
            ],
        )
        self.assertEqual(exact["audience_trailer_views"], 100000.0)
        self.assertEqual(exact["audience_trailer_views_known"], 1.0)
        self.assertEqual(wrong_version["audience_trailer_views_known"], 0.0)
        self.assertEqual(wrong_version["audience_trailer_views"], 0.0)

    def test_latest_visible_point_wins_within_same_protocol(self):
        self._observe()
        self._observe(
            observation_id="obs-2",
            value=150000,
            observed_at="2026-06-03T10:00:00Z",
            known_at="2026-06-03T12:00:00Z",
        )
        features = self.store.features_as_of(
            "film-1",
            "2026-06-04T00:00:00Z",
            release_at="2026-08-01T00:00:00Z",
            protocols=[
                {
                    "signal_type": "trailer_views",
                    "method": "youtube_public_stats",
                    "method_version": "1",
                    "unit": "count",
                }
            ],
        )
        self.assertEqual(features["audience_trailer_views"], 150000.0)
        self.assertEqual(features["audience_signal_known_ratio"], 1.0)

    def test_cutoff_after_release_is_rejected_fail_closed(self):
        self._observe(
            observed_at="2026-06-20T10:00:00Z",
            known_at="2026-06-20T12:00:00Z",
        )
        with self.assertRaises(AudienceSignalError):
            self.store.observations_as_of(
                "film-1",
                "2026-06-21T00:00:00Z",
                release_at="2026-06-15T00:00:00Z",
            )

    def test_registry_contains_only_aggregate_numeric_payload(self):
        self._observe()
        columns = {
            row[1]
            for row in self.store.conn.execute(
                "PRAGMA table_info('audience_signal_observations')"
            ).fetchall()
        }
        for forbidden in {"raw_text", "user_id", "username", "demographics"}:
            self.assertNotIn(forbidden, columns)


if __name__ == "__main__":
    unittest.main()
