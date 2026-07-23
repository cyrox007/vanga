import yaml
import pandas as pd

from src.logger import setup_logger
from src.data_loader import load_data
from src.preprocess import preprocess
from src.features import build_features
from src.model_train import train


logger = setup_logger("PIPELINE")



def main():


    logger.info(
        "===== TRAIN START ====="
    )


    with open(
        "config/config.yaml",
        encoding="utf8"
    ) as f:

        config = yaml.safe_load(f)



    basics,ratings,crew = load_data(
        config["data"]
    )



    df = preprocess(

        basics,
        ratings,
        crew,

        config["data"]["sample_frac"],

        config["data"]["random_state"]

    )



    numeric,text,vectorizer = build_features(

        df,

        config["features"]["text_column"],

        config["features"]["tfidf_max_features"]

    )



    X = numeric

    y = df["averageRating"]



    model,X_test,y_test = train(

        X,
        y,
        config

    )


    logger.info(
        "===== TRAIN FINISHED ====="
    )



if __name__=="__main__":

    main()