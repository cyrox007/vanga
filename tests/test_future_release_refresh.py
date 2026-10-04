from __future__ import annotations

import hashlib
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from src.future_release_refresh import (
    FutureReleaseRefreshError,
    FutureReleaseRefreshPipeline,
)
from src.future_releases import FutureReleaseStore


UTC = timezone.utc


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class FakeDiscoveryCollector:
    def __init__(self, project_count: int = 2) -> None:
        self.project_count = project_count
        self.calls: list[dict] = []

    def collect(self, from_at, to_at, *, limit, retrieved_at):
        self.calls.append(
            {
                "from_at": from_at,
                "to_at": to_at,
                "limit": limit,
                "retrieved_at": retrieved_at,
            }
        )
        projects = []
        releases = []
        sources = []
        for index in range(self.project_count):
            project_id = f"wikidata:Q{100 + index}"
            source_id = f"discovery-source-{index}"
            projects.append(
                {
                    "project_id": project_id,
                    "imdb_id": f"tt{100 + index:07d}",
                    "wikidata_id": f"Q{100 + index}",
                    "canonical_title": f"Future Film {index}",
                }
            )
            sources.append(
                {
                    "source_id": source_id,
                    "provider": "Fake Discovery",
                    "url": f"https://example.test/{index}",
                    "usage_basis": "public_record",
                    "retrieved_at": retrieved_at.isoformat(),
                }
            )
            releases.append(
                {
                    "observation_id": f"release-{index}-{retrieved_at.strftime('%Y%m%dT%H%M%SZ')}",
                    "project_id": project_id,
                    "territory": "worldwide",
                    "release_start_at": "2027-01-01T00:00:00+00:00",
                    "release_end_at": "2027-01-01T00:00:00+00:00",
                    "precision": "exact",
                    "known_at": retrieved_at.isoformat(),
                    "source_id": source_id,
                    "confidence": 0.9,
                }
            )
        bundle = {
            "sources": sources,
            "projects": projects,
            "people": [],
            "entities": [],
            "aliases": [],
            "release_windows": releases,
            "statuses": [],
            "project_people": [],
            "project_entities": [],
        }
        fp = _sha(f"discovery:{self.project_count}:{retrieved_at.isoformat()}")
        return {
            "collector": "fake-discovery",
            "collector_version": 1,
            "raw_fingerprint_sha256": _sha("raw:" + fp),
            "raw_cache_path": "/tmp/fake-discovery.json",
            "warning_count": 0,
            "warnings": [],
            "batch": {
                "version": 1,
                "batch_id": f"fake-discovery-{fp[:12]}",
                "provider": "fake-discovery",
                "retrieved_at": retrieved_at.isoformat(),
                "cursor": "test",
                "source_fingerprint_sha256": fp,
                "bundle": bundle,
            },
        }


