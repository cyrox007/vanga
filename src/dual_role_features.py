from __future__ import annotations

import os
import statistics
from typing import Iterable, Sequence

import duckdb


DUAL_ROLE_FEATURE_NAMES = (
    "director_team_dual_role_known_ratio",
    "director_team_dual_role_avg_rating",
    "director_team_dual_role_prior_count_mean",
    "director_team_dual_role_prior_count_max",
    "writer_dual_role_avg_rating",
    "writer_dual_role_prior_count",
    "writer_dual_role_known",
    "writer_is_in_director_team",
)


def dual_role_features_enabled() -> bool:
    """Возвращает режим dual-role блока для воспроизводимого ablation v13→v14."""
    raw = str(os.getenv("VANGA_TRAIN_DUAL_ROLE_FEATURES", "1")).strip().casefold()
    return raw not in {"0", "false", "no", "off"}


def empty_dual_role_context(director_count: int = 0) -> dict[str, float]:
    """Возвращает явное отсутствие director+writer history без ложной репутации."""
    return {
        "director_team_dual_role_known_ratio": 0.0,
        "director_team_dual_role_avg_rating": 6.5,
        "director_team_dual_role_prior_count_mean": 0.0,
        "director_team_dual_role_prior_count_max": 0.0,
        "writer_dual_role_avg_rating": 6.5,
        "writer_dual_role_prior_count": 0.0,
        "writer_dual_role_known": 0.0,
        "writer_is_in_director_team": 0.0,
    }


