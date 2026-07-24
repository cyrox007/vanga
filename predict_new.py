import yaml
import pandas as pd
from src.predict import load_artifacts, predict_movies  # новые имена

if __name__ == "__main__":
    with open("config/config.yaml", "r", encoding="utf8") as f:
        config = yaml.safe_load(f)
    
    model, imputer, scaler, vectorizer, director_avg = load_artifacts(config['paths']['model_dir'])
    # глобальное среднее (можно загрузить из метаданных)
    global_avg = 6.0  # или вычислить из director_avg
    
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
        },
        {
            'title': "Avatar 3",
            'year': 2026,
            'director': "James Cameron",
            'runtime': 170,
            'is_remake': False
        }
    ]
    
    df = predict_movies(new_movies, model, imputer, scaler, vectorizer, director_avg, global_avg)
    print("\nПредсказанные рейтинги:")
    print(df.to_string(index=False))