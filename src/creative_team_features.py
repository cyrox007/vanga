from __future__ import annotations

from typing import Iterable

import duckdb
import pandas as pd


RECENT_WORK_LIMIT = 5


def _genre_list(genres: str | Iterable[str] | None) -> list[str]:
    if genres is None:
        return []
    if isinstance(genres, str):
        values = genres.split(",")
    else:
        values = list(genres)
    return sorted({str(value).strip() for value in values if str(value).strip()})


def load_training_context(
    conn: duckdb.DuckDBPyConnection,
    tconsts: list[str],
    *,
    recent_limit: int = RECENT_WORK_LIMIT,
) -> pd.DataFrame:
    """Считает P2-A признаки для одного training batch.

    Все исторические строки строго старше целевого фильма. Жанровая история
    использует пересечение жанров target/history, но один исторический фильм
    учитывается ровно один раз независимо от числа совпавших жанров.
    """
    if not tconsts:
        return pd.DataFrame()
    if recent_limit < 1:
        raise ValueError("recent_limit должен быть положительным")

    query = """
        WITH batch_movies AS (
            SELECT
                b.tconst,
                TRY_CAST(b.startYear AS INTEGER) AS start_year,
                b.genres
            FROM UNNEST(?) AS t(tconst)
            JOIN title_basics b ON b.tconst = t.tconst
            WHERE b.titleType = 'movie'
              AND TRY_CAST(b.startYear AS INTEGER) IS NOT NULL
        ),
        director_ord AS (
            SELECT tconst, MIN(ordering) AS min_ord
            FROM title_principals
            WHERE category = 'director'
              AND tconst IN (SELECT tconst FROM batch_movies)
            GROUP BY tconst
        ),
        first_director AS (
            SELECT p.tconst, p.nconst
            FROM title_principals p
            JOIN director_ord d
              ON d.tconst = p.tconst
             AND d.min_ord = p.ordering
        ),
        first_writer AS (
            SELECT
                c.tconst,
                NULLIF(TRIM(split_part(c.writers, ',', 1)), '') AS nconst
            FROM title_crew c
            WHERE c.tconst IN (SELECT tconst FROM batch_movies)
              AND c.writers IS NOT NULL
              AND c.writers <> '\\N'
        ),
        director_prior_base AS (
            SELECT DISTINCT
                bm.tconst AS target_tconst,
                p.tconst AS history_tconst,
                TRY_CAST(h.startYear AS INTEGER) AS history_year,
                TRY_CAST(r.averageRating AS DOUBLE) AS rating,
                CASE
                    WHEN bm.genres IS NOT NULL
                     AND h.genres IS NOT NULL
                     AND list_has_any(
                        string_split(bm.genres, ','),
                        string_split(h.genres, ',')
                     )
                    THEN 1 ELSE 0
                END AS genre_match
            FROM batch_movies bm
            JOIN first_director fd ON fd.tconst = bm.tconst
            JOIN title_principals p
              ON p.nconst = fd.nconst
             AND p.category = 'director'
            JOIN title_basics h ON h.tconst = p.tconst
            JOIN title_ratings r ON r.tconst = h.tconst
            WHERE h.titleType = 'movie'
              AND TRY_CAST(h.startYear AS INTEGER) < bm.start_year
              AND TRY_CAST(r.averageRating AS DOUBLE) IS NOT NULL
        ),
        director_prior AS (
            SELECT
                *,
                ROW_NUMBER() OVER (
                    PARTITION BY target_tconst
                    ORDER BY history_year DESC, history_tconst DESC
                ) AS recent_rank
            FROM director_prior_base
        ),
        director_context AS (
            SELECT
                target_tconst AS tconst,
                AVG(CASE WHEN genre_match = 1 THEN rating END) AS director_genre_avg_rating,
                COUNT(DISTINCT CASE WHEN genre_match = 1 THEN history_tconst END) AS director_genre_prior_count,
                AVG(CASE WHEN recent_rank <= ? THEN rating END) AS director_recent_avg_rating,
                COUNT(CASE WHEN recent_rank <= ? THEN 1 END) AS director_recent_count
            FROM director_prior
            GROUP BY target_tconst
        ),
        writer_prior_base AS (
            SELECT DISTINCT
                bm.tconst AS target_tconst,
                tw.tconst AS history_tconst,
                TRY_CAST(h.startYear AS INTEGER) AS history_year,
                TRY_CAST(r.averageRating AS DOUBLE) AS rating,
                CASE
                    WHEN bm.genres IS NOT NULL
                     AND h.genres IS NOT NULL
                     AND list_has_any(
                        string_split(bm.genres, ','),
                        string_split(h.genres, ',')
                     )
                    THEN 1 ELSE 0
                END AS genre_match
            FROM batch_movies bm
            JOIN first_writer fw ON fw.tconst = bm.tconst
            JOIN title_writers tw ON tw.nconst = fw.nconst
            JOIN title_basics h ON h.tconst = tw.tconst
            JOIN title_ratings r ON r.tconst = h.tconst
            WHERE h.titleType = 'movie'
              AND TRY_CAST(h.startYear AS INTEGER) < bm.start_year
              AND TRY_CAST(r.averageRating AS DOUBLE) IS NOT NULL
        ),
        writer_prior AS (
            SELECT
                *,
                ROW_NUMBER() OVER (
                    PARTITION BY target_tconst
                    ORDER BY history_year DESC, history_tconst DESC
                ) AS recent_rank
            FROM writer_prior_base
        ),
        writer_context AS (
            SELECT
                target_tconst AS tconst,
                AVG(CASE WHEN genre_match = 1 THEN rating END) AS writer_genre_avg_rating,
                COUNT(DISTINCT CASE WHEN genre_match = 1 THEN history_tconst END) AS writer_genre_prior_count,
                AVG(CASE WHEN recent_rank <= ? THEN rating END) AS writer_recent_avg_rating,
                COUNT(CASE WHEN recent_rank <= ? THEN 1 END) AS writer_recent_count
            FROM writer_prior
            GROUP BY target_tconst
        )
        SELECT
            bm.tconst,
            dc.director_genre_avg_rating,
            COALESCE(dc.director_genre_prior_count, 0) AS director_genre_prior_count,
            dc.director_recent_avg_rating,
            COALESCE(dc.director_recent_count, 0) AS director_recent_count,
            wc.writer_genre_avg_rating,
            COALESCE(wc.writer_genre_prior_count, 0) AS writer_genre_prior_count,
            wc.writer_recent_avg_rating,
            COALESCE(wc.writer_recent_count, 0) AS writer_recent_count,
            CASE
                WHEN fd.nconst IS NOT NULL
                 AND fw.nconst IS NOT NULL
                 AND fd.nconst = fw.nconst
                THEN 1 ELSE 0
            END AS director_is_writer
        FROM batch_movies bm
        LEFT JOIN first_director fd ON fd.tconst = bm.tconst
        LEFT JOIN first_writer fw ON fw.tconst = bm.tconst
        LEFT JOIN director_context dc ON dc.tconst = bm.tconst
        LEFT JOIN writer_context wc ON wc.tconst = bm.tconst
        ORDER BY bm.tconst
    """
    return conn.execute(
        query,
        [tconsts, recent_limit, recent_limit, recent_limit, recent_limit],
    ).df()


