from __future__ import annotations

from pathlib import Path
from typing import Generator, List, Optional, Tuple

import duckdb
import numpy as np
import pandas as pd

from settings import config
from src.cast_pair_features import (
    CAST_PAIR_FEATURE_NAMES,
    cast_pair_features_enabled,
    fetch_batch_cast_pair_context,
)
from src.data_filtr import get_batches as base_get_batches
from src.creative_team_features import (
    CREATIVE_TEAM_FEATURE_NAMES,
    creative_team_features_enabled,
    fetch_batch_creative_team_context,
)
from src.director_actor_features import (
    DIRECTOR_ACTOR_PAIR_FEATURE_NAMES,
    director_actor_pair_features_enabled,
    fetch_batch_director_actor_pair_context,
)
from src.director_team_features import (
    DIRECTOR_TEAM_FEATURE_NAMES,
    director_team_features_enabled,
    fetch_batch_director_team_context,
)
from src.full_cast_features import (
    FULL_CAST_FEATURE_NAMES,
    fetch_batch_full_cast_context,
    full_cast_features_enabled,
)
from src.pair_features import (
    DIRECTOR_WRITER_PAIR_FEATURE_NAMES,
    director_writer_pair_features_enabled,
    fetch_batch_director_writer_pair_context,
)
from src.trend_features import (
    CREATIVE_TREND_FEATURE_NAMES,
    creative_trend_features_enabled,
    fetch_batch_creative_trend_context,
)
from src.logger import setup_logger


logger = setup_logger(__name__)


