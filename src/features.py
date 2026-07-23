from sklearn.feature_extraction.text import TfidfVectorizer


def build_features(df, text_column, max_features):
    """
    Строит TF‑IDF признаки и возвращает объединённый DataFrame, а также векторизатор.
    Если fit=False, использует переданный vectorizer для transform.
    """
    vectorizer = TfidfVectorizer(
        max_features=max_features,
        stop_words="english"
    )
    text = vectorizer.fit_transform(
        df[text_column]
    )
    numeric = df[
        [
        "startYear",
        "runtimeMinutes",
        "director_avg_rating",
        "is_remake"
        ]
    ]

    return numeric, text, vectorizer