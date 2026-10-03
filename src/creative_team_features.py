from __future__ import annotations

import os
from typing import Iterable, Sequence

import duckdb


RECENT_WORKS_LIMIT = 5
CREATIVE_TEAM_FEATURE_NAMES = (
    "director_genre_avg_rating",
    "director_genre_prior_count",
    "director_recent_avg_rating",
    "writer_genre_avg_rating",
    "writer_genre_prior_count",
    "writer_recent_avg_rating",
    "director_is_writer",
)


def creative_team_features_enabled() -> bool:
    """Возвращает режим Creative Team-признаков для воспроизводимого ablation."""
    raw = str(os.getenv("VANGA_TRAIN_CREATIVE_TEAM_FEATURES", "1")).strip().casefold()
    return raw not in {"0", "false", "no", "off"}


def _genres_value(genres: str | Sequence[str] | None) -> str:
    if isinstance(genres, str):
        parts = [part.strip() for part in genres.split(",") if part.strip()]
    else:
        parts = [str(part).strip() for part in (genres or []) if str(part).strip()]
    return ",".join(parts)


def empty_creative_context() -> dict[str, float]:
    """Совместимый числовой fallback, дополненный явным genre count=0."""
    return {
        "genre_avg_rating": 6.5,
        "genre_prior_count": 0.0,
        "recent_avg_rating": 6.5,
    }


def fetch_batch_creative_team_context(
    conn: duckdb.DuckDBPyConnection,
    tconsts: Iterable[str],
) -> dict[str, dict[str, float]]:
    """Считает P2-контекст для target-фильмов только по более ранним годам.

    В отличие от post-release аналитики здесь нельзя использовать target rating,
    будущие фильмы или фактическую реакцию аудитории. Recent form — среднее по
    последним ``RECENT_WORKS_LIMIT`` прошлым фильмам в соответствующей роли.
    Genre history учитывает только прошлые фильмы хотя бы с одним общим жанром.
    """
    ids = [str(value).strip() for value in tconsts if str(value).strip()]
    if not ids:
        return {}

    query = f"""
        WITH batch_movies AS (
            SELECT
                b.tconst,
                TRY_CAST(b.startYear AS INTEGER) AS target_year,
                b.genres AS target_genres
            FROM UNNEST(?) AS requested(tconst)
            JOIN title_basics b USING (tconst)
            WHERE b.titleType = 'movie'
              AND TRY_CAST(b.startYear AS INTEGER) IS NOT NULL
        ),
        director_ord AS (
            SELECT p.tconst, MIN(p.ordering) AS min_ord
            FROM title_principals p
            WHERE p.category = 'director'
              AND p.tconst IN (SELECT tconst FROM batch_movies)
            GROUP BY p.tconst
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
        director_history_rows AS (
            SELECT
                bm.tconst AS target_tconst,
                bm.target_genres,
                p.tconst AS prior_tconst,
                TRY_CAST(b.startYear AS INTEGER) AS prior_year,
                b.genres AS prior_genres,
                TRY_CAST(r.averageRating AS DOUBLE) AS rating,
                ROW_NUMBER() OVER (
                    PARTITION BY bm.tconst
                    ORDER BY TRY_CAST(b.startYear AS INTEGER) DESC, p.tconst DESC
                ) AS recent_rank
            FROM batch_movies bm
            JOIN first_director fd ON fd.tconst = bm.tconst
            JOIN title_principals p
              ON p.nconst = fd.nconst
             AND p.category = 'director'
            JOIN title_basics b ON b.tconst = p.tconst
            JOIN title_ratings r ON r.tconst = p.tconst
            WHERE b.titleType = 'movie'
              AND TRY_CAST(b.startYear AS INTEGER) < bm.target_year
              AND TRY_CAST(r.averageRating AS DOUBLE) IS NOT NULL
        ),
        director_context AS (
            SELECT
                target_tconst,
                AVG(CASE
                    WHEN list_has_any(
                        string_split(COALESCE(prior_genres, ''), ','),
                        string_split(COALESCE(target_genres, ''), ',')
                    )
                    THEN rating
                END) AS genre_avg_rating,
                COUNT(DISTINCT CASE
                    WHEN list_has_any(
                        string_split(COALESCE(prior_genres, ''), ','),
                        string_split(COALESCE(target_genres, ''), ',')
                    )
                    THEN prior_tconst
                END) AS genre_prior_count,
                AVG(CASE WHEN recent_rank <= {RECENT_WORKS_LIMIT} THEN rating END)
                    AS recent_avg_rating
            FROM director_history_rows
            GROUP BY target_tconst
        ),
        writer_history_rows AS (
            SELECT
                bm.tconst AS target_tconst,
                bm.target_genres,
                tw.tconst AS prior_tconst,
                TRY_CAST(b.startYear AS INTEGER) AS prior_year,
                b.genres AS prior_genres,
                TRY_CAST(r.averageRating AS DOUBLE) AS rating,
                ROW_NUMBER() OVER (
                    PARTITION BY bm.tconst
                    ORDER BY TRY_CAST(b.startYear AS INTEGER) DESC, tw.tconst DESC
                ) AS recent_rank
            FROM batch_movies bm
            JOIN first_writer fw ON fw.tconst = bm.tconst
            JOIN title_writers tw ON tw.nconst = fw.nconst
            JOIN title_basics b ON b.tconst = tw.tconst
            JOIN title_ratings r ON r.tconst = tw.tconst
            WHERE b.titleType = 'movie'
              AND TRY_CAST(b.startYear AS INTEGER) < bm.target_year
              AND TRY_CAST(r.averageRating AS DOUBLE) IS NOT NULL
        ),
        writer_context AS (
            SELECT
                target_tconst,
                AVG(CASE
                    WHEN list_has_any(
                        string_split(COALESCE(prior_genres, ''), ','),
                        string_split(COALESCE(target_genres, ''), ',')
                    )
                    THEN rating
                END) AS genre_avg_rating,
                COUNT(DISTINCT CASE
                    WHEN list_has_any(
                        string_split(COALESCE(prior_genres, ''), ','),
                        string_split(COALESCE(target_genres, ''), ',')
                    )
                    THEN prior_tconst
                END) AS genre_prior_count,
                AVG(CASE WHEN recent_rank <= {RECENT_WORKS_LIMIT} THEN rating END)
                    AS recent_avg_rating
            FROM writer_history_rows
            GROUP BY target_tconst
        )
        SELECT
            bm.tconst,
            COALESCE(dc.genre_avg_rating, 6.5) AS director_genre_avg_rating,
            COALESCE(dc.genre_prior_count, 0) AS director_genre_prior_count,
            COALESCE(dc.recent_avg_rating, 6.5) AS director_recent_avg_rating,
            COALESCE(wc.genre_avg_rating, 6.5) AS writer_genre_avg_rating,
            COALESCE(wc.genre_prior_count, 0) AS writer_genre_prior_count,
            COALESCE(wc.recent_avg_rating, 6.5) AS writer_recent_avg_rating,
            CASE
                WHEN fd.nconst IS NOT NULL
                 AND fw.nconst IS NOT NULL
                 AND fd.nconst = fw.nconst
                THEN 1.0 ELSE 0.0
            END AS director_is_writer
        FROM batch_movies bm
        LEFT JOIN first_director fd ON fd.tconst = bm.tconst
        LEFT JOIN first_writer fw ON fw.tconst = bm.tconst
        LEFT JOIN director_context dc ON dc.target_tconst = bm.tconst
        LEFT JOIN writer_context wc ON wc.target_tconst = bm.tconst
    """

    rows = conn.execute(query, [ids]).fetchall()
    result: dict[str, dict[str, float]] = {}
    for row in rows:
        result[str(row[0])] = {
            "director_genre_avg_rating": float(row[1]),
            "director_genre_prior_count": float(row[2]),
            "director_recent_avg_rating": float(row[3]),
            "writer_genre_avg_rating": float(row[4]),
            "writer_genre_prior_count": float(row[5]),
            "writer_recent_avg_rating": float(row[6]),
            "director_is_writer": float(row[7]),
        }
    return result


