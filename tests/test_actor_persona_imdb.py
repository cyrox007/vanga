from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import duckdb

from src.actor_persona import ActorPersonaStore
from src.actor_persona_imdb import materialize_imdb_actor_roles


class ActorPersonaImdbTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.imdb = root / "imdb.duckdb"
        conn = duckdb.connect(str(self.imdb))
        conn.execute("CREATE TABLE title_basics(tconst VARCHAR, startYear INTEGER, genres VARCHAR)")
        conn.execute("CREATE TABLE title_principals(tconst VARCHAR, nconst VARCHAR, category VARCHAR, characters VARCHAR)")
        conn.execute("INSERT INTO title_basics VALUES ('tt1', 2020, 'Action,Sci-Fi'), ('tt2', 2027, 'Comedy')")
        conn.execute("INSERT INTO title_principals VALUES ('tt1','nm1','actor','[\"Agent X\"]'), ('tt2','nm1','actor','[\"Funny Guy\"]')")
        conn.close()
        self.store = ActorPersonaStore(root / "actor.duckdb")

    def tearDown(self) -> None:
        self.store.close()
        self.tmp.cleanup()

    def test_materializer_is_temporal_and_idempotent(self) -> None:
        first = materialize_imdb_actor_roles(self.store, imdb_db_path=self.imdb)
        second = materialize_imdb_actor_roles(self.store, imdb_db_path=self.imdb)
        self.assertEqual(first["appearances_created"], 2)
        self.assertEqual(second["appearances_created"], 0)
        self.assertEqual(second["skipped_existing"], 2)
        snapshot = self.store.persona_snapshot_as_of("nm1", "2026-01-01T00:00:00Z")
        self.assertEqual(snapshot["works_count"], 1)
        self.assertIn("action", snapshot["genres"])
        self.assertNotIn("comedy", snapshot["genres"])


if __name__ == "__main__":
    unittest.main()
