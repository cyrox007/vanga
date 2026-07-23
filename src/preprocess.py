import pandas as pd
import numpy as np
from sklearn.impute import SimpleImputer

# 2. Предварительная обработка данных
def preprocess_data(df_basics, df_ratings, df_crew):
    # Объединение данных
    df = pd.merge(df_basics, df_ratings, on='tconst', how='inner')

    # Добавляем данные о режиссерах
    df = pd.merge(df, df_crew[['tconst', 'directors']], on='tconst', how='left')

    # Обработка пропусков
    df['primaryTitle'] = df['primaryTitle'].fillna('')
    df['description'] = df['primaryTitle']  # В реальности нужно использовать колонку с описанием
    df['directors'] = df['directors'].fillna('Unknown')

    # Создаем признак "ремейк" с обработкой NaN
    df['is_remake'] = df['primaryTitle'].apply(
        lambda x: 1 if isinstance(x, str) and 'remake' in x.lower() else 0
    )

    # Заполняем пропуски в числовых данных
    df['runtimeMinutes'] = pd.to_numeric(df['runtimeMinutes'], errors='coerce')
    df['startYear'] = pd.to_numeric(df['startYear'], errors='coerce')

    # Заполнение пропущенных значений
    num_imputer = SimpleImputer(strategy='median')
    df[['runtimeMinutes', 'startYear']] = num_imputer.fit_transform(
        df[['runtimeMinutes', 'startYear']]
    )

    # Добавляем средний рейтинг режиссеров
    director_avg = df_crew.merge(
        df[['tconst', 'averageRating']],
        on='tconst',
        how='left'
    )
    director_avg = director_avg.groupby('directors')['averageRating'].mean().reset_index()
    df = df.merge(
        director_avg.rename(columns={'averageRating': 'director_avg_rating'}),
        on='directors',
        how='left'
    )

    # Заполняем пропуски в рейтингах режиссеров
    df['director_avg_rating'] = df['director_avg_rating'].fillna(df['averageRating'].mean())


    return df.sample(frac=0.2, random_state=42)