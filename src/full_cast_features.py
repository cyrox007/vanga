from __future__ import annotations

import os
import statistics
from collections import defaultdict
from typing import Iterable, Sequence

import duckdb


FULL_CAST_FEATURE_NAMES = (
    "cast_size",
    "cast_known_ratio",
    "cast_avg_rating",
    "cast_rating_median",
    "cast_rating_std",
    "cast_rating_min",
    "cast_rating_max",
    "cast_prior_count_mean",
    "cast_prior_count_max",
    "cast_genre_known_ratio",
    "cast_genre_avg_rating",
    "cast_genre_rating_median",
    "cast_genre_rating_std",
    "cast_genre_rating_min",
    "cast_genre_rating_max",
    "cast_genre_prior_count_mean",
    "cast_genre_prior_count_max",
)


def full_cast_features_enabled() -> bool:
    """Возвращает режим Full Cast блока для воспроизводимого ablation v11→v12."""
    raw = str(os.getenv("VANGA_TRAIN_FULL_CAST_FEATURES", "1")).strip().casefold()
    return raw not in {"0", "false", "no", "off"}


def _genres_value(genres: str | Sequence[str] | None) -> str:
    if isinstance(genres, str):
        parts = [part.strip() for part in genres.split(",") if part.strip()]
    else:
        parts = [str(part).strip() for part in (genres or []) if str(part).strip()]
    return ",".join(parts)


def empty_full_cast_context(cast_size: int = 0) -> dict[str, float]:
    """Возвращает явный fallback, не выдавая 6.5 за реальную историю актёров."""
    return {
        "cast_size": float(max(0, cast_size)),
        "cast_known_ratio": 0.0,
        "cast_avg_rating": 6.5,
        "cast_rating_median": 6.5,
        "cast_rating_std": 0.0,
        "cast_rating_min": 6.5,
        "cast_rating_max": 6.5,
        "cast_prior_count_mean": 0.0,
        "cast_prior_count_max": 0.0,
        "cast_genre_known_ratio": 0.0,
        "cast_genre_avg_rating": 6.5,
        "cast_genre_rating_median": 6.5,
        "cast_genre_rating_std": 0.0,
        "cast_genre_rating_min": 6.5,
        "cast_genre_rating_max": 6.5,
        "cast_genre_prior_count_mean": 0.0,
        "cast_genre_prior_count_max": 0.0,
    }


def _distribution(values: list[float]) -> tuple[float, float, float, float, float]:
    if not values:
        return 6.5, 6.5, 0.0, 6.5, 6.5
    return (
        float(statistics.fmean(values)),
        float(statistics.median(values)),
        float(statistics.pstdev(values)) if len(values) > 1 else 0.0,
        float(min(values)),
        float(max(values)),
    )


def _aggregate_actor_rows(
    rows: Sequence[tuple[str, float, float, float, float]],
) -> dict[str, float]:
    """Сворачивает персональные истории ВСЕХ актёров в устойчивый числовой профиль.

    Одна строка: ``(nconst, overall_avg, overall_count, genre_avg, genre_count)``.
    Актёры без истории остаются в знаменателях coverage и в mean count как нули,
    но filler 6.5 не участвует в средних/медианах реальных рейтингов.
    """
    if not rows:
        return empty_full_cast_context(0)

    cast_size = len(rows)
    overall_known = [float(row[1]) for row in rows if float(row[2]) > 0]
    genre_known = [float(row[3]) for row in rows if float(row[4]) > 0]
    overall_counts = [float(row[2]) for row in rows]
    genre_counts = [float(row[4]) for row in rows]

    avg, median, std, min_rating, max_rating = _distribution(overall_known)
    g_avg, g_median, g_std, g_min, g_max = _distribution(genre_known)

    return {
        "cast_size": float(cast_size),
        "cast_known_ratio": float(len(overall_known)) / float(cast_size),
        "cast_avg_rating": avg,
        "cast_rating_median": median,
        "cast_rating_std": std,
        "cast_rating_min": min_rating,
        "cast_rating_max": max_rating,
        "cast_prior_count_mean": float(statistics.fmean(overall_counts)),
        "cast_prior_count_max": float(max(overall_counts, default=0.0)),
        "cast_genre_known_ratio": float(len(genre_known)) / float(cast_size),
        "cast_genre_avg_rating": g_avg,
        "cast_genre_rating_median": g_median,
        "cast_genre_rating_std": g_std,
        "cast_genre_rating_min": g_min,
        "cast_genre_rating_max": g_max,
        "cast_genre_prior_count_mean": float(statistics.fmean(genre_counts)),
        "cast_genre_prior_count_max": float(max(genre_counts, default=0.0)),
    }


