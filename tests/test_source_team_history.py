from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import duckdb

from src.source_context import SourceContextStore
from src.source_team_history import SourceTeamHistory


class SourceTeamHistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.source_path = root / "source_context.duckdb"
        self.imdb_path = root / "imdb.duckdb"
        self.store = SourceContextStore(self.source_path)

        imdb = duckdb.connect(str(self.imdb_path))
        imdb.execute(
            "CREATE TABLE title_basics (tconst VARCHAR, titleType VARCHAR, primaryTitle VARCHAR, startYear VARCHAR, runtimeMinutes VARCHAR, genres VARCHAR)"
        )
        imdb.execute(
            "CREATE TABLE title_principals (tconst VARCHAR, ordering INTEGER, nconst VARCHAR, category VARCHAR)"
        )
        imdb.execute(
            "CREATE TABLE title_writers (tconst VARCHAR, nconst VARCHAR)"
        )

        movies = (
            ("tt_p1", "2020", "Drama"),
            ("tt_p2", "2021", "Action"),
            ("tt_p3", "2022", "Fantasy"),
            ("tt_p4", "2023", "Drama"),
            ("tt_late", "2019", "Drama"),
            ("tt_future", "2026", "Drama"),
            ("tt_target", "2025", "Drama,Fantasy"),
        )
        for tconst, year, genres in movies:
            imdb.execute(
                "INSERT INTO title_basics VALUES (?, 'movie', ?, ?, '120', ?)",
                [tconst, tconst, year, genres],
            )

        credits = {
            "tt_p1": (["nm_a"], ["nm_w"]),
            "tt_p2": (["nm_a"], ["nm_x"]),
            "tt_p3": (["nm_b"], ["nm_w"]),
            "tt_p4": (["nm_b"], ["nm_w"]),
            "tt_late": (["nm_a"], ["nm_w"]),
            "tt_future": (["nm_a"], ["nm_w"]),
            "tt_target": (["nm_a", "nm_b"], ["nm_w"]),
        }
        for tconst, (directors, writers) in credits.items():
            for ordering, director in enumerate(directors, start=1):
                imdb.execute(
                    "INSERT INTO title_principals VALUES (?, ?, ?, 'director')",
                    [tconst, ordering, director],
                )
            for writer in writers:
                imdb.execute(
                    "INSERT INTO title_writers VALUES (?, ?)",
                    [tconst, writer],
                )
        imdb.close()

        for source_id, date in (
            ("src-old", "2020-01-01T00:00:00Z"),
            ("src-target", "2024-01-01T00:00:00Z"),
            ("src-late", "2025-01-01T00:00:00Z"),
        ):
            self.store.upsert_source(
                {
                    "source_id": source_id,
                    "url": f"https://example.test/{source_id}",
                    "published_at": date,
                    "retrieved_at": date,
                }
            )

        for work_id, source_type in (
            ("novel", "novel"),
            ("comic", "comic"),
            ("game", "game"),
        ):
            self.store.upsert_work(
                {
                    "work_id": work_id,
                    "title": work_id,
                    "source_type": source_type,
                    "first_publication_at": "2000-01-01T00:00:00Z",
                }
            )

        projects = (
            ("p1", "tt_p1", "2020-07-01T00:00:00Z", "novel", "2020-01-01T00:00:00Z", "src-old"),
            ("p2", "tt_p2", "2021-07-01T00:00:00Z", "comic", "2021-01-01T00:00:00Z", "src-old"),
            ("p3", "tt_p3", "2022-07-01T00:00:00Z", "novel", "2022-01-01T00:00:00Z", "src-old"),
            ("p4", "tt_p4", "2023-07-01T00:00:00Z", "game", "2023-01-01T00:00:00Z", "src-old"),
            # Старый фильм, но source-link раскрыт только в 2025.
            ("late-old", "tt_late", "2019-07-01T00:00:00Z", "novel", "2025-01-01T00:00:00Z", "src-late"),
            # Future project с заранее известным source-link не является prior experience.
            ("future", "tt_future", "2026-07-01T00:00:00Z", "novel", "2024-01-01T00:00:00Z", "src-target"),
            ("target", "tt_target", "2025-07-01T00:00:00Z", "novel", "2024-01-01T00:00:00Z", "src-target"),
        )
        for project_id, imdb_id, release_at, work_id, known_at, source_id in projects:
            self.store.upsert_project(
                {
                    "project_id": project_id,
                    "imdb_id": imdb_id,
                    "title": project_id,
                    "release_at": release_at,
                    "adaptation_format": "film",
                    "planned_runtime_minutes": 120,
                    "format_known_at": known_at,
                }
            )
            self.store.link_source(
                {
                    "link_id": f"{project_id}:{work_id}",
                    "project_id": project_id,
                    "work_id": work_id,
                    "relation_type": "adaptation_of",
                    "is_primary": True,
                    "known_at": known_at,
                    "source_id": source_id,
                }
            )

        self.history = SourceTeamHistory(self.store, self.imdb_path)

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def test_multi_director_history_uses_all_directors(self):
        f = self.history.features_as_of("target", "2024-06-01T00:00:00Z")
        # A: p1,p2 = 2; B: p3,p4 = 2.
        self.assertEqual(f["source_director_adaptation_known_ratio"], 1.0)
        self.assertEqual(f["source_director_adaptation_count_mean"], 2.0)
        self.assertEqual(f["source_director_adaptation_count_max"], 2.0)
        # Exact source type novel: A=p1, B=p3.
        self.assertEqual(f["source_director_same_type_adaptation_count_mean"], 1.0)
        self.assertEqual(f["source_director_same_type_adaptation_count_max"], 1.0)
        # Target genres Drama/Fantasy: A=p1, B=p3+p4.
        self.assertEqual(f["source_director_same_genre_adaptation_count_mean"], 1.5)
        self.assertEqual(f["source_director_same_genre_adaptation_count_max"], 2.0)

    def test_writer_history_is_adaptation_specific(self):
        f = self.history.features_as_of("target", "2024-06-01T00:00:00Z")
        # W: p1,p3,p4.
        self.assertEqual(f["source_writer_adaptation_count"], 3.0)
        self.assertEqual(f["source_writer_adaptation_known"], 1.0)
        self.assertEqual(f["source_writer_same_type_adaptation_count"], 2.0)
        self.assertEqual(f["source_writer_same_genre_adaptation_count"], 3.0)

    def test_director_writer_pair_history_is_aggregated_over_director_team(self):
        f = self.history.features_as_of("target", "2024-06-01T00:00:00Z")
        # A+W=p1 (1), B+W=p3,p4 (2).
        self.assertEqual(f["source_director_writer_adaptation_pair_known_ratio"], 1.0)
        self.assertEqual(f["source_director_writer_adaptation_pair_count_mean"], 1.5)
        self.assertEqual(f["source_director_writer_adaptation_pair_count_max"], 2.0)
        self.assertEqual(f["source_director_writer_same_type_pair_count_mean"], 1.0)
        self.assertEqual(f["source_director_writer_same_genre_pair_count_mean"], 1.5)

    def test_future_and_late_disclosed_projects_do_not_leak(self):
        early = self.history.features_as_of("target", "2024-06-01T00:00:00Z")
        late = self.history.features_as_of("target", "2025-02-01T00:00:00Z")
        self.assertEqual(early["source_team_prior_adaptation_project_count"], 4.0)
        self.assertEqual(late["source_team_prior_adaptation_project_count"], 5.0)
        # Future остаётся исключён даже при late cutoff до target release.
        self.assertEqual(late["source_director_adaptation_count_max"], 3.0)

    def test_explicit_unknown_director_stays_in_denominator_as_zero(self):
        f = self.history.features_as_of(
            "target",
            "2024-06-01T00:00:00Z",
            director_nconsts=["nm_a", "Unknown"],
            writer_nconst="nm_w",
        )
        self.assertEqual(f["source_director_adaptation_known_ratio"], 0.5)
        self.assertEqual(f["source_director_adaptation_count_mean"], 1.0)
        self.assertEqual(f["source_director_adaptation_count_max"], 2.0)
        self.assertEqual(f["source_director_writer_adaptation_pair_known_ratio"], 0.5)

    def test_target_type_and_genre_coverage_are_explicit(self):
        f = self.history.features_as_of("target", "2024-06-01T00:00:00Z")
        self.assertEqual(f["source_team_target_type_known"], 1.0)
        self.assertEqual(f["source_team_target_genre_known"], 1.0)


if __name__ == "__main__":
    unittest.main()