def fetch_batch_dual_role_context(
    conn: duckdb.DuckDBPyConnection,
    tconsts: Iterable[str],
) -> dict[str, dict[str, float]]:
    """Считает прошлый опыт людей, которые одновременно director и writer.

    Для режиссёрской стороны используются ВСЕ director-credit target-фильма.
    Для writer сохраняется текущий контракт модели: первый writer из title_crew.
    Исторический фильм засчитывается человеку только если на одном и том же
    prior title он имеет director-credit и writer-credit. Target, same-year и
    future исключаются условием ``startYear < target_year``.
    """
    ids = [str(value).strip() for value in tconsts if str(value).strip()]
    if not ids:
        return {}

    query = """
        WITH batch_movies AS (
            SELECT b.tconst, TRY_CAST(b.startYear AS INTEGER) AS target_year
            FROM UNNEST(?) AS requested(tconst)
            JOIN title_basics b USING (tconst)
            WHERE b.titleType = 'movie'
              AND TRY_CAST(b.startYear AS INTEGER) IS NOT NULL
        ),
        target_directors AS (
            SELECT DISTINCT
                bm.tconst AS target_tconst,
                bm.target_year,
                p.nconst
            FROM batch_movies bm
            JOIN title_principals p
              ON p.tconst = bm.tconst
             AND p.category = 'director'
        ),
        first_writer AS (
            SELECT
                bm.tconst AS target_tconst,
                bm.target_year,
                NULLIF(TRIM(split_part(c.writers, ',', 1)), '') AS nconst
            FROM batch_movies bm
            LEFT JOIN title_crew c ON c.tconst = bm.tconst
            WHERE c.writers IS NOT NULL
              AND c.writers <> '\\N'
        ),
        requested_people AS (
            SELECT target_tconst, target_year, nconst, 'director' AS target_role
            FROM target_directors
            UNION ALL
            SELECT target_tconst, target_year, nconst, 'writer' AS target_role
            FROM first_writer
            WHERE nconst IS NOT NULL
        ),
        dual_history AS (
            SELECT DISTINCT
                rp.target_tconst,
                rp.target_role,
                rp.nconst,
                p.tconst AS prior_tconst,
                TRY_CAST(r.averageRating AS DOUBLE) AS rating
            FROM requested_people rp
            JOIN title_principals p
              ON p.nconst = rp.nconst
             AND p.category = 'director'
            JOIN title_writers w
              ON w.tconst = p.tconst
             AND w.nconst = rp.nconst
            JOIN title_basics b ON b.tconst = p.tconst
            JOIN title_ratings r ON r.tconst = p.tconst
            WHERE b.titleType = 'movie'
              AND TRY_CAST(b.startYear AS INTEGER) < rp.target_year
              AND TRY_CAST(r.averageRating AS DOUBLE) IS NOT NULL
        ),
        person_stats AS (
            SELECT
                target_tconst,
                target_role,
                nconst,
                AVG(rating) AS avg_rating,
                COUNT(DISTINCT prior_tconst) AS prior_count
            FROM dual_history
            GROUP BY target_tconst, target_role, nconst
        ),
        director_agg AS (
            SELECT
                td.target_tconst,
                COUNT(*) AS director_count,
                SUM(CASE WHEN COALESCE(ps.prior_count, 0) > 0 THEN 1 ELSE 0 END) AS known_count,
                AVG(CASE WHEN COALESCE(ps.prior_count, 0) > 0 THEN ps.avg_rating END) AS avg_rating,
                AVG(COALESCE(ps.prior_count, 0)) AS count_mean,
                MAX(COALESCE(ps.prior_count, 0)) AS count_max
            FROM target_directors td
            LEFT JOIN person_stats ps
              ON ps.target_tconst = td.target_tconst
             AND ps.target_role = 'director'
             AND ps.nconst = td.nconst
            GROUP BY td.target_tconst
        ),
        writer_stats AS (
            SELECT
                fw.target_tconst,
                fw.nconst,
                COALESCE(ps.avg_rating, 6.5) AS avg_rating,
                COALESCE(ps.prior_count, 0) AS prior_count
            FROM first_writer fw
            LEFT JOIN person_stats ps
              ON ps.target_tconst = fw.target_tconst
             AND ps.target_role = 'writer'
             AND ps.nconst = fw.nconst
        ),
        target_overlap AS (
            SELECT
                fw.target_tconst,
                CASE WHEN EXISTS (
                    SELECT 1
                    FROM target_directors td
                    WHERE td.target_tconst = fw.target_tconst
                      AND td.nconst = fw.nconst
                ) THEN 1.0 ELSE 0.0 END AS writer_is_director
            FROM first_writer fw
        )
        SELECT
            bm.tconst,
            CASE
                WHEN COALESCE(da.director_count, 0) > 0
                THEN CAST(COALESCE(da.known_count, 0) AS DOUBLE) / da.director_count
                ELSE 0.0
            END AS director_known_ratio,
            COALESCE(da.avg_rating, 6.5) AS director_avg_rating,
            COALESCE(da.count_mean, 0.0) AS director_count_mean,
            COALESCE(da.count_max, 0.0) AS director_count_max,
            COALESCE(ws.avg_rating, 6.5) AS writer_avg_rating,
            COALESCE(ws.prior_count, 0) AS writer_prior_count,
            COALESCE(ov.writer_is_director, 0.0) AS writer_is_director
        FROM batch_movies bm
        LEFT JOIN director_agg da ON da.target_tconst = bm.tconst
        LEFT JOIN writer_stats ws ON ws.target_tconst = bm.tconst
        LEFT JOIN target_overlap ov ON ov.target_tconst = bm.tconst
    """

    result: dict[str, dict[str, float]] = {}
    for row in conn.execute(query, [ids]).fetchall():
        writer_count = float(row[6] or 0.0)
        result[str(row[0])] = {
            "director_team_dual_role_known_ratio": float(row[1] or 0.0),
            "director_team_dual_role_avg_rating": float(row[2] if row[2] is not None else 6.5),
            "director_team_dual_role_prior_count_mean": float(row[3] or 0.0),
            "director_team_dual_role_prior_count_max": float(row[4] or 0.0),
            "writer_dual_role_avg_rating": float(row[5] if row[5] is not None else 6.5),
            "writer_dual_role_prior_count": writer_count,
            "writer_dual_role_known": 1.0 if writer_count > 0 else 0.0,
            "writer_is_in_director_team": float(row[7] or 0.0),
        }
    return result


