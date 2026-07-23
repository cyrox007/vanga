import yaml
import pandas as pd
from src.predict import load_model_artifacts, predict_multiple_movies

# Здесь нужно получить словарь средних рейтингов режиссёров.
# Его можно вычислить из обучающего набора и сохранить отдельно,
# либо загрузить из файла. Для простоты мы создадим его на лету,
# но в реальном проекте лучше сохранять.
def get_director_avg_ratings(df):
    return df.groupby('directors')['averageRating'].mean().to_dict()

if __name__ == "__main__":
    with open('config/config.yaml', 'r') as f:
        config = yaml.safe_load(f)

    # Загружаем модель и артефакты
    model, imputer, vectorizer, feature_names = load_model_artifacts(config['paths']['model_dir'])

    # Для словаря режиссёров нужно загрузить исходный df (можно сохранить его после предобработки)
    # В демонстрационных целях мы просто загрузим его заново (лучше сохранять в data/processed)
    from src.data_loader import load_data
    from src.preprocess import preprocess_data
    df_basics, df_ratings, df_crew = load_data(
        config['data']['basics_url'],
        config['data']['ratings_url'],
        config['data']['crew_url']
    )
    df, _ = preprocess_data(df_basics, df_ratings, df_crew, sample_frac=1.0)  # на всех данных
    director_avg = get_director_avg_ratings(df)

    # Список новых фильмов
    new_movies = [
        {
            'title': "The Batman: Part II",
            'year': 2026,
            'director': "Мэтт Ривз",
            'runtime': 150,
            'description': "Продолжение истории Темного рыцаря, в котором Бэтмен сталкивается с новыми угрозами, включая Смерть (Deathstroke)."
        },
        {
            'title': "Дюна: Часть Третья",
            'year': 2026,
            'director': "Дени Вильнёв",
            'runtime': 165,
            'description': "Завершение эпической саги по романам Фрэнка Герберта"
        },
        # ...
    ]

    results = predict_multiple_movies(
        new_movies,
        model, imputer, vectorizer, feature_names,
        director_avg
    )
    print("\nПредсказанные рейтинги для новых фильмов:")
    print(results.to_string(index=False))