import pandas as pd
import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer

def build_text_features(df, text_column, max_features=100, fit=True, vectorizer=None):
    """
    Строит TF‑IDF признаки и возвращает объединённый DataFrame, а также векторизатор.
    Если fit=False, использует переданный vectorizer для transform.
    """
    texts = df[text_column].fillna('')
    if fit:
        tfidf = TfidfVectorizer(max_features=max_features, stop_words='english')
        text_features = tfidf.fit_transform(texts)
    else:
        if vectorizer is None:
            raise ValueError("При fit=False необходимо передать обученный vectorizer")
        tfidf = vectorizer
        text_features = tfidf.transform(texts)

    text_df = pd.DataFrame(
        text_features.toarray(),
        columns=[f"tfidf_{col}" for col in tfidf.get_feature_names_out()],
        index=df.index
    )
    df_with_text = pd.concat([df, text_df], axis=1)
    return df_with_text, tfidf

def prepare_feature_matrix(df, feature_columns):
    """
    Извлекает X (матрицу признаков) и y (целевую переменную) из обработанного DataFrame.
    """
    X = df[feature_columns]
    y = df['averageRating']
    return X, y