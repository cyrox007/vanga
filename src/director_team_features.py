from __future__ import annotations

import os
from typing import Iterable, Sequence

import duckdb


DIRECTOR_TEAM_FEATURE_NAMES = (
    "director_team_size",
    "director_team_known_ratio",
    "director_team_avg_rating",
    "director_team_prior_count_mean",
    "director_team_prior_collaboration_count",
    "director_team_prior_collaboration_avg_rating",
    "director_team_collaboration_known",
)


def director_team_features_enabled() -> bool:
    """Возвращает режим director-team блока для воспроизводимого ablation v10→v11."""
    raw = str(os.getenv("VANGA_TRAIN_DIRECTOR_TEAM_FEATURES", "1")).strip().casefold()
    return raw not in {"0", "false", "no", "off"}


def empty_director_team_context(team_size: int = 0) -> dict[str, float]:
    """Возвращает явное состояние отсутствия исторических данных команды."""
    return {
        "director_team_size": float(max(0, team_size)),
        "director_team_known_ratio": 0.0,
        "director_team_avg_rating": 6.5,
        "director_team_prior_count_mean": 0.0,
        "director_team_prior_collaboration_count": 0.0,
        "director_team_prior_collaboration_avg_rating": 6.5,
        "director_team_collaboration_known": 0.0,
    }


def fetch_batch_director_team_context(
    conn: duckdb.DuckDBPyConnection,
    tconsts: Iterable[str],
) -> dict[str, dict[str, float]]:
    """Считает историю всей режиссёрской команды до года target-фильма.

    Legacy-признаки сохраняют первого режиссёра для обратной совместимости,
    а этот блок использует ВСЕ director-credit target-фильма.

    ``director_team_avg_rating`` — среднее role-specific historical average
    только по режиссёрам с реальной прошлой историей. ``known_ratio`` показывает,
    какая доля команды вообще имеет такую историю. ``prior_count_mean`` при этом
    считается по всем режиссёрам, включая нули, чтобы training совпадал с inference.

    ``director_team_prior_collaboration_*`` описывает фильмы, где весь текущий
    набор режиссёров уже работал вместе как режиссёрская команда. Target,
    same-year и future фильмы исключены строгим ``startYear < target_year``.
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
        team_sizes AS (
            SELECT target_tconst, COUNT(DISTINCT nconst) AS team_size
            FROM target_directors
            GROUP BY target_tconst
        ),
        director_history AS (
            SELECT
                td.target_tconst,
                td.nconst,
                AVG(TRY_CAST(r.averageRating AS DOUBLE)) AS avg_rating,
                COUNT(DISTINCT p.tconst) AS prior_count
            FROM target_directors td
            JOIN title_principals p
              ON p.nconst = td.nconst
             AND p.category = 'director'
            JOIN title_basics b ON b.tconst = p.tconst
            JOIN title_ratings r ON r.tconst = p.tconst
            WHERE b.titleType = 'movie'
              AND TRY_CAST(b.startYear AS INTEGER) < td.target_year
              AND TRY_CAST(r.averageRating AS DOUBLE) IS NOT NULL
            GROUP BY td.target_tconst, td.nconst
        ),
        team_history AS (
            SELECT
                ts.target_tconst,
                ts.team_size,
                AVG(dh.avg_rating) AS team_avg_rating,
                AVG(COALESCE(dh.prior_count, 0)) AS prior_count_mean,
                COUNT(CASE WHEN COALESCE(dh.prior_count, 0) > 0 THEN 1 END)
                    AS known_directors
            FROM team_sizes ts
            JOIN target_directors td
              ON td.target_tconst = ts.target_tconst
            LEFT JOIN director_history dh
              ON dh.target_tconst = td.target_tconst
             AND dh.nconst = td.nconst
            GROUP BY ts.target_tconst, ts.team_size
        ),
        collaboration_candidates AS (
            SELECT DISTINCT
                td.target_tconst,
                td.target_year,
                p.tconst AS prior_tconst,
                p.nconst
            FROM target_directors td
            JOIN title_principals p
              ON p.nconst = td.nconst
             AND p.category = 'director'
            JOIN title_basics b ON b.tconst = p.tconst
            WHERE b.titleType = 'movie'
              AND TRY_CAST(b.startYear AS INTEGER) < td.target_year
        ),
        full_team_collaborations AS (
            SELECT
                cc.target_tconst,
                cc.prior_tconst
            FROM collaboration_candidates cc
            JOIN team_sizes ts ON ts.target_tconst = cc.target_tconst
            GROUP BY cc.target_tconst, cc.prior_tconst, ts.team_size
            HAVING COUNT(DISTINCT cc.nconst) = ts.team_size
        ),
        collaboration_stats AS (
            SELECT
                ftc.target_tconst,
                COUNT(DISTINCT ftc.prior_tconst) AS collaboration_count,
                AVG(TRY_CAST(r.averageRating AS DOUBLE)) AS collaboration_avg_rating
            FROM full_team_collaborations ftc
            JOIN title_ratings r ON r.tconst = ftc.prior_tconst
            WHERE TRY_CAST(r.averageRating AS DOUBLE) IS NOT NULL
            GROUP BY ftc.target_tconst
        )
        SELECT
            bm.tconst,
            COALESCE(th.team_size, 0) AS team_size,
            CASE
                WHEN COALESCE(th.team_size, 0) > 0
                THEN CAST(COALESCE(th.known_directors, 0) AS DOUBLE) / th.team_size
                ELSE 0.0
            END AS known_ratio,
            COALESCE(th.team_avg_rating, 6.5) AS team_avg_rating,
            COALESCE(th.prior_count_mean, 0.0) AS prior_count_mean,
            COALESCE(cs.collaboration_count, 0) AS collaboration_count,
            COALESCE(cs.collaboration_avg_rating, 6.5) AS collaboration_avg_rating
        FROM batch_movies bm
        LEFT JOIN team_history th ON th.target_tconst = bm.tconst
        LEFT JOIN collaboration_stats cs ON cs.target_tconst = bm.tconst
    """

    rows = conn.execute(query, [ids]).fetchall()
    result: dict[str, dict[str, float]] = {}
    for row in rows:
        collaboration_count = float(row[5] or 0.0)
        result[str(row[0])] = {
            "director_team_size": float(row[1] or 0.0),
            "director_team_known_ratio": float(row[2] or 0.0),
            "director_team_avg_rating": float(row[3] if row[3] is not None else 6.5),
            "director_team_prior_count_mean": float(row[4] or 0.0),
            "director_team_prior_collaboration_count": collaboration_count,
            "director_team_prior_collaboration_avg_rating": float(
                row[6] if row[6] is not None else 6.5
            ),
            "director_team_collaboration_known": 1.0 if collaboration_count > 0 else 0.0,
        }
    return result


