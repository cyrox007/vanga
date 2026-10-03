from __future__ import annotations

import os
import statistics
from collections import defaultdict
from typing import Iterable, Sequence

import duckdb


CAST_PAIR_FEATURE_NAMES = (
    "cast_pair_total",
    "cast_pair_known_ratio",
    "cast_pair_prior_collaboration_mean",
    "cast_pair_prior_collaboration_median",
    "cast_pair_prior_collaboration_max",
    "cast_pair_prior_rating_avg",
    "cast_pair_prior_rating_median",
    "cast_pair_prior_rating_std",
)


def cast_pair_features_enabled() -> bool:
    """Возвращает режим actor↔actor history для воспроизводимого ablation v12→v13."""
    raw = str(os.getenv("VANGA_TRAIN_CAST_PAIR_FEATURES", "1")).strip().casefold()
    return raw not in {"0", "false", "no", "off"}


def empty_cast_pair_context(pair_total: int = 0) -> dict[str, float]:
    return {
        "cast_pair_total": float(max(0, pair_total)),
        "cast_pair_known_ratio": 0.0,
        "cast_pair_prior_collaboration_mean": 0.0,
        "cast_pair_prior_collaboration_median": 0.0,
        "cast_pair_prior_collaboration_max": 0.0,
        "cast_pair_prior_rating_avg": 6.5,
        "cast_pair_prior_rating_median": 6.5,
        "cast_pair_prior_rating_std": 0.0,
    }


def _aggregate_pair_rows(
    rows: Sequence[tuple[str, str, float, float]],
    *,
    pair_total_override: int | None = None,
) -> dict[str, float]:
    """Сворачивает pair-level history, сохраняя нулевые неизвестные пары."""
    pair_total = int(pair_total_override) if pair_total_override is not None else len(rows)
    if pair_total <= 0:
        return empty_cast_pair_context(0)

    counts = [float(row[2]) for row in rows]
    if len(counts) < pair_total:
        counts.extend([0.0] * (pair_total - len(counts)))
    known_ratings = [float(row[3]) for row in rows if float(row[2]) > 0]
    known_count = sum(1 for value in counts if value > 0)

    return {
        "cast_pair_total": float(pair_total),
        "cast_pair_known_ratio": float(known_count) / float(pair_total),
        "cast_pair_prior_collaboration_mean": float(statistics.fmean(counts)),
        "cast_pair_prior_collaboration_median": float(statistics.median(counts)),
        "cast_pair_prior_collaboration_max": float(max(counts, default=0.0)),
        "cast_pair_prior_rating_avg": (
            float(statistics.fmean(known_ratings)) if known_ratings else 6.5
        ),
        "cast_pair_prior_rating_median": (
            float(statistics.median(known_ratings)) if known_ratings else 6.5
        ),
        "cast_pair_prior_rating_std": (
            float(statistics.pstdev(known_ratings)) if len(known_ratings) > 1 else 0.0
        ),
    }