def fetch_batch_full_cast_context(
    conn: duckdb.DuckDBPyConnection,
    tconsts: Iterable[str],
) -> dict[str, dict[str, float]]:
    """Считает общую и жанровую историю всего principal cast target-фильма.

    В target cast входят все `actor`/`actress` из IMDb `title_principals`, а не
    только legacy slots 1..3. История каждого человека ограничена фильмами с
    `startYear < target_year`; target, тот же календарный год и future исключены.
    """
    ids = [str(value).strip() for value in tconsts if str(value).strip()]
    if not ids:
        return {}

    query = """
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
        target_cast AS (
            SELECT
                bm.tconst AS target_tconst,
                bm.target_year,
                bm.target_genres,
                p.nconst
            FROM batch_movies bm
            JOIN title_principals p
              ON p.tconst = bm.tconst
             AND p.category IN ('actor', 'actress')
            GROUP BY bm.tconst, bm.target_year, bm.target_genres, p.nconst
        ),
        history_rows AS (
            SELECT DISTINCT
                tc.target_tconst,
                tc.nconst,
                tc.target_genres,
                hp.tconst AS prior_tconst,
                hb.genres AS prior_genres,
                TRY_CAST(hr.averageRating AS DOUBLE) AS rating
            FROM target_cast tc
            JOIN title_principals hp
              ON hp.nconst = tc.nconst
             AND hp.category IN ('actor', 'actress')
            JOIN title_basics hb ON hb.tconst = hp.tconst
            JOIN title_ratings hr ON hr.tconst = hp.tconst
            WHERE hb.titleType = 'movie'
              AND TRY_CAST(hb.startYear AS INTEGER) < tc.target_year
              AND TRY_CAST(hr.averageRating AS DOUBLE) IS NOT NULL
        ),
        actor_stats AS (
            SELECT
                target_tconst,
                nconst,
                AVG(rating) AS overall_avg,
                COUNT(DISTINCT prior_tconst) AS overall_count,
                AVG(CASE
                    WHEN list_has_any(
                        string_split(COALESCE(prior_genres, ''), ','),
                        string_split(COALESCE(target_genres, ''), ',')
                    ) THEN rating
                END) AS genre_avg,
                COUNT(DISTINCT CASE
                    WHEN list_has_any(
                        string_split(COALESCE(prior_genres, ''), ','),
                        string_split(COALESCE(target_genres, ''), ',')
                    ) THEN prior_tconst
                END) AS genre_count
            FROM history_rows
            GROUP BY target_tconst, nconst
        )
        SELECT
            tc.target_tconst,
            tc.nconst,
            COALESCE(s.overall_avg, 6.5) AS overall_avg,
            COALESCE(s.overall_count, 0) AS overall_count,
            COALESCE(s.genre_avg, 6.5) AS genre_avg,
            COALESCE(s.genre_count, 0) AS genre_count
        FROM target_cast tc
        LEFT JOIN actor_stats s
          ON s.target_tconst = tc.target_tconst
         AND s.nconst = tc.nconst
        ORDER BY tc.target_tconst, tc.nconst
    """

    grouped: dict[str, list[tuple[str, float, float, float, float]]] = defaultdict(list)
    for row in conn.execute(query, [ids]).fetchall():
        grouped[str(row[0])].append(
            (
                str(row[1]),
                float(row[2]),
                float(row[3]),
                float(row[4]),
                float(row[5]),
            )
        )

    result: dict[str, dict[str, float]] = {}
    for target in ids:
        result[target] = _aggregate_actor_rows(grouped.get(target, []))
    return result


