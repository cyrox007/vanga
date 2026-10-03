from __future__ import annotations

import os
from typing import Iterable

import duckdb


ACTOR_SLOTS = (1, 2, 3)
DIRECTOR_ACTOR_PAIR_FEATURE_NAMES = tuple(
    name
    for slot in ACTOR_SLOTS
    for name in (
        f"director_actor_{slot}_pair_avg_rating",
        f"director_actor_{slot}_pair_count",
        f"director_actor_{slot}_pair_known",
    )
)


def director_actor_pair_features_enabled() -> bool:
    """Возвращает режим director↔actor-признаков для ablation v8→v9."""
    raw = str(
        os.getenv("VANGA_TRAIN_DIRECTOR_ACTOR_PAIR_FEATURES", "1")
    ).strip().casefold()
    return raw not in {"0", "false", "no", "off"}


def empty_director_actor_pair_context() -> dict[str, float]:
    """Возвращает явное состояние отсутствия истории одной пары."""
    return {
        "avg_rating": 6.5,
        "count": 0.0,
        "known": 0.0,
    }


def _empty_batch_row() -> dict[str, float]:
    result: dict[str, float] = {}
    for slot in ACTOR_SLOTS:
        result[f"director_actor_{slot}_pair_avg_rating"] = 6.5
        result[f"director_actor_{slot}_pair_count"] = 0.0
        result[f"director_actor_{slot}_pair_known"] = 0.0
    return result


def fetch_batch_director_actor_pair_context(
    conn: duckdb.DuckDBPyConnection,
    tconsts: Iterable[str],
) -> dict[str, dict[str, float]]:
    """Считает прошлые collaboration-пары режиссёра с первыми 3 актёрами.

    Target-режиссёр совпадает с текущим контрактом модели: первый director по
    IMDb ordering. Target-актёры — первые три actor/actress по ordering, ровно как
    в ``src.data_filtr``. Исторический фильм учитывается, если тот же режиссёр
    имеет director-credit, а выбранный актёр — actor/actress-credit.

    Используются только фильмы с ``startYear < target_year``. Среднее и count
    считаются по уникальным прошлым фильмам, поэтому дубли credits не искажают
    pair average.
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
        actor_ranked AS (
            SELECT tconst, nconst, rn
            FROM (
                SELECT
                    p.tconst,
                    p.nconst,
                    ROW_NUMBER() OVER (
                        PARTITION BY p.tconst
                        ORDER BY p.ordering
                    ) AS rn
                FROM title_principals p
                WHERE p.category IN ('actor', 'actress')
                  AND p.tconst IN (SELECT tconst FROM batch_movies)
            )
            WHERE rn <= 3
        ),
        pair_history AS (
            SELECT DISTINCT
                bm.tconst AS target_tconst,
                ar.rn,
                dp.tconst AS prior_tconst,
                TRY_CAST(r.averageRating AS DOUBLE) AS rating
            FROM batch_movies bm
            JOIN first_director fd ON fd.tconst = bm.tconst
            JOIN actor_ranked ar ON ar.tconst = bm.tconst
            JOIN title_principals dp
              ON dp.nconst = fd.nconst
             AND dp.category = 'director'
            JOIN title_principals ap
              ON ap.tconst = dp.tconst
             AND ap.nconst = ar.nconst
             AND ap.category IN ('actor', 'actress')
            JOIN title_basics hb ON hb.tconst = dp.tconst
            JOIN title_ratings r ON r.tconst = dp.tconst
            WHERE hb.titleType = 'movie'
              AND TRY_CAST(hb.startYear AS INTEGER) < bm.target_year
              AND TRY_CAST(r.averageRating AS DOUBLE) IS NOT NULL
        )
        SELECT
            target_tconst,
            rn,
            AVG(rating) AS avg_rating,
            COUNT(*) AS prior_count
        FROM pair_history
        GROUP BY target_tconst, rn
    """

    result = {tconst: _empty_batch_row() for tconst in ids}
    rows = conn.execute(query, [ids]).fetchall()
    for tconst, slot, avg_rating, prior_count in rows:
        slot_num = int(slot)
        if slot_num not in ACTOR_SLOTS:
            continue
        count = float(prior_count or 0)
        row = result.setdefault(str(tconst), _empty_batch_row())
        row[f"director_actor_{slot_num}_pair_avg_rating"] = float(avg_rating)
        row[f"director_actor_{slot_num}_pair_count"] = count
        row[f"director_actor_{slot_num}_pair_known"] = 1.0 if count > 0 else 0.0
    return result


def fetch_director_actor_pair_context(
    conn: duckdb.DuckDBPyConnection,
    *,
    director_nconst: str | None,
    actor_nconst: str | None,
    before_year: int,
) -> dict[str, float]:
    """Возвращает историю одной пары director↔actor для inference."""
    director_id = str(director_nconst or "").strip()
    actor_id = str(actor_nconst or "").strip()
    if (
        not director_id
        or not actor_id
        or director_id == "Unknown"
        or actor_id == "Unknown"
    ):
        return empty_director_actor_pair_context()

    query = """
        WITH pair_history AS (
            SELECT DISTINCT
                dp.tconst,
                TRY_CAST(r.averageRating AS DOUBLE) AS rating
            FROM title_principals dp
            JOIN title_principals ap
              ON ap.tconst = dp.tconst
             AND ap.nconst = ?
             AND ap.category IN ('actor', 'actress')
            JOIN title_basics b ON b.tconst = dp.tconst
            JOIN title_ratings r ON r.tconst = dp.tconst
            WHERE dp.nconst = ?
              AND dp.category = 'director'
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
        [actor_id, director_id, int(before_year)],
    ).fetchone()
    if not row:
        return empty_director_actor_pair_context()

    count = float(row[1] or 0)
    return {
        "avg_rating": float(row[0]),
        "count": count,
        "known": 1.0 if count > 0 else 0.0,
    }
