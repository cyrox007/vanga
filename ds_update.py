from src.create_db import create_duckdb_table_direct
from src.data_loader import download_imdb_dataset

data = [
    "title.basics",
    "title.ratings"
]

if __name__ == "__main__":
    for item in data:
        download_imdb_dataset(item)
        create_duckdb_table_direct(item)
