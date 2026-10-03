from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

import duckdb

from src.proxy_materializer_audited import AuditedProxySourceMaterializer
from src.source_context import SourceContextStore


def _plan() -> dict:
    payload = {
        "registry_version": 1,
        "hypothesis_id": "same-year-history-test",
        "title": "Same year guard",
        "retrospective_signal": {
            "dimension": "plot",
            "change_type": "removed",
            "rationale": "test",
            "evidence": [
                {
                    "evidence_kind": "storydiff",
                    "reference_id": "storydiff:test",
                    "confidence": 0.9,
                }
            ],
        },
        "candidate_pre_release_features": [
            {
                "feature_name": "source_writer_adaptation_count",
                "source_layer": "imdb_history",
                "temporal_contract": "history_before_target_year",
                "proxy_role": "primary",
                "coverage_feature": None,
                "notes": None,
            }
        ],
        "ablation": {
            "baseline_schema": "v15",
            "candidate_label": "candidate-test",
            "holdout_policy": "stable_temporal_last_two_years",
            "primary_metric": "mae",
            "max_mae_regression": 0.0,
            "require_improvement": True,
            "dataset_fingerprint_required": True,
            "preregistered_at": "2026-01-01T00:00:00+00:00",
        },
        "status": "ablation_ready",
        "post_release_features_allowed": False,
        "expert_interpretation_as_feature": False,
        "automatic_catboost_inclusion": False,
    }
    payload["plan_fingerprint_sha256"] = hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return payload


class SameYearAdaptationHistoryGuardTests(unittest.TestCase):
    def test_same_year_prior_adaptation_is_explicit_missing(self):
        temp = tempfile.TemporaryDirectory()
        try:
            root = Path(temp.name)
            source_db = root / "source.duckdb"
            production_db = root / "production.duckdb"
            imdb_db = root / "imdb.duckdb"

            source = SourceContextStore(source_db)
            try:
                source.upsert_source(
                    {
                        "source_id": "src",
                        "url": "https://example.com/source",
                        "retrieved_at": "2025-01-01T00:00:00+00:00",
                    }
                )
                source.upsert_work(
                    {
                        "work_id": "book",
                        "title": "Book",
                        "source_type": "novel",
                    }
                )
                for project_id, imdb_id, release_at, known_at in (
                    ("prior-2024", "tt8100001", "2024-05-01T00:00:00+00:00", "2024-01-01T00:00:00+00:00"),
                    ("prior-2025", "tt8100002", "2025-01-20T00:00:00+00:00", "2025-01-01T00:00:00+00:00"),
                    ("target", "tt9100001", "2025-08-01T00:00:00+00:00", "2025-01-05T00:00:00+00:00"),
                ):
                    source.upsert_project(
                        {
                            "project_id": project_id,
                            "imdb_id": imdb_id,
                            "title": project_id,
                            "release_at": release_at,
                            "adaptation_format": "film",
                            "format_known_at": known_at,
                        }
                    )
                    source.link_source(
                        {
                            "link_id": f"link-{project_id}",
                            "project_id": project_id,
                            "work_id": "book",
                            "relation_type": "based_on",
                            "is_primary": True,
                            "known_at": known_at,
                            "source_id": "src",
                        }
                    )
            finally:
                source.close()

            conn = duckdb.connect(str(imdb_db))
            conn.execute(
                "CREATE TABLE title_basics(tconst VARCHAR, titleType VARCHAR, primaryTitle VARCHAR, startYear VARCHAR, runtimeMinutes VARCHAR, genres VARCHAR)"
            )
            conn.execute("CREATE TABLE title_ratings(tconst VARCHAR, averageRating DOUBLE)")
            conn.execute(
                "CREATE TABLE title_principals(tconst VARCHAR, ordering INTEGER, nconst VARCHAR, category VARCHAR)"
            )
            conn.execute("CREATE TABLE title_writers(tconst VARCHAR, nconst VARCHAR)")
            for tconst, year in (
                ("tt8100001", "2024"),
                ("tt8100002", "2025"),
                ("tt9100001", "2025"),
            ):
                conn.execute(
                    "INSERT INTO title_basics VALUES (?, 'movie', ?, ?, '100', 'Drama')",
                    [tconst, tconst, year],
                )
                conn.execute("INSERT INTO title_writers VALUES (?, 'nm-writer')", [tconst])
            conn.close()

            materializer = AuditedProxySourceMaterializer(
                source_db_path=source_db,
                production_db_path=production_db,
                imdb_db_path=imdb_db,
            )
            result = materializer.materialize(
                _plan(),
                [
                    {
                        "target_id": "tt9100001",
                        "target_year": 2025,
                        "cutoff_at": "2025-03-01T00:00:00+00:00",
                        "release_at": "2025-08-01T00:00:00+00:00",
                    }
                ],
            )
            row = result["observations"][0]

            self.assertFalse(row["available"])
            self.assertIsNone(row["value"])
            self.assertIn("same-year", row["missing_reason"])
            self.assertIn("tt8100002", row["missing_reason"])
        finally:
            temp.cleanup()


if __name__ == "__main__":
    unittest.main()
