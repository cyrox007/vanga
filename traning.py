from src.train_model import train_model, interpret_model
from src.data_filtr import get_all_genres

def main():
    genres = get_all_genres()
    model, scaler = train_model(genres)

    feature_names = genres + ['startYear', 'runtimeMinutes', 'numVotes']
    interpret_model(model, scaler, feature_names)

if __name__ == "__main__":
    main()