class FakeEnrichmentCollector:
    def __init__(self, *, fail_on_call: int | None = None) -> None:
        self.fail_on_call = fail_on_call
        self.calls: list[list[str]] = []

    def collect_from_registry(
        self,
        future_db_path,
        *,
        project_ids,
        limit,
        retrieved_at,
    ):
        current_call = len(self.calls) + 1
        chunk = list(project_ids)
        self.calls.append(chunk)
        if self.fail_on_call == current_call:
            raise RuntimeError("тестовый сбой enrichment")
        if limit != len(chunk):
            raise AssertionError("pipeline должен передавать limit размера chunk")

        sources = []
        people = []
        project_people = []
        for index, project_id in enumerate(chunk):
            qid = project_id.split(":", 1)[-1]
            person_id = f"wikidata:QD{qid[1:]}"
            source_id = f"enrich-source-{current_call}-{index}"
            sources.append(
                {
                    "source_id": source_id,
                    "provider": "Fake Enrichment",
                    "url": f"https://example.test/enrich/{qid}",
                    "usage_basis": "public_record",
                    "retrieved_at": retrieved_at.isoformat(),
                }
            )
            people.append(
                {
                    "person_id": person_id,
                    "wikidata_id": f"Q{9000 + current_call * 100 + index}",
                    "canonical_name": f"Director {project_id}",
                }
            )
            project_people.append(
                {
                    "link_id": f"director-link-{current_call}-{index}-{retrieved_at.strftime('%Y%m%dT%H%M%SZ')}",
                    "project_id": project_id,
                    "person_id": person_id,
                    "role": "director",
                    "billing_order": None,
                    "known_at": retrieved_at.isoformat(),
                    "source_id": source_id,
                    "confidence": 0.9,
                }
            )
        bundle = {
            "sources": sources,
            "projects": [],
            "people": people,
            "entities": [],
            "aliases": [],
            "release_windows": [],
            "statuses": [],
            "project_people": project_people,
            "project_entities": [],
        }
        fp = _sha(
            "enrichment:"
            + ",".join(chunk)
            + ":"
            + retrieved_at.isoformat()
        )
        return {
            "collector": "fake-enrichment",
            "collector_version": 1,
            "raw_fingerprint_sha256": _sha("raw:" + fp),
            "raw_cache_path": f"/tmp/fake-enrich-{current_call}.json",
            "warning_count": 0,
            "warnings": [],
            "batch": {
                "version": 1,
                "batch_id": f"fake-enrich-{fp[:12]}",
                "provider": "fake-enrichment",
                "retrieved_at": retrieved_at.isoformat(),
                "cursor": ",".join(chunk),
                "source_fingerprint_sha256": fp,
                "bundle": bundle,
            },
        }


class FutureReleaseRefreshPipelineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.db = Path(self.temp.name) / "future.duckdb"
        self.retrieved = datetime(2026, 10, 4, 9, 0, tzinfo=UTC)
        self.from_at = datetime(2026, 10, 4, 0, 0, tzinfo=UTC)
        self.to_at = datetime(2028, 1, 1, 0, 0, tzinfo=UTC)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _pipeline(self, project_count=2, *, fail_on_call=None):
        discovery = FakeDiscoveryCollector(project_count)
        enrichment = FakeEnrichmentCollector(fail_on_call=fail_on_call)
        pipeline = FutureReleaseRefreshPipeline(
            self.db,
            discovery_collector=discovery,
            enrichment_collector=enrichment,
        )
        return pipeline, discovery, enrichment

    def test_full_refresh_imports_discovery_and_enrichment(self) -> None:
        pipeline, discovery, enrichment = self._pipeline(2)
        report = pipeline.run(
            self.from_at,
            self.to_at,
            retrieved_at=self.retrieved,
            enrichment_chunk_size=100,
        )
        self.assertTrue(report["refresh_complete"])
        self.assertIsNone(report["failed_stage"])
        self.assertEqual(report["project_ids"], ["wikidata:Q100", "wikidata:Q101"])
        self.assertEqual(report["enriched_project_count"], 2)
        self.assertEqual(report["enrichment_batch_count"], 1)
        self.assertFalse(report["network_required_for_inference"])
        self.assertEqual(len(discovery.calls), 1)
        self.assertEqual(enrichment.calls, [["wikidata:Q100", "wikidata:Q101"]])
        self.assertEqual(len(report["refresh_fingerprint_sha256"]), 64)

        with FutureReleaseStore(self.db) as store:
            snapshot = store.snapshot_as_of(
                "wikidata:Q100",
                datetime(2026, 10, 4, 9, 1, tzinfo=UTC),
            )
        self.assertEqual(snapshot["release_at"], "2027-01-01T00:00:00+00:00")
        self.assertEqual(len(snapshot["directors"]), 1)
        self.assertEqual(
            snapshot["directors"][0]["canonical_name"],
            "Director wikidata:Q100",
        )

    def test_only_projects_from_current_discovery_batch_are_enriched(self) -> None:
        with FutureReleaseStore(self.db) as store:
            store.upsert_project(
                {
                    "project_id": "wikidata:Q999",
                    "wikidata_id": "Q999",
                    "canonical_title": "Old Registry Project",
                }
            )
        pipeline, _, enrichment = self._pipeline(2)
        report = pipeline.run(
            self.from_at,
            self.to_at,
            retrieved_at=self.retrieved,
        )
        self.assertTrue(report["refresh_complete"])
        flattened = [item for chunk in enrichment.calls for item in chunk]
        self.assertNotIn("wikidata:Q999", flattened)

    def test_enrichment_is_chunked(self) -> None:
        pipeline, _, enrichment = self._pipeline(5)
        report = pipeline.run(
            self.from_at,
            self.to_at,
            retrieved_at=self.retrieved,
            enrichment_chunk_size=2,
        )
        self.assertEqual(
            enrichment.calls,
            [
                ["wikidata:Q100", "wikidata:Q101"],
                ["wikidata:Q102", "wikidata:Q103"],
                ["wikidata:Q104"],
            ],
        )
        self.assertEqual(report["enrichment_batch_count"], 3)
        self.assertEqual(len(report["enrichment_imports"]), 3)

    def test_empty_discovery_is_successful_and_skips_enrichment(self) -> None:
        pipeline, _, enrichment = self._pipeline(0)
        report = pipeline.run(
            self.from_at,
            self.to_at,
            retrieved_at=self.retrieved,
        )
        self.assertTrue(report["refresh_complete"])
        self.assertEqual(report["project_ids"], [])
        self.assertEqual(
            report["enrichment_skipped_reason"],
            "discovery_batch_has_no_projects",
        )
        self.assertEqual(enrichment.calls, [])

    def test_enrichment_failure_keeps_discovery_and_returns_partial_report(self) -> None:
        pipeline, _, enrichment = self._pipeline(3, fail_on_call=2)
        with self.assertRaises(FutureReleaseRefreshError) as caught:
            pipeline.run(
                self.from_at,
                self.to_at,
                retrieved_at=self.retrieved,
                enrichment_chunk_size=2,
            )
        report = caught.exception.report
        self.assertFalse(report["refresh_complete"])
        self.assertEqual(report["failed_stage"], "enrichment_collect")
        self.assertEqual(report["failed_enrichment_batch"], 2)
        self.assertEqual(report["failed_project_ids"], ["wikidata:Q102"])
        self.assertEqual(len(report["enrichment_imports"]), 1)
        self.assertEqual(len(enrichment.calls), 2)

        with FutureReleaseStore(self.db) as store:
            discovery_project = store.conn.execute(
                "SELECT 1 FROM future_release_projects WHERE project_id='wikidata:Q102'"
            ).fetchone()
            first_director = store.people_as_of(
                "wikidata:Q100",
                datetime(2026, 10, 4, 9, 1, tzinfo=UTC),
            )
            failed_director = store.people_as_of(
                "wikidata:Q102",
                datetime(2026, 10, 4, 9, 1, tzinfo=UTC),
            )
        self.assertIsNotNone(discovery_project)
        self.assertEqual(len(first_director), 1)
        self.assertEqual(failed_director, [])

    def test_same_refresh_is_idempotent_and_fingerprint_is_stable(self) -> None:
        pipeline, _, _ = self._pipeline(2)
        first = pipeline.run(
            self.from_at,
            self.to_at,
            retrieved_at=self.retrieved,
        )
        # Новый набор fake collectors имитирует повтор того же source snapshot.
        pipeline2, _, _ = self._pipeline(2)
        second = pipeline2.run(
            self.from_at,
            self.to_at,
            retrieved_at=self.retrieved,
        )
        self.assertTrue(second["discovery_import"]["idempotent"])
        self.assertTrue(second["enrichment_imports"][0]["idempotent"])
        self.assertEqual(
            first["refresh_fingerprint_sha256"],
            second["refresh_fingerprint_sha256"],
        )

    def test_chunk_size_is_capped_at_enricher_limit(self) -> None:
        pipeline, _, _ = self._pipeline(1)
        report = pipeline.run(
            self.from_at,
            self.to_at,
            retrieved_at=self.retrieved,
            enrichment_chunk_size=9999,
        )
        self.assertEqual(report["enrichment_chunk_size"], 200)


if __name__ == "__main__":
    unittest.main()
