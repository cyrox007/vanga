from __future__ import annotations

import os
import statistics
from typing import Iterable, Sequence

import duckdb


TEAM_COLLABORATION_FEATURE_NAMES = (
    "team_writer_pair_total",
    "team_writer_pair_known_ratio",
    "team_writer_pair_prior_collaboration_mean",
    "team_writer_pair_prior_collaboration_max",
    "team_writer_pair_prior_rating_avg",
    "team_actor_pair_total",
    "team_actor_pair_known_ratio",
    "team_actor_pair_prior_collaboration_mean",
    "team_actor_pair_prior_collaboration_median",
    "team_actor_pair_prior_collaboration_max",
    "team_actor_pair_prior_rating_avg",
    "team_actor_pair_prior_rating_median",
    "team_actor_pair_prior_rating_std",
)


def team_collaboration_features_enabled() -> bool:
    """Возвращает режим team-wide блока для воспроизводимого ablation v14→v15."""
    raw = str(os.getenv("VANGA_TRAIN_TEAM_COLLABORATION_FEATURES", "1")).strip().casefold()
    return raw not in {"0", "false", "no", "off"}


def empty_team_collaboration_context() -> dict[str, float]:
    """Возвращает безопасный пустой контекст без фиктивной истории."""
    return {
        "team_writer_pair_total": 0.0,
        "team_writer_pair_known_ratio": 0.0,
        "team_writer_pair_prior_collaboration_mean": 0.0,
        "team_writer_pair_prior_collaboration_max": 0.0,
        "team_writer_pair_prior_rating_avg": 6.5,
        "team_actor_pair_total": 0.0,
        "team_actor_pair_known_ratio": 0.0,
        "team_actor_pair_prior_collaboration_mean": 0.0,
        "team_actor_pair_prior_collaboration_median": 0.0,
        "team_actor_pair_prior_collaboration_max": 0.0,
        "team_actor_pair_prior_rating_avg": 6.5,
        "team_actor_pair_prior_rating_median": 6.5,
        "team_actor_pair_prior_rating_std": 0.0,
    }


def _aggregate_pair_rows(
    rows: Sequence[tuple[float, float]],
    *,
    total_pairs: int,
    prefix: str,
) -> dict[str, float]:
    counts = [float(count) for count, _ in rows]
    ratings = [float(rating) for count, rating in rows if float(count) > 0]
    if len(counts) < total_pairs:
        counts.extend([0.0] * (total_pairs - len(counts)))

    known_count = sum(1 for value in counts if value > 0)
    result = {
        f"{prefix}_pair_total": float(total_pairs),
        f"{prefix}_pair_known_ratio": (
            float(known_count) / float(total_pairs) if total_pairs > 0 else 0.0
        ),
        f"{prefix}_pair_prior_collaboration_mean": (
            float(statistics.fmean(counts)) if counts else 0.0
        ),
        f"{prefix}_pair_prior_collaboration_max": float(max(counts, default=0.0)),
        f"{prefix}_pair_prior_rating_avg": (
            float(statistics.fmean(ratings)) if ratings else 6.5
        ),
    }
    if prefix == "team_actor":
        result.update(
            {
                "team_actor_pair_prior_collaboration_median": (
                    float(statistics.median(counts)) if counts else 0.0
                ),
                "team_actor_pair_prior_rating_median": (
                    float(statistics.median(ratings)) if ratings else 6.5
                ),
                "team_actor_pair_prior_rating_std": (
                    float(statistics.pstdev(ratings)) if len(ratings) > 1 else 0.0
                ),
            }
        )
    return result