def get_batches(
    genres: list,
    batch_size: int = 5000,
    use_director_stats: bool = True,
    use_actor_stats: bool = True,
    use_writer_stats: bool = True,
    max_batches: Optional[int] = None,
) -> Generator[Tuple[pd.DataFrame, pd.Series, List[str], List[str]], None, None]:
    """Добавляет P2 Creative Team features поверх базового disk-first pipeline.

    Иерархия схем:
    - Creative Team off -> v6;
    - director-writer pair off -> v7;
    - director-actor pair off -> v8;
    - trend off -> v9;
    - director-team off -> v10;
    - full-cast off -> v11;
    - cast-pair off -> v12;
    - все блоки on -> candidate v13.

    V13 описывает историю совместной работы всех unordered actor↔actor пар
    principal cast, не заменяя прозрачные pair-агрегаты единым cohesion score.
    """
    creative_enabled = creative_team_features_enabled()
    writer_pair_enabled = creative_enabled and director_writer_pair_features_enabled()
    actor_pair_enabled = writer_pair_enabled and director_actor_pair_features_enabled()
    trend_enabled = actor_pair_enabled and creative_trend_features_enabled()
    director_team_enabled = trend_enabled and director_team_features_enabled()
    full_cast_enabled = director_team_enabled and full_cast_features_enabled()
    cast_pair_enabled = full_cast_enabled and cast_pair_features_enabled()

    if not creative_enabled:
        yield from base_get_batches(
            genres,
            batch_size=batch_size,
            use_director_stats=use_director_stats,
            use_actor_stats=use_actor_stats,
            use_writer_stats=use_writer_stats,
            max_batches=max_batches,
        )
        return

    logger.info("Creative Team features включены: %s", ", ".join(CREATIVE_TEAM_FEATURE_NAMES))
    if writer_pair_enabled:
        logger.info("Director-writer pair features включены: %s", ", ".join(DIRECTOR_WRITER_PAIR_FEATURE_NAMES))
    if actor_pair_enabled:
        logger.info("Director-actor pair features включены: %s", ", ".join(DIRECTOR_ACTOR_PAIR_FEATURE_NAMES))
    if trend_enabled:
        logger.info("Creative trend features включены: %s", ", ".join(CREATIVE_TREND_FEATURE_NAMES))
    if director_team_enabled:
        logger.info("Director-team features включены: %s", ", ".join(DIRECTOR_TEAM_FEATURE_NAMES))
    if full_cast_enabled:
        logger.info("Full-cast features включены: %s", ", ".join(FULL_CAST_FEATURE_NAMES))
    if cast_pair_enabled:
        logger.info("Cast-pair features включены: %s", ", ".join(CAST_PAIR_FEATURE_NAMES))

    conn = duckdb.connect(str(config.IMDB_DB_PATH))
    conn.execute("SET memory_limit = '256MB'")
    conn.execute("SET threads = 1")
    conn.execute("SET preserve_insertion_order = false")
    temp_dir = Path(config.ABSPATH) / "temp"
    temp_dir.mkdir(parents=True, exist_ok=True)
    conn.execute(f"SET temp_directory = '{temp_dir}'")

    try:
        for X, y, titles, tconsts in base_get_batches(
            genres,
            batch_size=batch_size,
            use_director_stats=use_director_stats,
            use_actor_stats=use_actor_stats,
            use_writer_stats=use_writer_stats,
            max_batches=max_batches,
        ):
            context = fetch_batch_creative_team_context(conn, tconsts)
            enriched = X.copy()

            for feature_name in CREATIVE_TEAM_FEATURE_NAMES:
                default = 6.5 if feature_name.endswith("_avg_rating") else 0.0
                enriched[feature_name] = np.asarray(
                    [context.get(tconst, {}).get(feature_name, default) for tconst in tconsts],
                    dtype=np.float32,
                )

            if writer_pair_enabled:
                pair_context = fetch_batch_director_writer_pair_context(conn, tconsts)
                for feature_name in DIRECTOR_WRITER_PAIR_FEATURE_NAMES:
                    default = 6.5 if feature_name == "director_writer_pair_avg_rating" else 0.0
                    enriched[feature_name] = np.asarray(
                        [pair_context.get(tconst, {}).get(feature_name, default) for tconst in tconsts],
                        dtype=np.float32,
                    )

            if actor_pair_enabled:
                actor_pair_context = fetch_batch_director_actor_pair_context(conn, tconsts)
                for feature_name in DIRECTOR_ACTOR_PAIR_FEATURE_NAMES:
                    default = 6.5 if feature_name.endswith("_avg_rating") else 0.0
                    enriched[feature_name] = np.asarray(
                        [actor_pair_context.get(tconst, {}).get(feature_name, default) for tconst in tconsts],
                        dtype=np.float32,
                    )

            if trend_enabled:
                trend_context = fetch_batch_creative_trend_context(conn, tconsts)
                for feature_name in CREATIVE_TREND_FEATURE_NAMES:
                    enriched[feature_name] = np.asarray(
                        [trend_context.get(tconst, {}).get(feature_name, 0.0) for tconst in tconsts],
                        dtype=np.float32,
                    )

            if director_team_enabled:
                team_context = fetch_batch_director_team_context(conn, tconsts)
                for feature_name in DIRECTOR_TEAM_FEATURE_NAMES:
                    default = 6.5 if feature_name in {
                        "director_team_avg_rating",
                        "director_team_prior_collaboration_avg_rating",
                    } else 0.0
                    enriched[feature_name] = np.asarray(
                        [team_context.get(tconst, {}).get(feature_name, default) for tconst in tconsts],
                        dtype=np.float32,
                    )

            if full_cast_enabled:
                cast_context = fetch_batch_full_cast_context(conn, tconsts)
                rating_defaults = {
                    "cast_avg_rating",
                    "cast_rating_median",
                    "cast_rating_min",
                    "cast_rating_max",
                    "cast_genre_avg_rating",
                    "cast_genre_rating_median",
                    "cast_genre_rating_min",
                    "cast_genre_rating_max",
                }
                for feature_name in FULL_CAST_FEATURE_NAMES:
                    default = 6.5 if feature_name in rating_defaults else 0.0
                    enriched[feature_name] = np.asarray(
                        [cast_context.get(tconst, {}).get(feature_name, default) for tconst in tconsts],
                        dtype=np.float32,
                    )

            if cast_pair_enabled:
                cast_pair_context = fetch_batch_cast_pair_context(conn, tconsts)
                rating_defaults = {
                    "cast_pair_prior_rating_avg",
                    "cast_pair_prior_rating_median",
                }
                for feature_name in CAST_PAIR_FEATURE_NAMES:
                    default = 6.5 if feature_name in rating_defaults else 0.0
                    enriched[feature_name] = np.asarray(
                        [cast_pair_context.get(tconst, {}).get(feature_name, default) for tconst in tconsts],
                        dtype=np.float32,
                    )

            yield enriched, y, titles, tconsts
    finally:
        conn.close()
        logger.info("Соединение Creative Team training с БД закрыто")