def fetch_director_team_context(
    conn: duckdb.DuckDBPyConnection,
    *,
    director_nconsts: Sequence[str] | None,
    before_year: int,
) -> dict[str, float]:
    """Возвращает director-team context для inference с той же temporal-семантикой."""
    ids: list[str] = []
    for value in director_nconsts or []:
        clean = str(value or "").strip()
        if clean and clean != "Unknown" and clean not in ids:
            ids.append(clean)

    if not ids:
        return empty_director_team_context(0)

    individual_query = """
        WITH requested AS (
            SELECT nconst FROM UNNEST(?) AS t(nconst)
        ),
        history AS (
            SELECT
                req.nconst,
                AVG(TRY_CAST(r.averageRating AS DOUBLE)) AS avg_rating,
                COUNT(DISTINCT b.tconst) AS prior_count
            FROM requested req
            LEFT JOIN title_principals p
              ON p.nconst = req.nconst
             AND p.category = 'director'
            LEFT JOIN title_basics b
              ON b.tconst = p.tconst
             AND b.titleType = 'movie'
             AND TRY_CAST(b.startYear AS INTEGER) < ?
            LEFT JOIN title_ratings r
              ON r.tconst = b.tconst
             AND TRY_CAST(r.averageRating AS DOUBLE) IS NOT NULL
            GROUP BY req.nconst
        )
        SELECT nconst, avg_rating, prior_count FROM history
    """
    rows = conn.execute(individual_query, [ids, int(before_year)]).fetchall()
    known = [row for row in rows if int(row[2] or 0) > 0 and row[1] is not None]
    known_ratio = float(len(known)) / float(len(ids))
    team_avg = (
        sum(float(row[1]) for row in known) / len(known)
        if known
        else 6.5
    )
    prior_count_mean = (
        sum(float(row[2] or 0) for row in rows) / len(ids)
        if ids
        else 0.0
    )

    collaboration_query = """
        WITH requested AS (
            SELECT nconst FROM UNNEST(?) AS t(nconst)
        ),
        candidate_films AS (
            SELECT
                p.tconst,
                COUNT(DISTINCT p.nconst) AS matched_directors
            FROM title_principals p
            JOIN requested req ON req.nconst = p.nconst
            JOIN title_basics b ON b.tconst = p.tconst
            WHERE p.category = 'director'
              AND b.titleType = 'movie'
              AND TRY_CAST(b.startYear AS INTEGER) < ?
            GROUP BY p.tconst
            HAVING COUNT(DISTINCT p.nconst) = ?
        )
        SELECT
            COUNT(DISTINCT cf.tconst) AS collaboration_count,
            AVG(TRY_CAST(r.averageRating AS DOUBLE)) AS collaboration_avg_rating
        FROM candidate_films cf
        JOIN title_ratings r ON r.tconst = cf.tconst
        WHERE TRY_CAST(r.averageRating AS DOUBLE) IS NOT NULL
    """
    row = conn.execute(
        collaboration_query,
        [ids, int(before_year), len(ids)],
    ).fetchone()
    collaboration_count = float((row or [0])[0] or 0.0)
    collaboration_avg = float(row[1] if row and row[1] is not None else 6.5)

    return {
        "director_team_size": float(len(ids)),
        "director_team_known_ratio": known_ratio,
        "director_team_avg_rating": team_avg,
        "director_team_prior_count_mean": prior_count_mean,
        "director_team_prior_collaboration_count": collaboration_count,
        "director_team_prior_collaboration_avg_rating": collaboration_avg,
        "director_team_collaboration_known": 1.0 if collaboration_count > 0 else 0.0,
    }