def fetch_batch_cast_pair_context(
    conn: duckdb.DuckDBPyConnection,
    tconsts: Iterable[str],
) -> dict[str, dict[str, float]]:
    """Считает историю всех пар principal cast без target/future leakage."""
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
        target_cast AS (
            SELECT DISTINCT
                bm.tconst AS target_tconst,
                bm.target_year,
                p.nconst
            FROM batch_movies bm
            JOIN title_principals p
              ON p.tconst = bm.tconst
             AND p.category IN ('actor', 'actress')
        ),
        target_pairs AS (
            SELECT
                a.target_tconst,
                a.target_year,
                a.nconst AS actor_a,
                b.nconst AS actor_b
            FROM target_cast a
            JOIN target_cast b
              ON b.target_tconst = a.target_tconst
             AND b.nconst > a.nconst
        ),
        pair_history AS (
            SELECT DISTINCT
                tp.target_tconst,
                tp.actor_a,
                tp.actor_b,
                p1.tconst AS prior_tconst,
                TRY_CAST(r.averageRating AS DOUBLE) AS rating
            FROM target_pairs tp
            JOIN title_principals p1
              ON p1.nconst = tp.actor_a
             AND p1.category IN ('actor', 'actress')
            JOIN title_principals p2
              ON p2.tconst = p1.tconst
             AND p2.nconst = tp.actor_b
             AND p2.category IN ('actor', 'actress')
            JOIN title_basics hb ON hb.tconst = p1.tconst
            JOIN title_ratings r ON r.tconst = p1.tconst
            WHERE hb.titleType = 'movie'
              AND TRY_CAST(hb.startYear AS INTEGER) < tp.target_year
              AND TRY_CAST(r.averageRating AS DOUBLE) IS NOT NULL
        ),
        pair_stats AS (
            SELECT
                target_tconst,
                actor_a,
                actor_b,
                COUNT(DISTINCT prior_tconst) AS collaboration_count,
                AVG(rating) AS collaboration_avg_rating
            FROM pair_history
            GROUP BY target_tconst, actor_a, actor_b
        )
        SELECT
            tp.target_tconst,
            tp.actor_a,
            tp.actor_b,
            COALESCE(ps.collaboration_count, 0),
            COALESCE(ps.collaboration_avg_rating, 6.5)
        FROM target_pairs tp
        LEFT JOIN pair_stats ps
          ON ps.target_tconst = tp.target_tconst
         AND ps.actor_a = tp.actor_a
         AND ps.actor_b = tp.actor_b
        ORDER BY tp.target_tconst, tp.actor_a, tp.actor_b
    """

    grouped: dict[str, list[tuple[str, str, float, float]]] = defaultdict(list)
    for row in conn.execute(query, [ids]).fetchall():
        grouped[str(row[0])].append(
            (str(row[1]), str(row[2]), float(row[3]), float(row[4]))
        )

    return {
        target: _aggregate_pair_rows(grouped.get(target, []))
        for target in ids
    }


def fetch_cast_pair_context(
    conn: duckdb.DuckDBPyConnection,
    *,
    actor_nconsts: Sequence[str | None] | None,
    before_year: int,
) -> dict[str, float]:
    """Inference-версия actor↔actor history с тем же strict temporal cutoff."""
    raw_ids = [str(value or "").strip() for value in (actor_nconsts or [])]
    total_people = len(raw_ids)
    pair_total = total_people * (total_people - 1) // 2
    if pair_total <= 0:
        return empty_cast_pair_context(pair_total)

    valid_ids: list[str] = []
    for value in raw_ids:
        if value and value != "Unknown" and value not in valid_ids:
            valid_ids.append(value)

    rows: list[tuple[str, str, float, float]] = []
    if len(valid_ids) >= 2:
        query = """
            WITH requested AS (
                SELECT nconst FROM UNNEST(?) AS t(nconst)
            ),
            pairs AS (
                SELECT a.nconst AS actor_a, b.nconst AS actor_b
                FROM requested a
                JOIN requested b ON b.nconst > a.nconst
            ),
            pair_history AS (
                SELECT DISTINCT
                    p.actor_a,
                    p.actor_b,
                    p1.tconst AS prior_tconst,
                    TRY_CAST(r.averageRating AS DOUBLE) AS rating
                FROM pairs p
                JOIN title_principals p1
                  ON p1.nconst = p.actor_a
                 AND p1.category IN ('actor', 'actress')
                JOIN title_principals p2
                  ON p2.tconst = p1.tconst
                 AND p2.nconst = p.actor_b
                 AND p2.category IN ('actor', 'actress')
                JOIN title_basics b ON b.tconst = p1.tconst
                JOIN title_ratings r ON r.tconst = p1.tconst
                WHERE b.titleType = 'movie'
                  AND TRY_CAST(b.startYear AS INTEGER) < ?
                  AND TRY_CAST(r.averageRating AS DOUBLE) IS NOT NULL
            ),
            stats AS (
                SELECT
                    actor_a,
                    actor_b,
                    COUNT(DISTINCT prior_tconst) AS collaboration_count,
                    AVG(rating) AS collaboration_avg_rating
                FROM pair_history
                GROUP BY actor_a, actor_b
            )
            SELECT
                p.actor_a,
                p.actor_b,
                COALESCE(s.collaboration_count, 0),
                COALESCE(s.collaboration_avg_rating, 6.5)
            FROM pairs p
            LEFT JOIN stats s USING (actor_a, actor_b)
            ORDER BY p.actor_a, p.actor_b
        """
        for row in conn.execute(query, [valid_ids, int(before_year)]).fetchall():
            rows.append((str(row[0]), str(row[1]), float(row[2]), float(row[3])))

    # pair_total считается по всему переданному cast, поэтому пары с Unknown
    # автоматически добавляются как нулевые через pair_total_override.
    return _aggregate_pair_rows(rows, pair_total_override=pair_total)
