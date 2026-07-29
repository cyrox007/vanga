from src.train_model import save_trained_model, train_model, interpret_model
from src.data_filtr import get_all_genres

def main():
    # Ограничиваем параметры для экономии памяти
    genres = get_all_genres()
    model, scaler, director_avg, actor_avg, tconst_to_people, nconst_mapping = train_model(
        genres,
        batch_size=5000,  # уменьшенный размер батча
        max_batches=10    # ограничиваем количество батчей
    )

    feature_names = genres + ['startYear', 'runtimeMinutes', 'numVotes', 'director_avg_rating'] + \
                    [f'actor_{i+1}_avg_rating' for i in range(5)]

    interpret_model(model, scaler, feature_names)

    # Сохраняем модель и все метаданные
    save_trained_model(model, scaler, genres, director_avg, actor_avg, tconst_to_people, nconst_mapping)

if __name__ == "__main__":
    main()