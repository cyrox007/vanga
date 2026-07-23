import yaml
import joblib
import pandas as pd
from src.data_loader import load_data
from src.logger import setup_logger
from src.preprocess import preprocess_data
from src.features import build_text_features, prepare_feature_matrix
from src.model_train import train_and_save_model
from src.evaluate import evaluate_model, get_sample_comparison

logger = setup_logger(__name__)

def main():
    # Читаем конфиг
    with open('config/config.yaml', 'r') as f:
        logger.info('Читаем конфиг')
        config = yaml.safe_load(f)

    # 1. Загрузка
    df_basics, df_ratings, df_crew = load_data(
        config['data']['basics_url'],
        config['data']['ratings_url'],
        config['data']['crew_url']
    )

    # 2. Предобработка
    df, imputer_raw = preprocess_data(
        df_basics, df_ratings, df_crew,
        sample_frac=config['data']['sample_frac'],
        random_state=config['data']['random_state']
    )

    # 3. TF‑IDF признаки
    df_with_text, tfidf = build_text_features(
        df,
        text_column=config['features']['text_column'],
        max_features=config['features']['tfidf_max_features'],
        fit=True
    )

    # 4. Определяем список финальных признаков
    text_cols = [f"tfidf_{col}" for col in tfidf.get_feature_names_out()]
    feature_cols = ['startYear', 'runtimeMinutes', 'director_avg_rating', 'is_remake'] + text_cols
    X, y = prepare_feature_matrix(df_with_text, feature_cols)

    # 5. Обучение и сохранение
    model, imputer, X_train, X_test, y_train, y_test = train_and_save_model(
        X, y, config, config['paths']['model_dir']
    )

    # Сохраняем также векторизатор
    joblib.dump(tfidf, f"{config['paths']['model_dir']}/tfidf_vectorizer.pkl")

    # 6. Оценка
    mae, r2 = evaluate_model(model, X_test, y_test, imputer)
    print(f"MAE на тесте: {mae:.3f}")
    print(f"R² на тесте: {r2:.3f}")

    # 7. Пример сравнения
    sample_df = get_sample_comparison(model, X_test, y_test, df, imputer, num_samples=5)
    print("\nСравнение предсказаний с реальными рейтингами (случайные фильмы):")
    print(sample_df.to_string(index=False))

if __name__ == "__main__":
    main()