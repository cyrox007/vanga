
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.ensemble import RandomForestRegressor
from sklearn.model_selection import train_test_split
from sklearn.impute import SimpleImputer
import numpy as np



df_basics, df_ratings, df_crew = load_data()



df = preprocess_data(df_basics, df_ratings, df_crew)

# 3. Обработка текстовых данных
def process_text_features(df):
    tfidf = TfidfVectorizer(max_features=100, stop_words='english')
    text_features = tfidf.fit_transform(df['description'].fillna(''))
    text_df = pd.DataFrame(
        text_features.toarray(),
        columns=[f"tfidf_{col}" for col in tfidf.get_feature_names_out()]
    )
    return pd.concat([df, text_df], axis=1), tfidf

df, tfidf = process_text_features(df)

# 4. Подготовка финального датасета
def prepare_final_dataset(df):
    features = [
        'startYear',
        'runtimeMinutes',
        'director_avg_rating',
        'is_remake'
    ] + [col for col in df.columns if col.startswith('tfidf_')]

    X = df[features]
    y = df['averageRating']

    return X, y

X, y = prepare_final_dataset(df)

# 5. Обучение модели
X_train, X_test, y_train, y_test = train_test_split(
    X, y, test_size=0.2, random_state=42
)

y_train = y_train.fillna(y_train.mean())
imputer = SimpleImputer(strategy='median')
X_train = imputer.fit_transform(X_train)
X_test = imputer.transform(X_test)

model = RandomForestRegressor(
    n_estimators=100,
    random_state=42,
    n_jobs=-1
)
model.fit(X_train, y_train)

# 6. Оценка модели
from sklearn.metrics import mean_absolute_error, r2_score
import numpy as np

# Преобразуем X_test в DataFrame для удобной обработки
X_test_df = pd.DataFrame(X_test) if not isinstance(X_test, pd.DataFrame) else X_test

# Проверка NaN
print(f"NaN в X_test: {X_test_df.isna().sum().sum()}")
print(f"NaN в y_test: {y_test.isna().sum()}")

# Создаем общую маску для фильтрации
nan_mask = y_test.isna()

# Если X_test - numpy array, преобразуем маску в индексы
if isinstance(X_test, np.ndarray):
    # Для numpy arrays используем индексы
    valid_indices = np.where(~nan_mask)[0]
    X_test = X_test[valid_indices]
    y_test = y_test.iloc[valid_indices]  # Для Series используем iloc
else:
    # Для pandas DataFrame
    X_test = X_test[~nan_mask]
    y_test = y_test[~nan_mask]

# Проверка после обработки
print(f"\nПосле обработки:")
print(f"Размер X_test: {len(X_test)}")
print(f"Размер y_test: {len(y_test)}")
print(f"NaN в y_test: {y_test.isna().sum()}")

# Оценка модели
if len(X_test) > 0 and len(y_test) > 0:
    y_pred = model.predict(X_test)
    print("\nModel Evaluation:")
    print(f"MAE: {mean_absolute_error(y_test, y_pred):.3f}")
    print(f"R²: {r2_score(y_test, y_pred):.3f}")
else:
    print("\nОшибка: Нет данных для оценки после обработки NaN")

# 7. Подготовка словаря режиссеров
director_avg_ratings = df.groupby('directors')['averageRating'].mean().to_dict()

import pandas as pd
import random
import numpy as np
from IPython.display import display

def safe_predict_movie_rating(model, tfidf, director_avg_ratings, df, title, year, director, runtime, description):
    """Безопасная функция предсказания рейтинга с обработкой всех ошибок"""
    try:
        # Проверка и преобразование входных данных
        year = float(year) if str(year).replace('.','',1).isdigit() else df['startYear'].median()
        runtime = float(runtime) if str(runtime).replace('.','',1).isdigit() else df['runtimeMinutes'].median()
        description = str(description) if description is not None else ""
        director = str(director) if director is not None else "Unknown"
        director_avg = director_avg_ratings.get(director, df['director_avg_rating'].median())

        # Создание признаков
        is_remake = 1 if isinstance(title, str) and 'remake' in title.lower() else 0

        # Векторизация текста (с защитой от пустого описания)
        if len(description) > 0:
            text_features = tfidf.transform([description]).toarray()
        else:
            text_features = np.zeros((1, len(tfidf.get_feature_names_out())))

        # Подготовка DataFrame
        input_data = pd.DataFrame({
            'startYear': [year],
            'runtimeMinutes': [runtime],
            'director_avg_rating': [director_avg],
            'is_remake': [is_remake]
        })

        # Добавление текстовых фичей
        text_cols = [f"tfidf_{col}" for col in tfidf.get_feature_names_out()]
        text_df = pd.DataFrame(text_features, columns=text_cols)
        input_data = pd.concat([input_data, text_df], axis=1)

        # Добавление отсутствующих колонок
        if hasattr(model, 'feature_names_in_'):
            missing_cols = set(model.feature_names_in_) - set(input_data.columns)
            for col in missing_cols:
                input_data[col] = 0
            input_data = input_data[model.feature_names_in_]

        # Предсказание
        return float(model.predict(input_data)[0])

    except Exception as e:
        print(f"Ошибка предсказания для '{title}': {str(e)}")
        return float(df['averageRating'].median())

