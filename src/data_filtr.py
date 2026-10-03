import gc
from pathlib import Path
from typing import Generator, List, Optional, Tuple

import duckdb
import numpy as np
import pandas as pd

from settings import config
from src.database import db_connector
from src.logger import setup_logger
from src.normalize import extract_title_features, normalize_genre_str


logger = setup_logger(__name__)


@db_connector
def get_genre_counts(db: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    query = """
        SELECT unnest(string_split(genres, ',')) AS genre, COUNT(*) AS cnt
        FROM title_basics
        WHERE titleType = 'movie' AND genres IS NOT NULL
        GROUP BY genre
    """
    df = db.execute(query).df()
    return df.set_index("genre")["cnt"]


@db_connector
def get_all_genres(db: duckdb.DuckDBPyConnection) -> list[str]:
    query = """
        SELECT DISTINCT unnest(string_split(genres, ',')) AS genre
        FROM title_basics
        WHERE titleType = 'movie' AND genres IS NOT NULL
    """
    df_genres = db.execute(query).df()
    genres = [g for g in df_genres["genre"].tolist() if g and g.strip()]
    logger.info(f"Найдено жанров: {len(genres)}")
    return genres


def get_batches(
    genres: list,
    batch_size: int = 5000,
    use_director_stats: bool = True,
    use_actor_stats: bool = True,
    use_writer_stats: bool = True,
    max_batches: Optional[int] = None,
) -> Generator[Tuple[pd.DataFrame, pd.Series, List[str], List[str]], None, None]:
    """
    Генерирует обучающие батчи с признаками, доступными до релиза фильма.

    Важные правила:
    - статистика режиссёра, сценариста и актёров считается только по фильмам прошлых лет;
    - numVotes не используется, потому что до релиза этот признак неизвестен;
    - первые три актёра выбираются по рангу среди актёров, а не по глобальному ordering;
    - для совместимости avg_rating без истории остаётся 6.5, но schema v6 получает
      отдельные *_known и *_prior_count, поэтому fallback больше не выглядит фактом.
    """
    del genres  # список жанров оставлен в сигнатуре для обратной совместимости

    logger.info("Инициализация генератора обучающих батчей")
    conn = duckdb.connect(config.IMDB_DB_PATH)
    conn.execute("SET memory_limit = '700MB'")
    conn.execute("SET threads = 2")
    conn.execute("SET preserve_insertion_order = false")
    temp_dir = Path(f"{config.ABSPATH}/temp")
    temp_dir.mkdir(parents=True, exist_ok=True)
    conn.execute(f"SET temp_directory = '{temp_dir}'")

    id_query = """
        SELECT tconst
        FROM title_basics
        WHERE titleType = 'movie'
          AND startYear IS NOT NULL
          AND tconst > ?
        ORDER BY tconst
        LIMIT ?
    """

    enrich_query = """
        WITH batch_movies AS (
            SELECT
                b.tconst,
                b.primaryTitle,
                TRY_CAST(b.startYear AS INTEGER) AS startYear,
                TRY_CAST(b.runtimeMinutes AS INTEGER) AS runtimeMinutes,
                b.genres,
                TRY_CAST(r.averageRating AS DOUBLE) AS averageRating
            FROM UNNEST(?) AS t(tconst)
            JOIN title_basics b ON b.tconst = t.tconst
            JOIN title_ratings r ON r.tconst = b.tconst
            WHERE b.titleType = 'movie'
              AND TRY_CAST(b.startYear AS INTEGER) IS NOT NULL
              AND TRY_CAST(b.runtimeMinutes AS INTEGER) IS NOT NULL
              AND b.genres IS NOT NULL
              AND TRY_CAST(r.averageRating AS DOUBLE) IS NOT NULL
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
              ON p.tconst = d.tconst
             AND p.ordering = d.min_ord
        ),
        director_history AS (
            SELECT
                bm.tconst,
                fd.nconst,
                AVG(TRY_CAST(r.averageRating AS DOUBLE)) AS avg_rating,
                COUNT(DISTINCT p.tconst) AS prior_count
            FROM batch_movies bm
            JOIN first_director fd ON fd.tconst = bm.tconst
            JOIN title_principals p
              ON p.nconst = fd.nconst
             AND p.category = 'director'
            JOIN title_basics b ON b.tconst = p.tconst
            JOIN title_ratings r ON r.tconst = b.tconst
            WHERE b.titleType = 'movie'
              AND TRY_CAST(b.startYear AS INTEGER) < bm.startYear
              AND TRY_CAST(r.averageRating AS DOUBLE) IS NOT NULL
            GROUP BY bm.tconst, fd.nconst
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
        writer_history AS (
            SELECT
                bm.tconst,
                fw.nconst,
                AVG(TRY_CAST(r.averageRating AS DOUBLE)) AS avg_rating,
                COUNT(DISTINCT tw.tconst) AS prior_count
            FROM batch_movies bm
            JOIN first_writer fw ON fw.tconst = bm.tconst
            JOIN title_writers tw ON tw.nconst = fw.nconst
            JOIN title_basics b ON b.tconst = tw.tconst
            JOIN title_ratings r ON r.tconst = b.tconst
            WHERE b.titleType = 'movie'
              AND TRY_CAST(b.startYear AS INTEGER) < bm.startYear
              AND TRY_CAST(r.averageRating AS DOUBLE) IS NOT NULL
            GROUP BY bm.tconst, fw.nconst
        ),
        actor_ranked AS (
            SELECT tconst, nconst, rn
            FROM (
                SELECT
                    tconst,
                    nconst,
                    ROW_NUMBER() OVER (
                        PARTITION BY tconst
                        ORDER BY ordering
                    ) AS rn
                FROM title_principals
                WHERE category IN ('actor', 'actress')
                  AND tconst IN (SELECT tconst FROM batch_movies)
            )
            WHERE rn <= 3
        ),
        actors_pivot AS (
            SELECT
                tconst,
                MAX(CASE WHEN rn = 1 THEN nconst END) AS actor_1_nconst,
                MAX(CASE WHEN rn = 2 THEN nconst END) AS actor_2_nconst,
                MAX(CASE WHEN rn = 3 THEN nconst END) AS actor_3_nconst
            FROM actor_ranked
            GROUP BY tconst
        ),
        actor_history AS (
            SELECT
                bm.tconst,
                ar.rn,
                AVG(TRY_CAST(r.averageRating AS DOUBLE)) AS avg_rating,
                COUNT(DISTINCT p.tconst) AS prior_count
            FROM batch_movies bm
            JOIN actor_ranked ar ON ar.tconst = bm.tconst
            JOIN title_principals p
              ON p.nconst = ar.nconst
             AND p.category IN ('actor', 'actress')
            JOIN title_basics b ON b.tconst = p.tconst
            JOIN title_ratings r ON r.tconst = b.tconst
            WHERE b.titleType = 'movie'
              AND TRY_CAST(b.startYear AS INTEGER) < bm.startYear
              AND TRY_CAST(r.averageRating AS DOUBLE) IS NOT NULL
            GROUP BY bm.tconst, ar.rn
        ),
        actor_history_pivot AS (
            SELECT
                tconst,
                MAX(CASE WHEN rn = 1 THEN avg_rating END) AS actor_1_avg_rating,
                MAX(CASE WHEN rn = 2 THEN avg_rating END) AS actor_2_avg_rating,
                MAX(CASE WHEN rn = 3 THEN avg_rating END) AS actor_3_avg_rating,
                MAX(CASE WHEN rn = 1 THEN prior_count END) AS actor_1_prior_count,
                MAX(CASE WHEN rn = 2 THEN prior_count END) AS actor_2_prior_count,
                MAX(CASE WHEN rn = 3 THEN prior_count END) AS actor_3_prior_count
            FROM actor_history
            GROUP BY tconst
        )
        SELECT
            bm.tconst,
            bm.primaryTitle,
            bm.startYear,
            bm.runtimeMinutes,
            bm.genres,
            bm.averageRating,
            fd.nconst AS director_nconst,
            dh.avg_rating AS director_avg_rating,
            dh.prior_count AS director_prior_count,
            fw.nconst AS writer_nconst,
            wh.avg_rating AS writer_avg_rating,
            wh.prior_count AS writer_prior_count,
            ap.actor_1_nconst,
            ap.actor_2_nconst,
            ap.actor_3_nconst,
            ah.actor_1_avg_rating,
            ah.actor_2_avg_rating,
            ah.actor_3_avg_rating,
            ah.actor_1_prior_count,
            ah.actor_2_prior_count,
            ah.actor_3_prior_count
        FROM batch_movies bm
        LEFT JOIN first_director fd ON fd.tconst = bm.tconst
        LEFT JOIN director_history dh ON dh.tconst = bm.tconst
        LEFT JOIN first_writer fw ON fw.tconst = bm.tconst
        LEFT JOIN writer_history wh ON wh.tconst = bm.tconst
        LEFT JOIN actors_pivot ap ON ap.tconst = bm.tconst
        LEFT JOIN actor_history_pivot ah ON ah.tconst = bm.tconst
    """

    total_processed = 0
    batches_count = 0
    last_tconst = ""

    try:
        while True:
            if max_batches is not None and batches_count >= max_batches:
                logger.info(f"Достигнут лимит батчей: {max_batches}")
                break

            ids_rows = conn.execute(id_query, [last_tconst, batch_size]).fetchall()
            if not ids_rows:
                logger.info("Данные в базе исчерпаны")
                break

            tconsts_batch = [row[0] for row in ids_rows]
            last_tconst = tconsts_batch[-1]

            enriched_rows = conn.execute(enrich_query, [tconsts_batch]).fetchall()
            if not enriched_rows:
                continue

            df_batch = pd.DataFrame(
                enriched_rows,
                columns=[
                    "tconst",
                    "primaryTitle",
                    "startYear",
                    "runtimeMinutes",
                    "genres",
                    "averageRating",
                    "director_nconst",
                    "director_avg_rating",
                    "director_prior_count",
                    "writer_nconst",
                    "writer_avg_rating",
                    "writer_prior_count",
                    "actor_1_nconst",
                    "actor_2_nconst",
                    "actor_3_nconst",
                    "actor_1_avg_rating",
                    "actor_2_avg_rating",
                    "actor_3_avg_rating",
                    "actor_1_prior_count",
                    "actor_2_prior_count",
                    "actor_3_prior_count",
                ],
            )

            numeric_cols = [
                "startYear",
                "runtimeMinutes",
                "averageRating",
                "director_avg_rating",
                "director_prior_count",
                "writer_avg_rating",
                "writer_prior_count",
                "actor_1_avg_rating",
                "actor_2_avg_rating",
                "actor_3_avg_rating",
                "actor_1_prior_count",
                "actor_2_prior_count",
                "actor_3_prior_count",
            ]
            for col in numeric_cols:
                df_batch[col] = pd.to_numeric(df_batch[col], errors="coerce")

            df_batch = df_batch.dropna(
                subset=["startYear", "runtimeMinutes", "averageRating"]
            )
            df_batch = df_batch[
                (df_batch["startYear"] >= 1900)
                & (df_batch["startYear"] <= 2100)
                & (df_batch["runtimeMinutes"] >= 10)
                & (df_batch["runtimeMinutes"] <= 300)
            ]
            if df_batch.empty:
                continue

            numeric_df = pd.DataFrame(index=df_batch.index, dtype=np.float32)
            numeric_df["startYear"] = (
                (df_batch["startYear"] - 1900) / 100.0
            ).astype(np.float32)
            numeric_df["runtimeMinutes"] = (
                df_batch["runtimeMinutes"] / 100.0
            ).astype(np.float32)

            if use_director_stats:
                director_prior = df_batch["director_prior_count"].fillna(0)
                numeric_df["director_avg_rating"] = (
                    df_batch["director_avg_rating"].fillna(6.5).astype(np.float32)
                )
                numeric_df["director_prior_count"] = director_prior.astype(np.float32)
                numeric_df["director_known"] = (director_prior > 0).astype(np.float32)

            if use_writer_stats:
                writer_prior = df_batch["writer_prior_count"].fillna(0)
                numeric_df["writer_avg_rating"] = (
                    df_batch["writer_avg_rating"].fillna(6.5).astype(np.float32)
                )
                numeric_df["writer_prior_count"] = writer_prior.astype(np.float32)
                numeric_df["writer_known"] = (writer_prior > 0).astype(np.float32)

            if use_actor_stats:
                for i in range(3):
                    col = f"actor_{i + 1}_avg_rating"
                    prior_col = f"actor_{i + 1}_prior_count"
                    known_col = f"actor_{i + 1}_known"
                    prior = df_batch[prior_col].fillna(0)
                    numeric_df[col] = df_batch[col].fillna(6.5).astype(np.float32)
                    numeric_df[prior_col] = prior.astype(np.float32)
                    numeric_df[known_col] = (prior > 0).astype(np.float32)

            title_features = (
                df_batch["primaryTitle"]
                .fillna("")
                .apply(extract_title_features)
                .apply(pd.Series)
            )
            for col in title_features.columns:
                numeric_df[col] = title_features[col].fillna(0).astype(np.float32)

            categorical_df = pd.DataFrame(index=df_batch.index)
            categorical_df["genres_combined"] = (
                df_batch["genres"]
                .fillna("Unknown")
                .apply(normalize_genre_str)
            )
            categorical_df["director_id"] = (
                df_batch["director_nconst"].fillna("Unknown")
            )
            categorical_df["writer_id"] = (
                df_batch["writer_nconst"].fillna("Unknown")
            )
            for i in range(3):
                categorical_df[f"actor_{i + 1}_id"] = (
                    df_batch[f"actor_{i + 1}_nconst"].fillna("Unknown")
                )

            y = df_batch["averageRating"].astype(np.float32)
            X = pd.concat([numeric_df, categorical_df], axis=1)

            if X.empty:
                continue

            total_processed += len(X)
            batches_count += 1
            logger.info(
                f"Батч {batches_count}: {len(X)} строк. Всего: {total_processed}"
            )
            yield (
                X,
                y,
                df_batch["primaryTitle"].tolist(),
                df_batch["tconst"].tolist(),
            )

            del (
                df_batch,
                numeric_df,
                categorical_df,
                X,
                y,
                ids_rows,
                enriched_rows,
                tconsts_batch,
            )
            gc.collect()

    except Exception as exc:
        logger.error(f"Ошибка при генерации батчей: {exc}")
        raise
    finally:
        conn.close()
        logger.info("Соединение с БД закрыто")