def _person_dual_role_stats(
    conn: duckdb.DuckDBPyConnection,
    *,
    nconsts: Sequence[str],
    before_year: int,
) -> dict[str, tuple[float, float]]:
    if not nconsts:
        return {}
    query = """
        WITH requested AS (
            SELECT nconst FROM UNNEST(?) AS t(nconst)
        ),
        history AS (
            SELECT DISTINCT
                req.nconst,
                p.tconst,
                TRY_CAST(r.averageRating AS DOUBLE) AS rating
            FROM requested req
            JOIN title_principals p
              ON p.nconst = req.nconst
             AND p.category = 'director'
            JOIN title_writers w
              ON w.tconst = p.tconst
             AND w.nconst = req.nconst
            JOIN title_basics b ON b.tconst = p.tconst
            JOIN title_ratings r ON r.tconst = p.tconst
            WHERE b.titleType = 'movie'
              AND TRY_CAST(b.startYear AS INTEGER) < ?
              AND TRY_CAST(r.averageRating AS DOUBLE) IS NOT NULL
        )
        SELECT
            req.nconst,
            COALESCE(AVG(h.rating), 6.5) AS avg_rating,
            COUNT(DISTINCT h.tconst) AS prior_count
        FROM requested req
        LEFT JOIN history h USING (nconst)
        GROUP BY req.nconst
    """
    return {
        str(row[0]): (float(row[1]), float(row[2] or 0.0))
        for row in conn.execute(query, [list(nconsts), int(before_year)]).fetchall()
    }


def fetch_dual_role_context(
    conn: duckdb.DuckDBPyConnection,
    *,
    director_nconsts: Sequence[str | None] | None,
    writer_nconst: str | None,
    before_year: int,
) -> dict[str, float]:
    """Inference-версия dual-role context с multi-director coverage."""
    raw_directors = [str(value or "").strip() for value in (director_nconsts or [])]
    director_count = len(raw_directors)
    valid_directors: list[str] = []
    for value in raw_directors:
        if value and value != "Unknown" and value not in valid_directors:
            valid_directors.append(value)

    writer_id = str(writer_nconst or "").strip()
    valid_writer = writer_id if writer_id and writer_id != "Unknown" else None
    requested = list(valid_directors)
    if valid_writer and valid_writer not in requested:
        requested.append(valid_writer)
    stats = _person_dual_role_stats(
        conn,
        nconsts=requested,
        before_year=int(before_year),
    )

    director_rows = [stats.get(value, (6.5, 0.0)) for value in valid_directors]
    known_director_ratings = [avg for avg, count in director_rows if count > 0]
    director_counts = [count for _, count in director_rows]
    # Неразрешённые режиссёры входят в denominator и count mean как нули.
    missing_directors = max(0, director_count - len(valid_directors))
    director_counts.extend([0.0] * missing_directors)

    writer_avg, writer_count = stats.get(valid_writer, (6.5, 0.0)) if valid_writer else (6.5, 0.0)
    return {
        "director_team_dual_role_known_ratio": (
            float(len(known_director_ratings)) / float(director_count)
            if director_count > 0
            else 0.0
        ),
        "director_team_dual_role_avg_rating": (
            float(statistics.fmean(known_director_ratings))
            if known_director_ratings
            else 6.5
        ),
        "director_team_dual_role_prior_count_mean": (
            float(statistics.fmean(director_counts)) if director_counts else 0.0
        ),
        "director_team_dual_role_prior_count_max": float(max(director_counts, default=0.0)),
        "writer_dual_role_avg_rating": float(writer_avg),
        "writer_dual_role_prior_count": float(writer_count),
        "writer_dual_role_known": 1.0 if writer_count > 0 else 0.0,
        "writer_is_in_director_team": (
            1.0 if valid_writer and valid_writer in valid_directors else 0.0
        ),
    }