def fetch_person_creative_context(
    conn: duckdb.DuckDBPyConnection,
    *,
    nconst: str | None,
    before_year: int,
    role: str,
    genres: str | Sequence[str] | None,
) -> dict[str, float]:
    """Возвращает genre/recent history одной персоны для inference.

    Семантика совпадает с batch training: только фильмы с годом строго меньше
    ``before_year``, та же role-specific история и тот же лимит recent works.
    """
    person_id = str(nconst or "").strip()
    if not person_id or person_id == "Unknown":
        return empty_creative_context()
    if role not in {"director", "writer"}:
        raise ValueError("role должен быть director или writer")

    target_genres = _genres_value(genres)
    if role == "writer":
        credits = "title_writers c"
        role_filter = ""
    else:
        credits = "title_principals c"
        role_filter = "AND c.category = 'director'"

    query = f"""
        WITH history AS (
            SELECT
                c.tconst,
                TRY_CAST(b.startYear AS INTEGER) AS prior_year,
                b.genres,
                TRY_CAST(r.averageRating AS DOUBLE) AS rating,
                ROW_NUMBER() OVER (
                    ORDER BY TRY_CAST(b.startYear AS INTEGER) DESC, c.tconst DESC
                ) AS recent_rank
            FROM {credits}
            JOIN title_basics b ON b.tconst = c.tconst
            JOIN title_ratings r ON r.tconst = c.tconst
            WHERE c.nconst = ?
              {role_filter}
              AND b.titleType = 'movie'
              AND TRY_CAST(b.startYear AS INTEGER) < ?
              AND TRY_CAST(r.averageRating AS DOUBLE) IS NOT NULL
        )
        SELECT
            COALESCE(AVG(CASE
                WHEN list_has_any(
                    string_split(COALESCE(genres, ''), ','),
                    string_split(?, ',')
                )
                THEN rating
            END), 6.5) AS genre_avg_rating,
            COUNT(DISTINCT CASE
                WHEN list_has_any(
                    string_split(COALESCE(genres, ''), ','),
                    string_split(?, ',')
                )
                THEN tconst
            END) AS genre_prior_count,
            COALESCE(
                AVG(CASE WHEN recent_rank <= {RECENT_WORKS_LIMIT} THEN rating END),
                6.5
            ) AS recent_avg_rating
        FROM history
    """
    row = conn.execute(
        query,
        [person_id, int(before_year), target_genres, target_genres],
    ).fetchone()
    if not row:
        return empty_creative_context()
    return {
        "genre_avg_rating": float(row[0]),
        "genre_prior_count": float(row[1]),
        "recent_avg_rating": float(row[2]),
    }
