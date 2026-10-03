from __future__ import annotations

import os
from typing import Iterable

import duckdb


TREND_WINDOW_SIZE = 3
TREND_REQUIRED_WORKS = TREND_WINDOW_SIZE * 2
CREATIVE_TREND_FEATURE_NAMES = (
    "director_recent_trend",
    "director_recent_trend_known",
    "writer_recent_trend",
    "writer_recent_trend_known",
)


def creative_trend_features_enabled() -> bool:
    """Возвращает режим trend-признаков для воспроизводимого ablation v9→v10."""
    raw = str(os.getenv("VANGA_TRAIN_CREATIVE_TREND_FEATURES", "1")).strip().casefold()
    return raw not in {"0", "false", "no", "off"}


def empty_trend_context() -> dict[str, float]:
    """Нулевой trend не считается реальным без known=1."""
    return {"trend": 0.0, "known": 0.0}


def fetch_batch_creative_trend_context(
    conn: duckdb.DuckDBPyConnection,
    tconsts: Iterable[str],
) -> dict[str, dict[str, float]]:
    """Считает recent trend режиссёра и сценариста только по прошлым работам.

    Trend = среднее трёх наиболее свежих прошлых фильмов минус среднее трёх
    предыдущих. Значение публикуется только при наличии всех шести работ;
    иначе trend=0 и ``*_trend_known=0``. Это не смешивает реальный ровный тренд
    с нехваткой истории.
    """
    ids = [str(value).strip() for value in tconsts if str(value).strip()]
    if not ids:
        return {}

    query = f"""
        WITH batch_movies AS (
            SELECT
                b.tconst,
                TRY_CAST(b.startYear AS INTEGER) AS target_year
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
        director_history_base AS (
            SELECT DISTINCT
                bm.tconst AS target_tconst,
                p.tconst AS prior_tconst,
                TRY_CAST(b.startYear AS INTEGER) AS prior_year,
                TRY_CAST(r.averageRating AS DOUBLE) AS rating
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
        director_ranked AS (
            SELECT
                *,
                ROW_NUMBER() OVER (
                    PARTITION BY target_tconst
                    ORDER BY prior_year DESC, prior_tconst DESC
                ) AS rn
            FROM director_history_base
        ),
        director_stats AS (
            SELECT
                target_tconst,
                AVG(CASE WHEN rn BETWEEN 1 AND {TREND_WINDOW_SIZE} THEN rating END)
                    AS recent_avg,
                AVG(CASE WHEN rn BETWEEN {TREND_WINDOW_SIZE + 1} AND {TREND_REQUIRED_WORKS} THEN rating END)
                    AS previous_avg,
                COUNT(CASE WHEN rn BETWEEN 1 AND {TREND_WINDOW_SIZE} THEN 1 END)
                    AS recent_count,
                COUNT(CASE WHEN rn BETWEEN {TREND_WINDOW_SIZE + 1} AND {TREND_REQUIRED_WORKS} THEN 1 END)
                    AS previous_count
            FROM director_ranked
            WHERE rn <= {TREND_REQUIRED_WORKS}
            GROUP BY target_tconst
        ),
        writer_history_base AS (
            SELECT DISTINCT
                bm.tconst AS target_tconst,
                tw.tconst AS prior_tconst,
                TRY_CAST(b.startYear AS INTEGER) AS prior_year,
                TRY_CAST(r.averageRating AS DOUBLE) AS rating
            FROM batch_movies bm
            JOIN first_writer fw ON fw.tconst = bm.tconst
            JOIN title_writers tw ON tw.nconst = fw.nconst
            JOIN title_basics b ON b.tconst = tw.tconst
            JOIN title_ratings r ON r.tconst = tw.tconst
            WHERE b.titleType = 'movie'
              AND TRY_CAST(b.startYear AS INTEGER) < bm.target_year
              AND TRY_CAST(r.averageRating AS DOUBLE) IS NOT NULL
        ),
        writer_ranked AS (
            SELECT
                *,
                ROW_NUMBER() OVER (
                    PARTITION BY target_tconst
                    ORDER BY prior_year DESC, prior_tconst DESC
                ) AS rn
            FROM writer_history_base
        ),
        writer_stats AS (
            SELECT
                target_tconst,
                AVG(CASE WHEN rn BETWEEN 1 AND {TREND_WINDOW_SIZE} THEN rating END)
                    AS recent_avg,
                AVG(CASE WHEN rn BETWEEN {TREND_WINDOW_SIZE + 1} AND {TREND_REQUIRED_WORKS} THEN rating END)
                    AS previous_avg,
                COUNT(CASE WHEN rn BETWEEN 1 AND {TREND_WINDOW_SIZE} THEN 1 END)
                    AS recent_count,
                COUNT(CASE WHEN rn BETWEEN {TREND_WINDOW_SIZE + 1} AND {TREND_REQUIRED_WORKS} THEN 1 END)
                    AS previous_count
            FROM writer_ranked
            WHERE rn <= {TREND_REQUIRED_WORKS}
            GROUP BY target_tconst
        )
        SELECT
            bm.tconst,
            CASE
                WHEN ds.recent_count = {TREND_WINDOW_SIZE}
                 AND ds.previous_count = {TREND_WINDOW_SIZE}
                THEN ds.recent_avg - ds.previous_avg
                ELSE 0.0
            END AS director_trend,
            CASE
                WHEN ds.recent_count = {TREND_WINDOW_SIZE}
                 AND ds.previous_count = {TREND_WINDOW_SIZE}
                THEN 1.0 ELSE 0.0
            END AS director_trend_known,
            CASE
                WHEN ws.recent_count = {TREND_WINDOW_SIZE}
                 AND ws.previous_count = {TREND_WINDOW_SIZE}
                THEN ws.recent_avg - ws.previous_avg
                ELSE 0.0
            END AS writer_trend,
            CASE
                WHEN ws.recent_count = {TREND_WINDOW_SIZE}
                 AND ws.previous_count = {TREND_WINDOW_SIZE}
                THEN 1.0 ELSE 0.0
            END AS writer_trend_known
        FROM batch_movies bm
        LEFT JOIN director_stats ds ON ds.target_tconst = bm.tconst
        LEFT JOIN writer_stats ws ON ws.target_tconst = bm.tconst
    """

    rows = conn.execute(query, [ids]).fetchall()
    return {
        str(row[0]): {
            "director_recent_trend": float(row[1] or 0.0),
            "director_recent_trend_known": float(row[2] or 0.0),
            "writer_recent_trend": float(row[3] or 0.0),
            "writer_recent_trend_known": float(row[4] or 0.0),
        }
        for row in rows
    }


