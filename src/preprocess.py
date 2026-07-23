import pandas as pd
from sklearn.impute import SimpleImputer

from src.logger import setup_logger
from src.utils import memory


logger = setup_logger(__name__)



def preprocess(
        basics,
        ratings,
        crew,
        sample_frac,
        random_state
):
    logger.info(
        "Merge datasets"
    )

    df = basics.merge(
        ratings,
        on="tconst",
        how="inner"
    )

    df = df.merge(
        crew,
        on="tconst",
        how="left"
    )

    logger.info(
        f"Merged rows={len(df)} RAM={memory()}"
    )

    df["primaryTitle"] = (
        df["primaryTitle"]
        .fillna("")
    )

    df["directors"] = (
        df["directors"]
        .fillna("Unknown")
    )

    df["runtimeMinutes"] = pd.to_numeric(
        df["runtimeMinutes"],
        errors="coerce"
    )

    df["startYear"] = pd.to_numeric(
        df["startYear"],
        errors="coerce"
    )

    imputer = SimpleImputer(
        strategy="median"
    )

    df[
        [
            "runtimeMinutes",
            "startYear"
        ]
    ] = imputer.fit_transform(
        df[
            [
            "runtimeMinutes",
            "startYear"
            ]
        ]
    )

    df["is_remake"] = (
        df["primaryTitle"]
        .str.lower()
        .str.contains("remake")
        .astype(int)
    )

    director_rating = (
        df.groupby("directors")["averageRating"].mean()
    )

    df["director_avg_rating"] = (
        df["directors"].map(director_rating)
    )

    df["director_avg_rating"] = (
        df["director_avg_rating"]
        .fillna(
            df["averageRating"].mean()
        )
    )

    if sample_frac < 1:

        df = df.sample(
            frac=sample_frac,
            random_state=random_state
        )

    logger.info(
        f"Preprocess finished rows={len(df)}"
    )

    return df