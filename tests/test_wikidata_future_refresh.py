from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from src.future_release_import import FutureReleaseBatchImporter
from src.future_releases import FutureReleaseStore
from src.future_temporal_facts import FutureTemporalFactStore
from src.wikidata_future_refresh import WikidataFutureRefresh


UTC = timezone.utc


class _FakeCollector:
    def __init__(self, result: dict) -> None:
        self.result = result
        self.calls: list[dict] = []

    def collect_from_registry(self, future_db_path, **kwargs):
        self.calls.append({"future_db_path": future_db_path, **kwargs})
        return self.result


def _team_result(project_ids=None) -> dict:
    project_ids = project_ids or ["wikidata:Q100"]
    return {
        "project_ids": project_ids,
        "warnings": [],
        "raw_cache_path": "/tmp/team.json",
        "batch": {
            "source_fingerprint_sha256": "a" * 64,
            "bundle": {
                "sources": [
                    {
                        "source_id": "wikidata:Q100:team",
                        "provider": "Wikidata",
                        "usage_basis": "public_record",
                        "retrieved_at": "2026-10-04T10:00:00+00:00",
                    }
                ],
                "projects": [],
                "people": [
                    {
                        "person_id": "wikidata:Q1",
                        "wikidata_id": "Q1",
                        "canonical_name": "Director One",
                    }
                ],
                "entities": [],
                "aliases": [],
                "release_windows": [],
                "statuses": [],
                "project_people": [
                    {
                        "link_id": "team-link-1",
                        "project_id": "wikidata:Q100",
                        "person_id": "wikidata:Q1",
                        "role": "director",
                        "known_at": "2026-10-04T10:00:00+00:00",
                        "source_id": "wikidata:Q100:team",
                        "confidence": 0.9,
                    }
                ],
                "project_entities": [],
                "temporal_facts": [],
            },
        },
    }


def _facts_result(project_ids=None) -> dict:
    project_ids = project_ids or ["wikidata:Q100"]
    return {
        "project_ids": project_ids,
        "warnings": [],
        "raw_cache_path": "/tmp/facts.json",
        "batch": {
            "source_fingerprint_sha256": "b" * 64,
            "bundle": {
                "sources": [
                    {
                        "source_id": "wikidata:Q100:facts",
                        "provider": "Wikidata",
                        "usage_basis": "public_record",
                        "retrieved_at": "2026-10-04T10:00:00+00:00",
                    }
                ],
                "projects": [],
                "people": [],
                "entities": [],
                "aliases": [],
                "release_windows": [],
                "statuses": [],
                "project_people": [],
                "project_entities": [],
                "temporal_facts": [
                    {
                        "observation_id": "runtime-1",
                        "project_id": "wikidata:Q100",
                        "fact_type": "runtime_minutes",
                        "value": 132,
                        "known_at": "2026-10-04T10:00:00+00:00",
                        "source_id": "wikidata:Q100:facts",
                        "confidence": 0.9,
                    }
                ],
            },
        },
    }


class WikidataFutureRefreshTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.db = Path(self.temp.name) / "future.duckdb"
        with FutureReleaseStore(self.db) as store:
            store.upsert_project(
                {
                    "project_id": "wikidata:Q100",
                    "wikidata_id": "Q100",
                    "canonical_title": "Future Film",
                }
            )
        self.observed = datetime(2026, 10, 4, 10, 0, tzinfo=UTC)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_merge_combines_team_and_temporal_facts(self) -> None:
        refresh = WikidataFutureRefresh(
            enricher=_FakeCollector(_team_result()),
            facts=_FakeCollector(_facts_result()),
        )
        result = refresh.collect_from_registry(
            self.db,
            project_ids=["wikidata:Q100"],
            retrieved_at=self.observed,
        )
        bundle = result["batch"]["bundle"]
        self.assertEqual(result["collector"], "wikidata-wdqs-refresh")
        self.assertEqual(result["people_count"], 1)
        self.assertEqual(result["project_people_count"], 1)
        self.assertEqual(result["temporal_fact_count"], 1)
        self.assertEqual(len(bundle["sources"]), 2)
        self.assertEqual(bundle["temporal_facts"][0]["value"], 132)

    def test_same_retrieved_at_is_forwarded_to_both_collectors(self) -> None:
        team = _FakeCollector(_team_result())
        facts = _FakeCollector(_facts_result())
        refresh = WikidataFutureRefresh(enricher=team, facts=facts)
        refresh.collect_from_registry(
            self.db,
            project_ids=["wikidata:Q100"],
            limit=25,
            retrieved_at=self.observed,
        )
        self.assertEqual(team.calls[0]["retrieved_at"], self.observed)
        self.assertEqual(facts.calls[0]["retrieved_at"], self.observed)
        self.assertEqual(team.calls[0]["limit"], 25)
        self.assertEqual(facts.calls[0]["limit"], 25)

    def test_different_project_sets_abort_atomic_refresh(self) -> None:
        refresh = WikidataFutureRefresh(
            enricher=_FakeCollector(_team_result(["wikidata:Q100"])),
            facts=_FakeCollector(_facts_result(["wikidata:Q200"])),
        )
        with self.assertRaisesRegex(ValueError, "разные project sets"):
            refresh.collect_from_registry(self.db, retrieved_at=self.observed)

    def test_conflicting_duplicate_identity_is_rejected(self) -> None:
        team = _team_result()
        facts = _facts_result()
        facts["batch"]["bundle"]["sources"][0]["source_id"] = "wikidata:Q100:team"
        facts["batch"]["bundle"]["sources"][0]["provider"] = "Other"
        refresh = WikidataFutureRefresh(
            enricher=_FakeCollector(team),
            facts=_FakeCollector(facts),
        )
        with self.assertRaisesRegex(ValueError, "Конфликт duplicate"):
            refresh.collect_from_registry(self.db, retrieved_at=self.observed)

    def test_combined_batch_import_is_atomic_and_readable_as_of(self) -> None:
        refresh = WikidataFutureRefresh(
            enricher=_FakeCollector(_team_result()),
            facts=_FakeCollector(_facts_result()),
        )
        result = refresh.collect_from_registry(self.db, retrieved_at=self.observed)
        with FutureReleaseBatchImporter(self.db) as importer:
            imported = importer.import_batch(result["batch"])
        self.assertFalse(imported["idempotent"])
        self.assertEqual(imported["counts"]["temporal_facts"], 1)
        self.assertEqual(imported["counts"]["project_people"], 1)

        with FutureReleaseStore(self.db) as store:
            snapshot = store.snapshot_as_of(
                "wikidata:Q100",
                datetime(2026, 10, 4, 10, 1, tzinfo=UTC),
            )
        self.assertEqual(
            [item["canonical_name"] for item in snapshot["directors"]],
            ["Director One"],
        )
        with FutureTemporalFactStore(self.db) as facts_store:
            temporal = facts_store.snapshot_as_of(
                "wikidata:Q100",
                datetime(2026, 10, 4, 10, 1, tzinfo=UTC),
            )
        self.assertEqual(temporal["facts"]["runtime_minutes"], 132)


if __name__ == "__main__":
    unittest.main()
