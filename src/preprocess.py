import pandas as pd
import numpy as np
from sklearn.impute import SimpleImputer

def preprocess_data(df_basics, df_ratings, df_crew, sample_frac=None, random_state=None):
    # Объединение
    df = pd.merge(df_basics, df_ratings, on='tconst', how='inner')
    df = pd.merge(df, df_crew[['tconst', 'directors']], on='tconst', how='left')

    # Обработка пропусков
    df['primaryTitle'] = df['primaryTitle'].fillna('')
    df['directors'] = df['directors'].fillna('Unknown')
    df['is_remake'] = df['primaryTitle'].apply(
        lambda x: 1 if isinstance(x, str) and 'remake' in x.lower() else 0
    )

    # Числовые колонки
    df['runtimeMinutes'] = pd.to_numeric(df['runtimeMinutes'], errors='coerce')
    df['startYear'] = pd.to_numeric(df['startYear'], errors='coerce')

    # Заполнение медианой
    num_imputer = SimpleImputer(strategy='median')
    df[['runtimeMinutes', 'startYear']] = num_imputer.fit_transform(
        df[['runtimeMinutes', 'startYear']]
    )

    # Средний рейтинг режиссёра
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
    df['director_avg_rating'] = df['director_avg_rating'].fillna(df['averageRating'].mean())

    # Сэмплирование (если нужно)
    if sample_frac and sample_frac < 1.0:
        df = df.sample(frac=sample_frac, random_state=random_state)

    return df, num_imputer   # возвращаем импьютер для сохранения