def fetch_batch_team_collaboration_context(
    conn: duckdb.DuckDBPyConnection,
    tconsts: Iterable[str],
) -> dict[str, dict[str, float]]:
    """Считает team-wide director↔writer и director↔actor history для batch.

    В отличие от legacy pair-признаков используются все director-credit и все
    principal actor/actress target-фильма. История каждой пары ограничена
    фильмами с ``startYear < target_year``; target, same-year и future исключены.
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
            SELECT DISTINCT bm.tconst AS target_tconst, bm.target_year, p.nconst
            FROM batch_movies bm
            JOIN title_principals p
              ON p.tconst = bm.tconst
             AND p.category = 'director'
        ),
        target_actors AS (
            SELECT DISTINCT bm.tconst AS target_tconst, bm.target_year, p.nconst
            FROM batch_movies bm
            JOIN title_principals p
              ON p.tconst = bm.tconst
             AND p.category IN ('actor', 'actress')
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
        writer_pairs AS (
            SELECT td.target_tconst, td.target_year, td.nconst AS director_id, fw.nconst AS person_id
            FROM target_directors td
            JOIN first_writer fw USING (target_tconst, target_year)
            WHERE fw.nconst IS NOT NULL
        ),
        actor_pairs AS (
            SELECT td.target_tconst, td.target_year, td.nconst AS director_id, ta.nconst AS person_id
            FROM target_directors td
            JOIN target_actors ta USING (target_tconst, target_year)
        ),
        writer_history AS (
            SELECT DISTINCT
                wp.target_tconst,
                wp.director_id,
                wp.person_id,
                hp.tconst AS prior_tconst,
                TRY_CAST(r.averageRating AS DOUBLE) AS rating
            FROM writer_pairs wp
            JOIN title_principals hp
              ON hp.nconst = wp.director_id
             AND hp.category = 'director'
            JOIN title_writers hw
              ON hw.tconst = hp.tconst
             AND hw.nconst = wp.person_id
            JOIN title_basics hb ON hb.tconst = hp.tconst
            JOIN title_ratings r ON r.tconst = hp.tconst
            WHERE hb.titleType = 'movie'
              AND TRY_CAST(hb.startYear AS INTEGER) < wp.target_year
              AND TRY_CAST(r.averageRating AS DOUBLE) IS NOT NULL
        ),
        actor_history AS (
            SELECT DISTINCT
                ap.target_tconst,
                ap.director_id,
                ap.person_id,
                hp.tconst AS prior_tconst,
                TRY_CAST(r.averageRating AS DOUBLE) AS rating
            FROM actor_pairs ap
            JOIN title_principals hp
              ON hp.nconst = ap.director_id
             AND hp.category = 'director'
            JOIN title_principals ha
              ON ha.tconst = hp.tconst
             AND ha.nconst = ap.person_id
             AND ha.category IN ('actor', 'actress')
            JOIN title_basics hb ON hb.tconst = hp.tconst
            JOIN title_ratings r ON r.tconst = hp.tconst
            WHERE hb.titleType = 'movie'
              AND TRY_CAST(hb.startYear AS INTEGER) < ap.target_year
              AND TRY_CAST(r.averageRating AS DOUBLE) IS NOT NULL
        ),
        writer_stats AS (
            SELECT
                wp.target_tconst,
                wp.director_id,
                wp.person_id,
                COUNT(DISTINCT wh.prior_tconst) AS prior_count,
                COALESCE(AVG(wh.rating), 6.5) AS avg_rating
            FROM writer_pairs wp
            LEFT JOIN writer_history wh
              ON wh.target_tconst = wp.target_tconst
             AND wh.director_id = wp.director_id
             AND wh.person_id = wp.person_id
            GROUP BY wp.target_tconst, wp.director_id, wp.person_id
        ),
        actor_stats AS (
            SELECT
                ap.target_tconst,
                ap.director_id,
                ap.person_id,
                COUNT(DISTINCT ah.prior_tconst) AS prior_count,
                COALESCE(AVG(ah.rating), 6.5) AS avg_rating
            FROM actor_pairs ap
            LEFT JOIN actor_history ah
              ON ah.target_tconst = ap.target_tconst
             AND ah.director_id = ap.director_id
             AND ah.person_id = ap.person_id
            GROUP BY ap.target_tconst, ap.director_id, ap.person_id
        )
        SELECT 'writer' AS kind, target_tconst, director_id, person_id, prior_count, avg_rating
        FROM writer_stats
        UNION ALL
        SELECT 'actor' AS kind, target_tconst, director_id, person_id, prior_count, avg_rating
        FROM actor_stats
    """

    rows = conn.execute(query, [ids]).fetchall()
    grouped: dict[str, dict[str, list[tuple[float, float]]]] = {
        tconst: {"writer": [], "actor": []} for tconst in ids
    }
    for kind, target_tconst, _director_id, _person_id, prior_count, avg_rating in rows:
        grouped.setdefault(str(target_tconst), {"writer": [], "actor": []})[str(kind)].append(
            (float(prior_count or 0.0), float(avg_rating if avg_rating is not None else 6.5))
        )

    counts_query = """
        WITH batch_movies AS (
            SELECT b.tconst
            FROM UNNEST(?) AS requested(tconst)
            JOIN title_basics b USING (tconst)
        ),
        d AS (
            SELECT bm.tconst, COUNT(DISTINCT p.nconst) AS director_count
            FROM batch_movies bm
            LEFT JOIN title_principals p ON p.tconst = bm.tconst AND p.category = 'director'
            GROUP BY bm.tconst
        ),
        a AS (
            SELECT bm.tconst, COUNT(DISTINCT p.nconst) AS actor_count
            FROM batch_movies bm
            LEFT JOIN title_principals p ON p.tconst = bm.tconst AND p.category IN ('actor','actress')
            GROUP BY bm.tconst
        ),
        w AS (
            SELECT bm.tconst,
                   CASE WHEN c.writers IS NOT NULL AND c.writers <> '\\N'
                             AND NULLIF(TRIM(split_part(c.writers, ',', 1)), '') IS NOT NULL
                        THEN 1 ELSE 0 END AS writer_present
            FROM batch_movies bm
            LEFT JOIN title_crew c ON c.tconst = bm.tconst
        )
        SELECT d.tconst, d.director_count, a.actor_count, w.writer_present
        FROM d JOIN a USING (tconst) JOIN w USING (tconst)
    """
    totals = {
        str(tconst): (int(dcount or 0), int(acount or 0), int(wpresent or 0))
        for tconst, dcount, acount, wpresent in conn.execute(counts_query, [ids]).fetchall()
    }

    result: dict[str, dict[str, float]] = {}
    for tconst in ids:
        director_count, actor_count, writer_present = totals.get(tconst, (0, 0, 0))
        writer_total = director_count if writer_present else 0
        actor_total = director_count * actor_count
        values = empty_team_collaboration_context()
        values.update(
            _aggregate_pair_rows(
                grouped.get(tconst, {}).get("writer", []),
                total_pairs=writer_total,
                prefix="team_writer",
            )
        )
        values.update(
            _aggregate_pair_rows(
                grouped.get(tconst, {}).get("actor", []),
                total_pairs=actor_total,
                prefix="team_actor",
            )
        )
        result[tconst] = values
    return result


