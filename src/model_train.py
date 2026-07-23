import joblib
import pandas as pd
from sklearn.ensemble import RandomForestRegressor
from sklearn.model_selection import train_test_split
from sklearn.impute import SimpleImputer

def train_and_save_model(X, y, config, model_dir):
    """
    Обучает модель, сохраняет её, а также импьютер и список колонок.
    """
    # Разделение
    X_train, X_test, y_train, y_test = train_test_split(
        X, y,
        test_size=config['train']['test_size'],
        random_state=config['train']['random_state']
    )

    # Импьютер для X
    imputer = SimpleImputer(strategy='median')
    X_train_imp = imputer.fit_transform(X_train)
    X_test_imp = imputer.transform(X_test)

    # Модель
    model = RandomForestRegressor(
        n_estimators=config['model']['n_estimators'],
        random_state=config['model']['random_state'],
        n_jobs=config['model']['n_jobs']
    )
    model.fit(X_train_imp, y_train)

    # Сохраняем
    joblib.dump(model, f"{model_dir}/random_forest.pkl")
    joblib.dump(imputer, f"{model_dir}/imputer.pkl")
    # Сохраняем имена признаков (для валидации при предсказании)
    joblib.dump(X_train.columns.tolist(), f"{model_dir}/feature_names.pkl")

    return model, imputer, X_train_imp, X_test_imp, y_train, y_test