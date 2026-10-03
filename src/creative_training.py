from __future__ import annotations

from pathlib import Path
from typing import Generator, List, Optional, Tuple

import duckdb
import numpy as np
import pandas as pd

from settings import config
from src.data_filtr import get_batches as base_get_batches
from src.creative_team_features import (
    CREATIVE_TEAM_FEATURE_NAMES,
    creative_team_features_enabled,
    fetch_batch_creative_team_context,
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
    """Добавляет P2 Creative Team features поверх проверенного base pipeline.

    Base pipeline остаётся источником schema v5/v6. Это позволяет воспроизводимо
    отключить P2 через ``VANGA_TRAIN_CREATIVE_TEAM_FEATURES=0`` и сравнить v6 с
    кандидатом v7 на том же temporal dataset.
    """
    enabled = creative_team_features_enabled()
    if not enabled:
        yield from base_get_batches(
            genres,
            batch_size=batch_size,
            use_director_stats=use_director_stats,
            use_actor_stats=use_actor_stats,
            use_writer_stats=use_writer_stats,
            max_batches=max_batches,
        )
        return

    logger.info(
        "Creative Team features включены: %s",
        ", ".join(CREATIVE_TEAM_FEATURE_NAMES),
    )
    conn = duckdb.connect(str(config.IMDB_DB_PATH), read_only=True)
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
                values = [
                    context.get(tconst, {}).get(feature_name, default)
                    for tconst in tconsts
                ]
                enriched[feature_name] = np.asarray(values, dtype=np.float32)

            yield enriched, y, titles, tconsts
    finally:
        conn.close()
        logger.info("Соединение Creative Team training с БД закрыто")