def _pair_stats(
    conn: duckdb.DuckDBPyConnection,
    *,
    director_ids: Sequence[str],
    person_ids: Sequence[str],
    before_year: int,
    person_role: str,
) -> list[tuple[float, float]]:
    if not director_ids or not person_ids:
        return []
    if person_role not in {"writer", "actor"}:
        raise ValueError("person_role должен быть writer или actor")

    person_join = (
        "JOIN title_writers x ON x.tconst = p.tconst AND x.nconst = pair.person_id"
        if person_role == "writer"
        else "JOIN title_principals x ON x.tconst = p.tconst AND x.nconst = pair.person_id AND x.category IN ('actor','actress')"
    )
    query = f"""
        WITH directors AS (SELECT nconst FROM UNNEST(?) AS t(nconst)),
        persons AS (SELECT nconst FROM UNNEST(?) AS t(nconst)),
        pairs AS (
            SELECT d.nconst AS director_id, person.nconst AS person_id
            FROM directors d CROSS JOIN persons person
        ),
        history AS (
            SELECT DISTINCT
                pair.director_id,
                pair.person_id,
                p.tconst,
                TRY_CAST(r.averageRating AS DOUBLE) AS rating
            FROM pairs pair
            JOIN title_principals p
              ON p.nconst = pair.director_id
             AND p.category = 'director'
            {person_join}
            JOIN title_basics b ON b.tconst = p.tconst
            JOIN title_ratings r ON r.tconst = p.tconst
            WHERE b.titleType = 'movie'
              AND TRY_CAST(b.startYear AS INTEGER) < ?
              AND TRY_CAST(r.averageRating AS DOUBLE) IS NOT NULL
        )
        SELECT
            pair.director_id,
            pair.person_id,
            COUNT(DISTINCT h.tconst) AS prior_count,
            COALESCE(AVG(h.rating), 6.5) AS avg_rating
        FROM pairs pair
        LEFT JOIN history h
          ON h.director_id = pair.director_id
         AND h.person_id = pair.person_id
        GROUP BY pair.director_id, pair.person_id
    """
    return [
        (float(row[2] or 0.0), float(row[3] if row[3] is not None else 6.5))
        for row in conn.execute(query, [list(director_ids), list(person_ids), int(before_year)]).fetchall()
    ]


def fetch_team_collaboration_context(
    conn: duckdb.DuckDBPyConnection,
    *,
    director_nconsts: Sequence[str | None] | None,
    writer_nconst: str | None,
    actor_nconsts: Sequence[str | None] | None,
    before_year: int,
) -> dict[str, float]:
    """Inference-версия team-wide collaboration с той же missing-семантикой."""
    raw_directors = [str(value or "").strip() for value in (director_nconsts or [])]
    raw_actors = [str(value or "").strip() for value in (actor_nconsts or [])]
    director_total = len(raw_directors)
    actor_total_people = len(raw_actors)

    directors = [value for value in raw_directors if value and value != "Unknown"]
    actors = [value for value in raw_actors if value and value != "Unknown"]
    directors = list(dict.fromkeys(directors))
    actors = list(dict.fromkeys(actors))

    writer_id = str(writer_nconst or "").strip()
    valid_writer = writer_id if writer_id and writer_id != "Unknown" else None

    writer_total = director_total if valid_writer else 0
    actor_total = director_total * actor_total_people
    writer_rows = _pair_stats(
        conn,
        director_ids=directors,
        person_ids=[valid_writer] if valid_writer else [],
        before_year=int(before_year),
        person_role="writer",
    )
    actor_rows = _pair_stats(
        conn,
        director_ids=directors,
        person_ids=actors,
        before_year=int(before_year),
        person_role="actor",
    )

    result = empty_team_collaboration_context()
    result.update(
        _aggregate_pair_rows(writer_rows, total_pairs=writer_total, prefix="team_writer")
    )
    result.update(
        _aggregate_pair_rows(actor_rows, total_pairs=actor_total, prefix="team_actor")
    )
    return result
