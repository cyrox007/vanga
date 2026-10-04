from __future__ import annotations

import json
import pickle
import tempfile
import unittest
from pathlib import Path

import duckdb

from src.release_readiness import ReleaseReadiness


class ReleaseReadinessTests(unittest.TestCase):
    def _root(self, base: Path) -> Path:
        root = base / "repo"
        for relative in (
            "api.py",
            "settings.py",
            "src/future_http_api.py",
            "src/future_regional_release.py",
            "deploy/update-vanga.sh",
        ):
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("# test\n", encoding="utf-8")
        return root

    def _imdb(self, path: Path) -> None:
        conn = duckdb.connect(str(path))
        try:
            conn.execute("CREATE TABLE title_basics(tconst VARCHAR)")
            conn.execute("CREATE TABLE title_crew(tconst VARCHAR, writers VARCHAR)")
            conn.execute("CREATE TABLE title_writers(tconst VARCHAR, nconst VARCHAR)")
        finally:
            conn.close()

    def _p9(self, path: Path, *, with_project: bool = True) -> None:
        conn = duckdb.connect(str(path))
        try:
            conn.execute("CREATE TABLE future_release_projects(project_id VARCHAR)")
            conn.execute("CREATE TABLE future_release_sources(source_id VARCHAR)")
            conn.execute("CREATE TABLE future_release_windows(observation_id VARCHAR)")
            conn.execute("CREATE TABLE future_release_temporal_facts(observation_id VARCHAR)")
            conn.execute("CREATE TABLE future_release_territory_mappings(mapping_id VARCHAR)")
            if with_project:
                conn.execute("INSERT INTO future_release_projects VALUES ('film-1')")
                conn.execute("INSERT INTO future_release_sources VALUES ('source-1')")
                conn.execute("INSERT INTO future_release_windows VALUES ('release-1')")
        finally:
            conn.close()

    def _model(self, root: Path, *, gate_passed: bool = True) -> None:
        generation = "20261004T120000Z-test"
        release = root / "models" / "releases" / generation
        release.mkdir(parents=True, exist_ok=True)
        (release / "model.cbm").write_bytes(b"fake-model")
        with (release / "metadata.pkl").open("wb") as handle:
            pickle.dump(
                {
                    "schema_version": 15,
                    "test_mae": 0.91,
                    "quality_gate": {"passed": gate_passed},
                },
                handle,
            )
        pointer = root / "models" / "current.json"
        pointer.parent.mkdir(parents=True, exist_ok=True)
        pointer.write_text(json.dumps({"generation": generation}), encoding="utf-8")

    def test_offline_report_ready_when_runtime_is_complete(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = self._root(base)
            imdb = base / "imdb.duckdb"
            p9 = base / "future.duckdb"
            self._imdb(imdb)
            self._p9(p9)
            self._model(root)

            report = ReleaseReadiness(
                root=root,
                imdb_db_path=imdb,
                future_db_path=p9,
                strict_p9=True,
            ).run()

            self.assertTrue(report["ready"])
            self.assertEqual(report["summary"]["fail"], 0)
            statuses = {item["name"]: item["status"] for item in report["checks"]}
            self.assertEqual(statuses["imdb_db"], "pass")
            self.assertEqual(statuses["active_model"], "pass")
            self.assertEqual(statuses["p9_registry"], "pass")

    def test_failed_quality_gate_is_release_blocker(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = self._root(base)
            imdb = base / "imdb.duckdb"
            p9 = base / "future.duckdb"
            self._imdb(imdb)
            self._p9(p9)
            self._model(root, gate_passed=False)

            report = ReleaseReadiness(
                root=root,
                imdb_db_path=imdb,
                future_db_path=p9,
            ).run()

            self.assertFalse(report["ready"])
            model = next(item for item in report["checks"] if item["name"] == "active_model")
            self.assertEqual(model["status"], "fail")

    def test_empty_p9_is_warning_unless_strict(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            p9 = base / "future.duckdb"
            self._p9(p9, with_project=False)

            relaxed = ReleaseReadiness(future_db_path=p9).check_p9_registry()
            strict = ReleaseReadiness(future_db_path=p9, strict_p9=True).check_p9_registry()

            self.assertEqual(relaxed.status, "warn")
            self.assertEqual(strict.status, "fail")

    def test_live_api_distinguishes_empty_catalog_from_failure(self) -> None:
        checker = ReleaseReadiness()
        responses = {
            "/health": (200, {"ok": True}),
            "/model-info": (200, {"ok": True}),
        }

        def fake_http(url: str, *, timeout: float = 4.0):
            del timeout
            for suffix, response in responses.items():
                if url.endswith(suffix):
                    return response
            if "/future/catalog?" in url:
                return 200, {"ok": True, "items": []}
            raise AssertionError(url)

        checker._http_json = fake_http  # type: ignore[method-assign]
        checks = checker.check_live_api(
            api_base_url="http://127.0.0.1:9100",
            markets=["DE"],
        )
        statuses = {item.name: item.status for item in checks}
        self.assertEqual(statuses["api_health"], "pass")
        self.assertEqual(statuses["api_model_info"], "pass")
        self.assertEqual(statuses["future_catalog_de"], "warn")


if __name__ == "__main__":
    unittest.main()
