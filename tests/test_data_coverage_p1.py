from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import duckdb

from src.pre_release_analysis import build_pre_release_profile


class DataCoverageP1Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.conn = duckdb.connect(str(Path(self.temp.name) / "imdb.duckdb"))
        self.conn.execute("CREATE TABLE title_basics (tconst VARCHAR, titleType VARCHAR, primaryTitle VARCHAR, startYear VARCHAR)")
        self.conn.execute("CREATE TABLE title_ratings (tconst VARCHAR, averageRating DOUBLE, numVotes BIGINT)")
        self.conn.execute("CREATE TABLE title_principals (tconst VARCHAR, nconst VARCHAR, category VARCHAR)")
        self.conn.execute("CREATE TABLE title_writers (tconst VARCHAR, nconst VARCHAR)")
        self.conn.execute("CREATE TABLE name_basics (nconst VARCHAR, primaryName VARCHAR)")

        self.conn.executemany(
            "INSERT INTO name_basics VALUES (?, ?)",
            [
                ("nm_d", "Director Known"),
                ("nm_w", "Writer Known"),
                ("nm_a", "Actor Known"),
                ("nm_new", "New Actor"),
            ],
        )
        self.conn.executemany(
            "INSERT INTO title_basics VALUES (?, ?, ?, ?)",
            [
                ("tt1", "movie", "Past One", "2020"),
                ("tt2", "movie", "Past Two", "2022"),
                ("tt_future", "movie", "Future", "2027"),
            ],
        )
        self.conn.executemany(
            "INSERT INTO title_ratings VALUES (?, ?, ?)",
            [("tt1", 8.0, 1000), ("tt2", 7.0, 1000), ("tt_future", 9.9, 1)],
        )
        self.conn.executemany(
            "INSERT INTO title_principals VALUES (?, ?, ?)",
            [
                ("tt1", "nm_d", "director"),
                ("tt2", "nm_d", "director"),
                ("tt_future", "nm_d", "director"),
                ("tt1", "nm_a", "actor"),
                ("tt2", "nm_a", "actor"),
            ],
        )
        self.conn.executemany(
            "INSERT INTO title_writers VALUES (?, ?)",
            [("tt1", "nm_w"), ("tt2", "nm_w"), ("tt_future", "nm_w")],
        )

    def tearDown(self):
        self.conn.close()
        self.temp.cleanup()

    def _profile(self, **overrides):
        params = {
            "conn": self.conn,
            "year": 2025,
            "runtime": 120,
            "director": "Director Known",
            "writer": "Writer Known",
            "actors": ["Actor Known", "New Actor", "Missing Actor"],
            "synopsis": "Герой защищает королевство и свою семью.",
            "rating": 7.1,
            "uncertainty": {"lower": 6.0, "upper": 8.2},
            "contributions": {"director_avg_rating": 0.2},
        }
        params.update(overrides)
        return build_pre_release_profile(**params)

    def test_exposes_prior_counts_ratios_and_missing_features(self):
        coverage = self._profile()["data_coverage"]

        self.assertEqual(coverage["director"]["prior_count"], 2)
        self.assertEqual(coverage["writer"]["prior_count"], 2)
        self.assertEqual(coverage["actors"][0]["prior_count"], 2)
        self.assertEqual(coverage["known_people_count"], 3)
        self.assertEqual(coverage["provided_people_count"], 5)
        self.assertEqual(coverage["known_people_ratio"], 0.6)
        self.assertEqual(coverage["missing_feature_count"], 2)
        self.assertGreater(coverage["model_familiarity"]["score"], 0.5)
        self.assertFalse(coverage["abstention"]["recommended"])

    def test_resolved_person_without_prior_history_is_not_fake_average(self):
        actor = self._profile()["data_coverage"]["actors"][1]

        self.assertTrue(actor["resolved"])
        self.assertFalse(actor["known"])
        self.assertEqual(actor["state"], "resolved_no_history")
        self.assertEqual(actor["prior_count"], 0)
        self.assertIsNone(actor["avg_rating"])

    def test_unknown_person_is_distinct_from_resolved_no_history(self):
        actor = self._profile()["data_coverage"]["actors"][2]

        self.assertFalse(actor["resolved"])
        self.assertFalse(actor["known"])
        self.assertEqual(actor["state"], "not_resolved")
        self.assertIsNone(actor["avg_rating"])

    def test_extremely_low_familiarity_recommends_abstention(self):
        coverage = self._profile(
            director="Missing Director",
            writer=None,
            actors=[],
            synopsis=None,
        )["data_coverage"]

        self.assertEqual(coverage["model_familiarity"]["score"], 0.0)
        self.assertEqual(coverage["missing_feature_count"], 5)
        self.assertTrue(coverage["abstention"]["recommended"])
        self.assertEqual(
            coverage["abstention"]["reason"],
            "insufficient_person_history",
        )


if __name__ == "__main__":
    unittest.main()
