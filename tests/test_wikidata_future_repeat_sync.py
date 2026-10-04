from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.future_release_import import FutureReleaseBatchImporter
from src.wikidata_future_releases import WikidataFutureReleaseCollector


def _cell(value: str) -> dict:
    return {"type": "literal", "value": value}


def _uri(value: str) -> dict:
    return {"type": "uri", "value": value}


class WikidataFutureRepeatSyncTests(unittest.TestCase):
    def test_next_day_same_project_imports_without_alias_collision_and_keeps_old_source(self) -> None:
        raw = {
            "head": {
                "vars": [
                    "film",
                    "filmLabel",
                    "imdb",
                    "releaseStatement",
                    "releaseDate",
                    "precision",
                ]
            },
            "results": {
                "bindings": [
                    {
                        "film": _uri("http://www.wikidata.org/entity/Q100"),
                        "filmLabel": _cell("Exact Film"),
                        "imdb": _cell("tt1234567"),
                        "releaseStatement": _uri(
                            "http://www.wikidata.org/entity/statement/Q100-AAA"
                        ),
                        "releaseDate": _cell("2027-05-20T00:00:00Z"),
                        "precision": _cell("11"),
                    }
                ]
            },
        }

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            collector = WikidataFutureReleaseCollector(
                cache_dir=root / "cache",
                transport=lambda _query: raw,
            )
            first_at = datetime(2026, 10, 4, 8, 0, tzinfo=timezone.utc)
            second_at = first_at + timedelta(days=1)
            first = collector.collect(
                "2026-10-04T00:00:00Z",
                "2029-01-01T00:00:00Z",
                retrieved_at=first_at,
            )
            second = collector.collect(
                "2026-10-04T00:00:00Z",
                "2029-01-01T00:00:00Z",
                retrieved_at=second_at,
            )

            first_source = first["batch"]["bundle"]["sources"][0]
            second_source = second["batch"]["bundle"]["sources"][0]
            self.assertNotEqual(first_source["source_id"], second_source["source_id"])
            self.assertEqual(first_source["retrieved_at"], first_at.isoformat())
            self.assertEqual(second_source["retrieved_at"], second_at.isoformat())
            self.assertNotEqual(
                first["batch"]["bundle"]["aliases"][0]["alias_id"],
                second["batch"]["bundle"]["aliases"][0]["alias_id"],
            )

            db = root / "future.duckdb"
            with FutureReleaseBatchImporter(db) as importer:
                first_import = importer.import_batch(first["batch"])
                second_import = importer.import_batch(second["batch"])
                self.assertFalse(first_import["idempotent"])
                self.assertFalse(second_import["idempotent"])

                early = importer.store.snapshot_as_of("wikidata:Q100", first_at)
                late = importer.store.snapshot_as_of("wikidata:Q100", second_at)
                self.assertEqual(len(early["release_candidates"]), 1)
                self.assertEqual(len(late["release_candidates"]), 1)
                self.assertEqual(
                    early["release_candidates"][0]["evidence"][0]["source_id"],
                    first_source["source_id"],
                )
                late_sources = {
                    item["source_id"]
                    for item in late["release_candidates"][0]["evidence"]
                }
                self.assertEqual(late_sources, {first_source["source_id"], second_source["source_id"]})

                source_rows = importer.store.conn.execute(
                    "SELECT source_id, retrieved_at FROM future_release_sources ORDER BY retrieved_at"
                ).fetchall()
                self.assertEqual(len(source_rows), 2)
                self.assertEqual(str(source_rows[0][0]), first_source["source_id"])
                self.assertEqual(str(source_rows[1][0]), second_source["source_id"])


if __name__ == "__main__":
    unittest.main()
