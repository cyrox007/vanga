# 3. Обработка текстовых данных
def process_text_features(df):
    tfidf = TfidfVectorizer(max_features=100, stop_words='english')
    text_features = tfidf.fit_transform(df['description'].fillna(''))
    text_df = pd.DataFrame(
        text_features.toarray(),
        columns=[f"tfidf_{col}" for col in tfidf.get_feature_names_out()]
    )
    return pd.concat([df, text_df], axis=1), tfidf