def fetch_person_recent_trend(
    conn: duckdb.DuckDBPyConnection,
    *,
    nconst: str | None,
    before_year: int,
    role: str,
) -> dict[str, float]:
    """Возвращает такой же 3-vs-3 trend для одного человека на inference."""
    person_id = str(nconst or "").strip()
    if not person_id or person_id == "Unknown":
        return empty_trend_context()
    if role not in {"director", "writer"}:
        raise ValueError("role должен быть director или writer")

    if role == "writer":
        credits = "title_writers c"
        role_filter = ""
    else:
        credits = "title_principals c"
        role_filter = "AND c.category = 'director'"

    query = f"""
        WITH history_base AS (
            SELECT DISTINCT
                c.tconst,
                TRY_CAST(b.startYear AS INTEGER) AS prior_year,
                TRY_CAST(r.averageRating AS DOUBLE) AS rating
            FROM {credits}
            JOIN title_basics b ON b.tconst = c.tconst
            JOIN title_ratings r ON r.tconst = c.tconst
            WHERE c.nconst = ?
              {role_filter}
              AND b.titleType = 'movie'
              AND TRY_CAST(b.startYear AS INTEGER) < ?
              AND TRY_CAST(r.averageRating AS DOUBLE) IS NOT NULL
        ),
        ranked AS (
            SELECT
                *,
                ROW_NUMBER() OVER (
                    ORDER BY prior_year DESC, tconst DESC
                ) AS rn
            FROM history_base
        ),
        stats AS (
            SELECT
                AVG(CASE WHEN rn BETWEEN 1 AND {TREND_WINDOW_SIZE} THEN rating END)
                    AS recent_avg,
                AVG(CASE WHEN rn BETWEEN {TREND_WINDOW_SIZE + 1} AND {TREND_REQUIRED_WORKS} THEN rating END)
                    AS previous_avg,
                COUNT(CASE WHEN rn BETWEEN 1 AND {TREND_WINDOW_SIZE} THEN 1 END)
                    AS recent_count,
                COUNT(CASE WHEN rn BETWEEN {TREND_WINDOW_SIZE + 1} AND {TREND_REQUIRED_WORKS} THEN 1 END)
                    AS previous_count
            FROM ranked
            WHERE rn <= {TREND_REQUIRED_WORKS}
        )
        SELECT recent_avg, previous_avg, recent_count, previous_count
        FROM stats
    """
    row = conn.execute(query, [person_id, int(before_year)]).fetchone()
    if not row:
        return empty_trend_context()

    recent_avg, previous_avg, recent_count, previous_count = row
    if (
        int(recent_count or 0) != TREND_WINDOW_SIZE
        or int(previous_count or 0) != TREND_WINDOW_SIZE
    ):
        return empty_trend_context()
    return {
        "trend": float(recent_avg) - float(previous_avg),
        "known": 1.0,
    }
