import yaml
import pandas as pd
from src.predict import load_artifacts, predict_movies

if __name__ == "__main__":
    with open("config/config.yaml", "r", encoding="utf8") as f:
        config = yaml.safe_load(f)
    
    model, imputer, vectorizer, director_avg = load_artifacts(config['paths']['model_dir'])
    # глобальное среднее (можно загрузить из метаданных или вычислить)
    global_avg = 6.0  # или из директора
    
    new_movies = [
        {
            'title': "The Batman: Part II",
            'year': 2026,
            'director': "Matt Reeves",
            'runtime': 150,
            'is_remake': False
        },
        {
            'title': "Дюна: Часть Третья",
            'year': 2026,
            'director': "Denis Villeneuve",
            'runtime': 165,
            'is_remake': False
        }
    ]
    
    df = predict_movies(new_movies, model, imputer, vectorizer, director_avg, global_avg)
    print(df.to_string(index=False))