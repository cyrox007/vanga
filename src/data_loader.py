import pandas as pd

from src.logger import setup_logger
from src.utils import memory

logger = setup_logger(__name__)

def load_data(config) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    logger.info(
        f"Loading IMDb data RAM={memory()}"
    )

    basics = pd.read_csv(
        config["basics_url"],
        sep="\t",
        compression="gzip",
        usecols=[
            "tconst",
            "primaryTitle",
            "startYear",
            "runtimeMinutes"
        ],
        dtype={
            "primaryTitle":"string"
        },
        low_memory=False
    )

    logger.info(
        f"Basics loaded {len(basics)} rows RAM={memory()}"
    )

    ratings = pd.read_csv(
        config["ratings_url"],
        sep="\t",
        compression="gzip",
        usecols=[
            "tconst",
            "averageRating"
        ]
    )

    logger.info(
        f"Ratings loaded {len(ratings)} rows RAM={memory()}"
    )

    crew = pd.read_csv(
        config["crew_url"],
        sep="\t",
        compression="gzip",
        usecols=[
            "tconst",
            "directors"
        ]
    )

    logger.info(
        f"Crew loaded {len(crew)} rows RAM={memory()}"
    )

    return basics, ratings, crew