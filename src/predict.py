import pandas as pd
import numpy as np
import joblib

def load_model_artifacts(model_dir):
    """Загружает сохранённую модель, импьютер, векторизатор и имена признаков."""
    model = joblib.load(f"{model_dir}/random_forest.pkl")
    imputer = joblib.load(f"{model_dir}/imputer.pkl")
    vectorizer = joblib.load(f"{model_dir}/tfidf_vectorizer.pkl")
    feature_names = joblib.load(f"{model_dir}/feature_names.pkl")
    return model, imputer, vectorizer, feature_names

def predict_single_movie(title, year, director, runtime, description,
                         model, imputer, vectorizer, feature_names,
                         director_avg_ratings, default_rating=6.0):
    """
    Предсказывает рейтинг для одного фильма по переданным параметрам.
    director_avg_ratings – словарь {директор: средний рейтинг его фильмов}
    """
    # Преобразование типов
    year = float(year) if str(year).replace('.','',1).isdigit() else 2020
    runtime = float(runtime) if str(runtime).replace('.','',1).isdigit() else 120
    description = str(description) if description else ""
    director = str(director) if director else "Unknown"

    # Базовые признаки
    is_remake = 1 if 'remake' in title.lower() else 0
    director_avg = director_avg_ratings.get(director, np.nan)
    if np.isnan(director_avg):
        director_avg = 6.0  # можно заменить на медиану из обучающей выборки

    # TF‑IDF
    if description:
        text_feat = vectorizer.transform([description]).toarray()
    else:
        text_feat = np.zeros((1, len(vectorizer.get_feature_names_out())))

    # Собираем DataFrame с правильными колонками
    input_dict = {
        'startYear': year,
        'runtimeMinutes': runtime,
        'director_avg_rating': director_avg,
        'is_remake': is_remake
    }
    # Добавляем TF‑IDF колонки
    for i, col in enumerate(vectorizer.get_feature_names_out()):
        input_dict[f"tfidf_{col}"] = text_feat[0, i]

    input_df = pd.DataFrame([input_dict])

    # Добавляем недостающие колонки (если их нет – заполняем 0)
    for col in feature_names:
        if col not in input_df.columns:
            input_df[col] = 0
    input_df = input_df[feature_names]

    # Импьютинг и предсказание
    X_imp = imputer.transform(input_df)
    pred = model.predict(X_imp)[0]
    return round(pred, 2)

def predict_multiple_movies(movies_list, model, imputer, vectorizer, feature_names,
                            director_avg_ratings, default_rating=6.0):
    """
    Принимает список словарей с полями title, year, director, runtime, description.
    Возвращает DataFrame с результатами.
    """
    results = []
    for movie in movies_list:
        try:
            rating = predict_single_movie(
                title=movie.get('title', ''),
                year=movie.get('year', 2020),
                director=movie.get('director', 'Unknown'),
                runtime=movie.get('runtime', 120),
                description=movie.get('description', ''),
                model=model,
                imputer=imputer,
                vectorizer=vectorizer,
                feature_names=feature_names,
                director_avg_ratings=director_avg_ratings,
                default_rating=default_rating
            )
            results.append({
                'Название': movie.get('title', 'Без названия'),
                'Год': movie.get('year', 2020),
                'Режиссёр': movie.get('director', 'Неизвестен'),
                'Длительность (мин)': movie.get('runtime', 120),
                'Предсказанный рейтинг': rating,
                'Описание': (movie.get('description', '')[:100] + '...') if len(movie.get('description', '')) > 100 else movie.get('description', '')
            })
        except Exception as e:
            print(f"Ошибка при предсказании для {movie.get('title', '')}: {e}")
    return pd.DataFrame(results)