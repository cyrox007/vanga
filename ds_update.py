from src.create_db import create_duckdb_table_direct
from src.data_loader import download_imdb_dataset
import threading


data = [
    "title.basics",
    "title.ratings",
    "title.principals",
    'name.basics'
]

def main(item: str):   
    download_imdb_dataset(item)
    create_duckdb_table_direct(item)

if __name__ == "__main__":
    threads = []
    for item in data:
        thread = threading.Thread(target=main, args=(item,))
        threads.append(thread)
        thread.start()

    for t in threads:
        t.join()