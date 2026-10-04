from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from src.future_release_import import FutureReleaseBatchImporter
from src.future_releases import FutureReleaseStore
from src.wikidata_future_enrichment import (
    WikidataFutureEnricher,
    WikidataFutureEnrichmentError,
)


UTC = timezone.utc


def _b(value: str) -> dict[str, str]:
    return {"type": "literal", "value": value}


def _u(value: str) -> dict[str, str]:
    return {"type": "uri", "value": value}


def _row(
    relation: str,
    target_qid: str,
    label: str,
    statement: str,
    *,
    film_qid: str = "Q100",
    film_imdb: str = "tt0000100",
    target_imdb: str | None = None,
    ordinal: str | None = None,
) -> dict:
    row = {
        "film": _u(f"http://www.wikidata.org/entity/{film_qid}"),
        "filmImdb": _b(film_imdb),
        "relation": _b(relation),
        "statement": _u(
            f"http://www.wikidata.org/entity/statement/{statement}"
        ),
        "target": _u(f"http://www.wikidata.org/entity/{target_qid}"),
        "targetLabel": _b(label),
    }
    if target_imdb is not None:
        row["targetImdb"] = _b(target_imdb)
    if ordinal is not None:
        row["ordinal"] = _b(ordinal)
    return row


def _raw() -> dict:
    return {
        "head": {"vars": []},
        "results": {
            "bindings": [
                _row("director", "Q1", "Director One", "Q100-D1", target_imdb="nm0000001"),
                _row("writer", "Q2", "Writer One", "Q100-W1", target_imdb="nm0000002"),
                _row("actor", "Q3", "Actor Two", "Q100-A2", target_imdb="nm0000003", ordinal="2"),
                _row("actor", "Q4", "Actor One", "Q100-A1A", target_imdb="nm0000004", ordinal="1"),
                # Дубликат того же logical actor с конфликтующим ordinal не должен
                # породить второго актёра в snapshot.
                _row("actor", "Q4", "Actor One", "Q100-A1B", target_imdb="nm0000004", ordinal="3"),
                _row("based_on", "Q10", "Source Novel", "Q100-SRC"),
                _row("franchise", "Q20", "Saga", "Q100-FR"),
                _row("production_company", "Q30", "Studio A", "Q100-PC"),
                # Binding для фильма вне выбранного registry batch обязан быть
                # проигнорирован, а не неявно создать новый project.
                _row("director", "Q9991", "Unknown Film Director", "Q999-D", film_qid="Q999"),
            ]
        },
    }


class WikidataFutureEnrichmentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.db = root / "future.duckdb"
        self.cache = root / "cache"
        with FutureReleaseStore(self.db) as store:
            store.upsert_project(
                {
                    "project_id": "wikidata:Q100",
                    "imdb_id": "tt0000100",
                    "wikidata_id": "Q100",
                    "canonical_title": "Future Film",
                }
            )
        self.project = {
            "project_id": "wikidata:Q100",
            "imdb_id": "tt0000100",
            "wikidata_id": "Q100",
            "canonical_title": "Future Film",
        }
        self.retrieved = datetime(2026, 10, 4, 8, 30, tzinfo=UTC)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_query_is_enrichment_only_and_excludes_deprecated_statements(self) -> None:
        query = WikidataFutureEnricher.build_query(["Q100", "Q200", "Q100"])
        self.assertIn("VALUES ?film { wd:Q100 wd:Q200 }", query)
        self.assertIn("p:P57", query)
        self.assertIn("p:P58", query)
        self.assertIn("p:P161", query)
        self.assertIn("p:P144", query)
        self.assertIn("p:P179", query)
        self.assertIn("p:P272", query)
        self.assertIn("wikibase:DeprecatedRank", query)
        self.assertNotIn("p:P577", query)
        with self.assertRaises(WikidataFutureEnrichmentError):
            WikidataFutureEnricher.build_query(["not-a-qid"])

    def test_normalize_deduplicates_team_and_maps_entities(self) -> None:
        bundle, warnings = WikidataFutureEnricher.normalize(
            _raw(),
            projects=[self.project],
            retrieved_at=self.retrieved,
        )
        self.assertEqual(len(bundle["people"]), 4)
        self.assertEqual(len(bundle["project_people"]), 4)
        self.assertEqual(len(bundle["entities"]), 3)
        self.assertEqual(len(bundle["project_entities"]), 3)
        self.assertEqual(len(bundle["sources"]), 7)
        reasons = {item["reason"] for item in warnings}
        self.assertIn("actor_billing_order_conflict", reasons)
        self.assertIn("unknown_project_binding", reasons)

        actors = [
            item
            for item in bundle["project_people"]
            if item["role"] == "actor"
        ]
        by_person = {item["person_id"]: item for item in actors}
        self.assertEqual(by_person["wikidata:Q4"]["billing_order"], 1)
        self.assertEqual(by_person["wikidata:Q3"]["billing_order"], 2)

        kinds = {item["kind"] for item in bundle["entities"]}
        self.assertEqual(kinds, {"source_work", "franchise", "production_company"})
        relations = {item["relation_type"] for item in bundle["project_entities"]}
        self.assertEqual(relations, {"based_on", "part_of_franchise", "produced_by"})
        self.assertTrue(
            all(item["known_at"] == self.retrieved.isoformat() for item in bundle["project_people"])
        )

    def _import(self, *, retrieved: datetime, fingerprint_char: str) -> dict:
        bundle, _ = WikidataFutureEnricher.normalize(
            _raw(),
            projects=[self.project],
            retrieved_at=retrieved,
        )
        batch = {
            "version": 1,
            "batch_id": f"enrich-{retrieved.strftime('%Y%m%dT%H%M%SZ')}",
            "provider": "wikidata-wdqs-enrichment",
            "retrieved_at": retrieved.isoformat(),
            "cursor": "test",
            "source_fingerprint_sha256": fingerprint_char * 64,
            "bundle": bundle,
        }
        with FutureReleaseBatchImporter(self.db) as importer:
            return importer.import_batch(batch)

    def test_imported_facts_are_hidden_before_retrieval_and_visible_after(self) -> None:
        result = self._import(retrieved=self.retrieved, fingerprint_char="a")
        self.assertFalse(result["idempotent"])
        with FutureReleaseStore(self.db) as store:
            early = store.snapshot_as_of(
                "wikidata:Q100",
                datetime(2026, 10, 4, 8, 29, tzinfo=UTC),
            )
            late = store.snapshot_as_of(
                "wikidata:Q100",
                datetime(2026, 10, 4, 8, 31, tzinfo=UTC),
            )
        self.assertEqual(early["directors"], [])
        self.assertEqual(early["writers"], [])
        self.assertEqual(early["cast"], [])
        self.assertEqual(early["entities"], [])
        self.assertEqual([item["canonical_name"] for item in late["directors"]], ["Director One"])
        self.assertEqual([item["canonical_name"] for item in late["writers"]], ["Writer One"])
        self.assertEqual([item["canonical_name"] for item in late["cast"]], ["Actor One", "Actor Two"])
        self.assertEqual(len(late["entities"]), 3)

    def test_second_sync_adds_provenance_without_duplicate_logical_people(self) -> None:
        self._import(retrieved=self.retrieved, fingerprint_char="a")
        second = datetime(2026, 10, 5, 8, 30, tzinfo=UTC)
        self._import(retrieved=second, fingerprint_char="b")
        with FutureReleaseStore(self.db) as store:
            snapshot = store.snapshot_as_of(
                "wikidata:Q100",
                datetime(2026, 10, 5, 9, 0, tzinfo=UTC),
            )
        self.assertEqual(len(snapshot["directors"]), 1)
        self.assertEqual(len(snapshot["writers"]), 1)
        self.assertEqual(len(snapshot["cast"]), 2)
        director = snapshot["directors"][0]
        self.assertEqual(len(director["source_ids"]), 2)

    def test_registry_selection_requires_existing_qid(self) -> None:
        with FutureReleaseStore(self.db) as store:
            store.upsert_project(
                {
                    "project_id": "manual:no-qid",
                    "canonical_title": "Manual Future Film",
                }
            )
        projects, warnings = WikidataFutureEnricher.registry_projects(
            self.db,
            project_ids=["wikidata:Q100", "manual:no-qid", "missing"],
        )
        self.assertEqual([item["project_id"] for item in projects], ["wikidata:Q100"])
        warning_pairs = {(item.get("project_id"), item["reason"]) for item in warnings}
        self.assertIn(("manual:no-qid", "wikidata_id_missing"), warning_pairs)
        self.assertIn(("missing", "project_not_found"), warning_pairs)

    def test_collect_caches_raw_and_emits_importer_compatible_batch(self) -> None:
        seen_queries: list[str] = []

        def transport(query: str) -> dict:
            seen_queries.append(query)
            return _raw()

        enricher = WikidataFutureEnricher(
            cache_dir=self.cache,
            transport=transport,
        )
        result = enricher.collect_from_registry(
            self.db,
            project_ids=["wikidata:Q100"],
            retrieved_at=self.retrieved,
        )
        self.assertEqual(len(seen_queries), 1)
        self.assertIn("wd:Q100", seen_queries[0])
        self.assertTrue(Path(result["raw_cache_path"]).exists())
        self.assertEqual(result["people_count"], 4)
        self.assertEqual(result["entity_count"], 3)
        self.assertFalse(result["network_required_for_inference"])

        with FutureReleaseBatchImporter(self.db) as importer:
            imported = importer.import_batch(result["batch"])
        self.assertEqual(imported["provider"], "wikidata-wdqs-enrichment")
        self.assertFalse(imported["idempotent"])

    def test_project_imdb_mismatch_is_warning_not_identity_mutation(self) -> None:
        raw = {
            "results": {
                "bindings": [
                    _row(
                        "director",
                        "Q1",
                        "Director One",
                        "Q100-D1",
                        film_imdb="tt9999999",
                        target_imdb="nm0000001",
                    )
                ]
            }
        }
        bundle, warnings = WikidataFutureEnricher.normalize(
            raw,
            projects=[self.project],
            retrieved_at=self.retrieved,
        )
        self.assertEqual(bundle["projects"], [])
        self.assertIn(
            "project_imdb_id_mismatch",
            {item["reason"] for item in warnings},
        )


if __name__ == "__main__":
    unittest.main()
