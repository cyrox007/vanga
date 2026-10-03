from __future__ import annotations

import os
from typing import Iterable

import duckdb


DIRECTOR_WRITER_PAIR_FEATURE_NAMES = (
    "director_writer_pair_avg_rating",
    "director_writer_pair_count",
    "director_writer_pair_known",
)


def director_writer_pair_features_enabled() -> bool:
    """Возвращает режим pair-признаков для воспроизводимого ablation v7→v8."""
    raw = str(
        os.getenv("VANGA_TRAIN_DIRECTOR_WRITER_PAIR_FEATURES", "1")
    ).strip().casefold()
    return raw not in {"0", "false", "no", "off"}


def empty_director_writer_pair_context() -> dict[str, float]:
    """Возвращает явное состояние отсутствия прошлой совместной работы."""
    return {
        "director_writer_pair_avg_rating": 6.5,
        "director_writer_pair_count": 0.0,
        "director_writer_pair_known": 0.0,
    }


def fetch_batch_director_writer_pair_context(
    conn: duckdb.DuckDBPyConnection,
    tconsts: Iterable[str],
) -> dict[str, dict[str, float]]:
    """Считает историю пары режиссёр-сценарист до года target-фильма.

    Target-пара соответствует текущему контракту модели: первый режиссёр по
    ordering и первый сценарист из title_crew.writers. В исторических фильмах
    достаточно любого director-credit выбранного режиссёра и writer-credit
    выбранного сценариста: это отражает сам факт их предыдущей совместной работы.

    Рейтинг target-фильма и фильмы того же/будущего года не используются.
    Числовой fallback 6.5 нужен только для CatBoost; pair_count/pair_known явно
    показывают, была ли у пары историческая выборка.
    """
    ids = [str(value).strip() for value in tconsts if str(value).strip()]
    if not ids:
        return {}

    query = """
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
        pair_history AS (
            SELECT DISTINCT
                bm.tconst AS target_tconst,
                hp.tconst AS prior_tconst,
                TRY_CAST(r.averageRating AS DOUBLE) AS rating
            FROM batch_movies bm
            JOIN first_director fd ON fd.tconst = bm.tconst
            JOIN first_writer fw ON fw.tconst = bm.tconst
            JOIN title_principals hp
              ON hp.nconst = fd.nconst
             AND hp.category = 'director'
            JOIN title_writers hw
              ON hw.tconst = hp.tconst
             AND hw.nconst = fw.nconst
            JOIN title_basics hb ON hb.tconst = hp.tconst
            JOIN title_ratings r ON r.tconst = hp.tconst
            WHERE hb.titleType = 'movie'
              AND TRY_CAST(hb.startYear AS INTEGER) < bm.target_year
              AND TRY_CAST(r.averageRating AS DOUBLE) IS NOT NULL
        ),
        pair_stats AS (
            SELECT
                target_tconst,
                AVG(rating) AS avg_rating,
                COUNT(*) AS prior_count
            FROM pair_history
            GROUP BY target_tconst
        )
        SELECT
            bm.tconst,
            COALESCE(ps.avg_rating, 6.5) AS pair_avg_rating,
            COALESCE(ps.prior_count, 0) AS pair_count
        FROM batch_movies bm
        LEFT JOIN pair_stats ps ON ps.target_tconst = bm.tconst
    """

    rows = conn.execute(query, [ids]).fetchall()
    result: dict[str, dict[str, float]] = {}
    for tconst, avg_rating, pair_count in rows:
        count = float(pair_count or 0)
        result[str(tconst)] = {
            "director_writer_pair_avg_rating": float(avg_rating),
            "director_writer_pair_count": count,
            "director_writer_pair_known": 1.0 if count > 0 else 0.0,
        }
    return result


def fetch_director_writer_pair_context(
    conn: duckdb.DuckDBPyConnection,
    *,
    director_nconst: str | None,
    writer_nconst: str | None,
    before_year: int,
) -> dict[str, float]:
    """Возвращает pair history для inference с той же temporal-семантикой."""
    director_id = str(director_nconst or "").strip()
    writer_id = str(writer_nconst or "").strip()
    if (
        not director_id
        or not writer_id
        or director_id == "Unknown"
        or writer_id == "Unknown"
    ):
        return empty_director_writer_pair_context()

    query = """
        WITH pair_history AS (
            SELECT DISTINCT
                p.tconst,
                TRY_CAST(r.averageRating AS DOUBLE) AS rating
            FROM title_principals p
            JOIN title_writers w
              ON w.tconst = p.tconst
             AND w.nconst = ?
            JOIN title_basics b ON b.tconst = p.tconst
            JOIN title_ratings r ON r.tconst = p.tconst
            WHERE p.nconst = ?
              AND p.category = 'director'
              AND b.titleType = 'movie'
              AND TRY_CAST(b.startYear AS INTEGER) < ?
              AND TRY_CAST(r.averageRating AS DOUBLE) IS NOT NULL
        )
        SELECT
            COALESCE(AVG(rating), 6.5) AS avg_rating,
            COUNT(*) AS prior_count
        FROM pair_history
    """
    row = conn.execute(
        query,
        [writer_id, director_id, int(before_year)],
    ).fetchone()
    if not row:
        return empty_director_writer_pair_context()

    count = float(row[1] or 0)
    return {
        "director_writer_pair_avg_rating": float(row[0]),
        "director_writer_pair_count": count,
        "director_writer_pair_known": 1.0 if count > 0 else 0.0,
    }
