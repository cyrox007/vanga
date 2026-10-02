from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import duckdb

from settings import config
from src.create_db import create_derived_tables


class WriterSchemaTests(unittest.TestCase):
    def test_create_derived_tables_normalizes_writer_ids(self):
        old_abspath = config.ABSPATH
        old_db_path = config.IMDB_DB_PATH

        with tempfile.TemporaryDirectory() as tmp:
            config.ABSPATH = tmp
            db_path = Path(tmp) / "imdb.duckdb"
            config.IMDB_DB_PATH = str(db_path)
            try:
                conn = duckdb.connect(str(db_path))
                conn.execute(
                    """
                    CREATE TABLE title_crew (
                        tconst VARCHAR,
                        directors VARCHAR,
                        writers VARCHAR
                    )
                    """
                )
                conn.executemany(
                    "INSERT INTO title_crew VALUES (?, ?, ?)",
                    [
                        ("tt1", "nm_d1", "nm_w1,nm_w2"),
                        ("tt2", "nm_d2", None),
                        ("tt3", "nm_d3", "\\N"),
                    ],
                )
                conn.close()

                create_derived_tables()

                conn = duckdb.connect(str(db_path), read_only=True)
                try:
                    rows = conn.execute(
                        """
                        SELECT tconst, nconst
                        FROM title_writers
                        ORDER BY tconst, nconst
                        """
                    ).fetchall()
                finally:
                    conn.close()

                self.assertEqual(
                    rows,
                    [("tt1", "nm_w1"), ("tt1", "nm_w2")],
                )
            finally:
                config.ABSPATH = old_abspath
                config.IMDB_DB_PATH = old_db_path


if __name__ == "__main__":
    unittest.main()
