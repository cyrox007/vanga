import numpy as np
import pandas as pd
import joblib
import os

def load_artifacts(model_dir):
    model = joblib.load(os.path.join(model_dir, "sgd_model.pkl"))
    imputer = joblib.load(os.path.join(model_dir, "imputer.pkl"))
    vectorizer = joblib.load(os.path.join(model_dir, "hashing_vectorizer.pkl"))
    director_avg = joblib.load(os.path.join(model_dir, "director_avg.pkl"))
    return model, imputer, vectorizer, director_avg

def predict_single(title, year, runtime, director, is_remake,
                   model, imputer, vectorizer, director_avg, global_avg=6.0):
    try:
        year = float(year)
    except:
        year = 2020
    try:
        runtime = float(runtime)
    except:
        runtime = 120
    if pd.isna(year):
        year = 2020
    if pd.isna(runtime):
        runtime = 120
    
    X_num = np.array([[year, runtime]]).astype(np.float64)
    X_num_imp = imputer.transform(X_num)
    
    text = title if title else ""
    X_text = vectorizer.transform([text]).toarray()
    
    remake_val = 1 if is_remake else 0
    dir_avg = director_avg.get(director, global_avg)
    if np.isnan(dir_avg):
        dir_avg = global_avg
    
    X = np.hstack([X_num_imp, X_text, np.array([[remake_val]]), np.array([[dir_avg]])])
    pred = model.predict(X)[0]
    return round(pred, 2)

def predict_movies(movies_list, model, imputer, vectorizer, director_avg, global_avg=6.0):
    results = []
    for movie in movies_list:
        pred = predict_single(
            title=movie.get('title', ''),
            year=movie.get('year', 2020),
            runtime=movie.get('runtime', 120),
            director=movie.get('director', 'Unknown'),
            is_remake=movie.get('is_remake', False),
            model=model,
            imputer=imputer,
            vectorizer=vectorizer,
            director_avg=director_avg,
            global_avg=global_avg
        )
        results.append({
            'Название': movie.get('title', ''),
            'Год': movie.get('year', 2020),
            'Режиссёр': movie.get('director', 'Unknown'),
            'Предсказанный рейтинг': pred
        })
    return pd.DataFrame(results)