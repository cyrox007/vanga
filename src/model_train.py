import joblib
import os

from sklearn.ensemble import RandomForestRegressor
from sklearn.model_selection import train_test_split
from sklearn.impute import SimpleImputer


def train(X,y,config):
    X_train,X_test,y_train,y_test = train_test_split(
        X,
        y,
        test_size=config["train"]["test_size"],
        random_state=config["train"]["random_state"]
    )
    imputer = SimpleImputer(
        strategy="median"
    )
    X_train = imputer.fit_transform(
        X_train
    )
    X_test = imputer.transform(
        X_test
    )
    model = RandomForestRegressor(
        n_estimators=config["model"]["n_estimators"],
        max_depth=config["model"]["max_depth"],
        n_jobs=config["model"]["n_jobs"]
    )
    model.fit(
        X_train,
        y_train
    )
    os.makedirs(
        config["paths"]["model_dir"],
        exist_ok=True
    )
    joblib.dump(
        model,
        "models/random_forest.pkl"
    )
    joblib.dump(
        imputer,
        "models/imputer.pkl"
    )
    return model,X_test,y_test