import os
from pathlib import Path


class Config:
    ABSPATH = os.path.dirname(os.path.abspath(__file__))

    IMDB_DB_PATH = os.getenv(
        "VANGA_IMDB_DB",
        str(Path(ABSPATH) / "imdb.duckdb"),
    )
    ENRICHMENT_DB_PATH = os.getenv(
        "VANGA_ENRICHMENT_DB",
        str(Path(ABSPATH) / "enrichment.duckdb"),
    )
    WIKIMEDIA_USER_AGENT = os.getenv(
        "VANGA_WIKIMEDIA_USER_AGENT",
        "KinoVanga/0.1 (https://github.com/cyrox007/vanga)",
    ).strip()
    WIKIMEDIA_MIN_INTERVAL_SECONDS = max(
        0.2,
        float(os.getenv("VANGA_WIKIMEDIA_MIN_INTERVAL_SECONDS", "0.5")),
    )
    WIKIMEDIA_TIMEOUT_SECONDS = max(
        5,
        int(os.getenv("VANGA_WIKIMEDIA_TIMEOUT_SECONDS", "20")),
    )


config = Config()