def fetch_full_cast_context(
    conn: duckdb.DuckDBPyConnection,
    *,
    actor_nconsts: Sequence[str | None] | None,
    before_year: int,
    genres: str | Sequence[str] | None,
) -> dict[str, float]:
    """Inference-версия Full Cast с той же temporal/genre семантикой.

    Неразрешённые актёры сохраняются как отдельные unknown-участники ансамбля:
    они уменьшают coverage, но не превращаются в фиктивный рейтинг 6.5.
    """
    raw_ids = [str(value or "").strip() for value in (actor_nconsts or [])]
    valid_ids: list[str] = []
    unknown_count = 0
    for value in raw_ids:
        if not value or value == "Unknown":
            unknown_count += 1
        elif value not in valid_ids:
            valid_ids.append(value)

    if not valid_ids and unknown_count == 0:
        return empty_full_cast_context(0)

    target_genres = _genres_value(genres)
    rows: list[tuple[str, float, float, float, float]] = []
    if valid_ids:
        query = """
            WITH requested AS (
                SELECT nconst FROM UNNEST(?) AS t(nconst)
            ),
            history_rows AS (
                SELECT DISTINCT
                    req.nconst,
                    hp.tconst AS prior_tconst,
                    hb.genres AS prior_genres,
                    TRY_CAST(hr.averageRating AS DOUBLE) AS rating
                FROM requested req
                JOIN title_principals hp
                  ON hp.nconst = req.nconst
                 AND hp.category IN ('actor', 'actress')
                JOIN title_basics hb ON hb.tconst = hp.tconst
                JOIN title_ratings hr ON hr.tconst = hp.tconst
                WHERE hb.titleType = 'movie'
                  AND TRY_CAST(hb.startYear AS INTEGER) < ?
                  AND TRY_CAST(hr.averageRating AS DOUBLE) IS NOT NULL
            ),
            actor_stats AS (
                SELECT
                    nconst,
                    AVG(rating) AS overall_avg,
                    COUNT(DISTINCT prior_tconst) AS overall_count,
                    AVG(CASE
                        WHEN list_has_any(
                            string_split(COALESCE(prior_genres, ''), ','),
                            string_split(?, ',')
                        ) THEN rating
                    END) AS genre_avg,
                    COUNT(DISTINCT CASE
                        WHEN list_has_any(
                            string_split(COALESCE(prior_genres, ''), ','),
                            string_split(?, ',')
                        ) THEN prior_tconst
                    END) AS genre_count
                FROM history_rows
                GROUP BY nconst
            )
            SELECT
                req.nconst,
                COALESCE(s.overall_avg, 6.5),
                COALESCE(s.overall_count, 0),
                COALESCE(s.genre_avg, 6.5),
                COALESCE(s.genre_count, 0)
            FROM requested req
            LEFT JOIN actor_stats s USING (nconst)
            ORDER BY req.nconst
        """
        for row in conn.execute(
            query,
            [valid_ids, int(before_year), target_genres, target_genres],
        ).fetchall():
            rows.append(
                (
                    str(row[0]),
                    float(row[1]),
                    float(row[2]),
                    float(row[3]),
                    float(row[4]),
                )
            )

    for index in range(unknown_count):
        rows.append((f"Unknown:{index}", 6.5, 0.0, 6.5, 0.0))
    return _aggregate_actor_rows(rows)
