from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

import duckdb

from src.data_freshness import (
    FreshnessPolicy,
    build_freshness_report,
    require_fresh_imdb_data,
    write_freshness_manifest,
)


AS_OF = datetime(2026, 10, 3, 12, 0, 0, tzinfo=timezone.utc)
DATASET_TIME = datetime(2026, 10, 1, 12, 0, 0, tzinfo=timezone.utc)
DB_TIME = datetime(2026, 10, 2, 12, 0, 0, tzinfo=timezone.utc)


class DataFreshnessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.data_dir = self.root / "data" / "imdb"
        self.data_dir.mkdir(parents=True)
        self.db_path = self.root / "imdb.duckdb"
        self._write_dataset_files(DATASET_TIME)
        self._write_database(years=(2024, 2025, 2026))
        os.utime(self.db_path, (DB_TIME.timestamp(), DB_TIME.timestamp()))

    def tearDown(self):
        self.temp.cleanup()

    def _write_dataset_files(self, source_time: datetime) -> None:
        http_date = source_time.strftime("%a, %d %b %Y %H:%M:%S GMT")
        for name in (
            "title.basics",
            "title.ratings",
            "title.principals",
            "title.crew",
            "name.basics",
        ):
            archive = self.data_dir / f"{name}.tsv.gz"
            archive.write_bytes(b"test")
            os.utime(archive, (source_time.timestamp(), source_time.timestamp()))
            meta = archive.with_suffix(archive.suffix + ".meta.json")
            meta.write_text(
                json.dumps(
                    {
                        "etag": f'"{name}-etag"',
                        "last_modified": http_date,
                        "content_length": "4",
                        "url": f"https://datasets.imdbws.com/{name}.tsv.gz",
                        "size": 4,
                    }
                ),
                encoding="utf-8",
            )

    def _write_database(self, *, years: tuple[int, ...]) -> None:
        conn = duckdb.connect(str(self.db_path))
        try:
            conn.execute(
                """
                CREATE TABLE title_basics (
                    tconst VARCHAR,
                    startYear INTEGER
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE title_ratings (
                    tconst VARCHAR,
                    averageRating DOUBLE,
                    numVotes INTEGER
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE title_principals (
                    tconst VARCHAR,
                    nconst VARCHAR,
                    category VARCHAR
                )
                """
            )
            conn.execute("CREATE TABLE title_crew (tconst VARCHAR, writers VARCHAR)")
            conn.execute("CREATE TABLE title_writers (tconst VARCHAR, nconst VARCHAR)")
            conn.execute("CREATE TABLE name_basics (nconst VARCHAR, primaryName VARCHAR)")

            for year in years:
                for index in range(2):
                    tconst = f"tt{year}{index}"
                    conn.execute(
                        "INSERT INTO title_basics VALUES (?, ?)",
                        [tconst, year],
                    )
                    conn.execute(
                        "INSERT INTO title_ratings VALUES (?, ?, ?)",
                        [tconst, 7.0 + index * 0.1, 5000 + index],
                    )
                    conn.execute(
                        "INSERT INTO title_principals VALUES (?, ?, 'director')",
                        [tconst, f"nm-d-{year}-{index}"],
                    )
                    conn.execute(
                        "INSERT INTO title_principals VALUES (?, ?, 'actor')",
                        [tconst, f"nm-a-{year}-{index}"],
                    )
                    conn.execute(
                        "INSERT INTO title_writers VALUES (?, ?)",
                        [tconst, f"nm-w-{year}-{index}"],
                    )
                    conn.execute(
                        "INSERT INTO title_crew VALUES (?, ?)",
                        [tconst, f"nm-w-{year}-{index}"],
                    )
            conn.execute("INSERT INTO name_basics VALUES ('nm1', 'Test')")
        finally:
            conn.close()

    def _report(self, **kwargs):
        return build_freshness_report(
            self.db_path,
            data_dir=self.data_dir,
            as_of=AS_OF,
            policy=kwargs.pop(
                "policy",
                FreshnessPolicy(
                    source_max_age_days=14,
                    high_vote_threshold=1000,
                    recent_year_span=3,
                ),
            ),
            **kwargs,
        )

    def test_fresh_snapshot_is_ready_but_current_year_is_provisional(self):
        report = self._report()
        self.assertTrue(report["ready_for_full_training"])
        self.assertEqual(report["max_title_year"], 2026)
        self.assertEqual(report["stable_history_through_year"], 2025)
        self.assertEqual(report["recommended_training_target_max_year"], 2025)
        self.assertEqual(report["current_year_status"], "provisional")
        self.assertEqual(report["current_year_high_vote_candidate_count"], 2)
        self.assertEqual(report["recent_year_coverage"]["2025"]["rating_coverage"], 1.0)
        self.assertEqual(report["recent_year_coverage"]["2025"]["director_coverage"], 1.0)
        self.assertEqual(report["recent_year_coverage"]["2025"]["writer_coverage"], 1.0)
        self.assertEqual(report["recent_year_coverage"]["2025"]["cast_coverage"], 1.0)
        self.assertEqual(len(report["logical_fingerprint_sha256"]), 64)

    def test_missing_current_year_blocks_full_training(self):
        self.db_path.unlink()
        self._write_database(years=(2024, 2025))
        os.utime(self.db_path, (DB_TIME.timestamp(), DB_TIME.timestamp()))
        report = self._report()
        self.assertFalse(report["ready_for_full_training"])
        self.assertIn(
            "latest_title_year_too_old:2025<current_year:2026",
            report["blocking_reasons"],
        )
        self.assertIn("recent_year_missing:2026", report["blocking_reasons"])

    def test_stale_source_metadata_blocks_full_training(self):
        stale = datetime(2026, 8, 1, 12, 0, 0, tzinfo=timezone.utc)
        self._write_dataset_files(stale)
        os.utime(self.db_path, (DB_TIME.timestamp(), DB_TIME.timestamp()))
        report = self._report()
        self.assertFalse(report["ready_for_full_training"])
        self.assertTrue(
            any(reason.startswith("dataset_stale:") for reason in report["blocking_reasons"])
        )

    def test_database_older_than_downloaded_datasets_is_blocked(self):
        old_db = datetime(2026, 9, 30, 12, 0, 0, tzinfo=timezone.utc)
        os.utime(self.db_path, (old_db.timestamp(), old_db.timestamp()))
        report = self._report()
        self.assertFalse(report["ready_for_full_training"])
        self.assertIn(
            "database_older_than_downloaded_datasets",
            report["blocking_reasons"],
        )

    def test_require_raises_and_manifest_is_reproducible(self):
        fresh = require_fresh_imdb_data(
            self.db_path,
            data_dir=self.data_dir,
            as_of=AS_OF,
            policy=FreshnessPolicy(source_max_age_days=14),
        )
        manifest_path = write_freshness_manifest(
            fresh, self.root / "freshness-manifest.json"
        )
        loaded = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(
            loaded["logical_fingerprint_sha256"],
            fresh["logical_fingerprint_sha256"],
        )

        self.db_path.unlink()
        self._write_database(years=(2024, 2025))
        os.utime(self.db_path, (DB_TIME.timestamp(), DB_TIME.timestamp()))
        with self.assertRaises(RuntimeError):
            require_fresh_imdb_data(
                self.db_path,
                data_dir=self.data_dir,
                as_of=AS_OF,
                policy=FreshnessPolicy(source_max_age_days=14),
            )


if __name__ == "__main__":
    unittest.main()