def safe_evaluate_performance(model, X_test, y_test, df, num_samples=5):
    """Безопасное сравнение предсказаний с реальными рейтингами"""
    try:
        # Преобразование входных данных
        if isinstance(X_test, np.ndarray):
            if hasattr(model, 'feature_names_in_'):
                X_test_df = pd.DataFrame(X_test, columns=model.feature_names_in_)
            else:
                X_test_df = pd.DataFrame(X_test)
        else:
            X_test_df = X_test.copy()

        if isinstance(y_test, (pd.Series, pd.DataFrame)):
            y_test_values = y_test.values
        else:
            y_test_values = y_test

        # Проверка размеров
        if len(X_test_df) != len(y_test_values):
            print("Ошибка: Размеры X_test и y_test не совпадают")
            return pd.DataFrame()

        # Выбор случайных индексов
        valid_indices = [i for i in range(len(X_test_df)) if not np.isnan(y_test_values[i])]
        sample_indices = random.sample(valid_indices, min(num_samples, len(valid_indices)))

        results = []
        for idx in sample_indices:
            try:
                movie_data = X_test_df.iloc[idx]
                true_rating = y_test_values[idx]

                # Получаем метаданные из оригинального df
                orig_idx = df.index[idx] if idx < len(df) else idx
                movie_meta = {
                    'Название': df.loc[orig_idx, 'primaryTitle'] if 'primaryTitle' in df.columns else f"Фильм {idx}",
                    'Год': int(df.loc[orig_idx, 'startYear']) if 'startYear' in df.columns else 'Неизвестен',
                    'Режиссер': df.loc[orig_idx, 'directors'] if 'directors' in df.columns else "Неизвестен",
                    'Длительность': f"{int(df.loc[orig_idx, 'runtimeMinutes'])} мин" if 'runtimeMinutes' in df.columns else "Неизвестна"
                }

                # Предсказание
                predicted_rating = model.predict(pd.DataFrame([movie_data]))[0]

                results.append({
                    **movie_meta,
                    'Предсказанный': round(predicted_rating, 1),
                    'Реальный': round(true_rating, 1),
                    'Разница': round(abs(predicted_rating - true_rating), 1)
                })
            except Exception as e:
                print(f"Ошибка при обработке фильма {idx}: {str(e)}")
                continue

        if results:
            result_df = pd.DataFrame(results)
            return result_df.style.background_gradient(cmap='Blues', subset=['Разница'])
        return pd.DataFrame()
    except Exception as e:
        print(f"Ошибка в evaluate_model_performance: {str(e)}")
        return pd.DataFrame()

def safe_predict_new_movies(model, tfidf, director_avg_ratings, df, new_movies_list):
    """Безопасное предсказание для новых фильмов"""
    try:
        predictions = []

        for movie in new_movies_list:
            try:
                if 'title' not in movie:
                    print("Пропущен фильм без названия")
                    continue

                rating = safe_predict_movie_rating(
                    model=model,
                    tfidf=tfidf,
                    director_avg_ratings=director_avg_ratings,
                    df=df,
                    title=movie.get('title', 'Без названия'),
                    year=movie.get('year', df['startYear'].median()),
                    director=movie.get('director', 'Unknown'),
                    runtime=movie.get('runtime', df['runtimeMinutes'].median()),
                    description=movie.get('description', '')
                )

                predictions.append({
                    'Название': movie.get('title', 'Без названия'),
                    'Год': int(movie.get('year', df['startYear'].median())),
                    'Режиссер': movie.get('director', 'Неизвестен'),
                    'Длительность': f"{int(movie.get('runtime', df['runtimeMinutes'].median()))} мин",
                    'Рейтинг': round(rating, 1),
                    'Описание': (movie.get('description', '')[:100] + '...') if len(movie.get('description', '')) > 100 else movie.get('description', '')
                })
            except Exception as e:
                print(f"Ошибка при обработке фильма {movie.get('title', '')}: {str(e)}")
                continue

        if predictions:
            result_df = pd.DataFrame(predictions)
            return result_df.style.background_gradient(cmap='Greens', subset=['Рейтинг'])
        return pd.DataFrame()
    except Exception as e:
        print(f"Ошибка в predict_new_movies: {str(e)}")
        return pd.DataFrame()

# 1. Оценка на тестовой выборке
print("=== Оценка на тестовой выборке ===")
performance_result = safe_evaluate_performance(model, X_test, y_test, df, num_samples=5)
if not performance_result.data.empty:
    display(performance_result)
else:
    print("Не удалось получить результаты оценки")

# 2. Предсказание для новых фильмов
print("\n=== Предсказания для новых фильмов ===")
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
    {
        'title': "Avatar 3",
        'year': 2026,
        'director': "Джеймс Кэмерон",
        'runtime': 170,
        'description': "Продолжение эпопеи о Пандоре. В этом фильме будут больше акцентов на водной среде и новых племенах."
    }
]

predictions_result = safe_predict_new_movies(model, tfidf, director_avg_ratings, df, new_movies)
if not predictions_result.data.empty:
    display(predictions_result)
else:
    print("Не удалось получить предсказания для новых фильмов")