def load_person_context(
    conn: duckdb.DuckDBPyConnection,
    *,
    nconst: str | None,
    role: str,
    before_year: int,
    genres: str | Iterable[str] | None,
    recent_limit: int = RECENT_WORK_LIMIT,
) -> dict:
    """Считает genre/recent history для inference по уже разрешённому IMDb ID."""
    if role not in {"director", "writer"}:
        raise ValueError("role должен быть director или writer")
    if recent_limit < 1:
        raise ValueError("recent_limit должен быть положительным")
    if not nconst:
        return {
            "genre_avg_rating": 6.5,
            "genre_prior_count": 0,
            "genre_known": 0.0,
            "recent_avg_rating": 6.5,
            "recent_count": 0,
            "recent_known": 0.0,
        }

    target_genres = _genre_list(genres)
    if role == "writer":
        credits = "title_writers tw"
        join_condition = "tw.nconst = ?"
        title_expr = "tw.tconst"
    else:
        credits = "title_principals tp"
        join_condition = "tp.nconst = ? AND tp.category = 'director'"
        title_expr = "tp.tconst"

    query = f"""
        WITH history_base AS (
            SELECT DISTINCT
                {title_expr} AS history_tconst,
                TRY_CAST(b.startYear AS INTEGER) AS history_year,
                b.genres,
                TRY_CAST(r.averageRating AS DOUBLE) AS rating
            FROM {credits}
            JOIN title_basics b ON b.tconst = {title_expr}
            JOIN title_ratings r ON r.tconst = b.tconst
            WHERE {join_condition}
              AND b.titleType = 'movie'
              AND TRY_CAST(b.startYear AS INTEGER) < ?
              AND TRY_CAST(r.averageRating AS DOUBLE) IS NOT NULL
        ),
        ranked AS (
            SELECT
                *,
                ROW_NUMBER() OVER (
                    ORDER BY history_year DESC, history_tconst DESC
                ) AS recent_rank
            FROM history_base
        )
        SELECT
            AVG(
                CASE
                    WHEN len(?) > 0
                     AND genres IS NOT NULL
                     AND list_has_any(string_split(genres, ','), ?)
                    THEN rating
                END
            ) AS genre_avg_rating,
            COUNT(
                CASE
                    WHEN len(?) > 0
                     AND genres IS NOT NULL
                     AND list_has_any(string_split(genres, ','), ?)
                    THEN 1
                END
            ) AS genre_prior_count,
            AVG(CASE WHEN recent_rank <= ? THEN rating END) AS recent_avg_rating,
            COUNT(CASE WHEN recent_rank <= ? THEN 1 END) AS recent_count
        FROM ranked
    """
    row = conn.execute(
        query,
        [
            nconst,
            int(before_year),
            target_genres,
            target_genres,
            target_genres,
            target_genres,
            recent_limit,
            recent_limit,
        ],
    ).fetchone()
    genre_avg, genre_count, recent_avg, recent_count = row or (None, 0, None, 0)
    genre_count = int(genre_count or 0)
    recent_count = int(recent_count or 0)
    return {
        "genre_avg_rating": float(genre_avg) if genre_avg is not None else 6.5,
        "genre_prior_count": genre_count,
        "genre_known": 1.0 if genre_count > 0 else 0.0,
        "recent_avg_rating": float(recent_avg) if recent_avg is not None else 6.5,
        "recent_count": recent_count,
        "recent_known": 1.0 if recent_count > 0 else 0.0,
    }
