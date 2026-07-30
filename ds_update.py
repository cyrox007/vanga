from src.create_db import create_duckdb_table_direct, create_indexes
from src.data_loader import download_imdb_dataset
import threading

from src.database import cleanup_temp


data = [
    "title.basics",
    "title.ratings",
    "title.principals",
    'name.basics'
]

def main() -> None:   
    download_threads: list[threading.Thread] = []
    for item in data:
        thread = threading.Thread(target=download_imdb_dataset, args=(item,))
        download_threads.append(thread)
        thread.start()

    for t in download_threads:
        t.join()

    
    create_duckdb_table_direct('title.basics')
    creating_threads: list[threading.Thread] = []
    for item in ["title.ratings", "title.principals"]:
        thread = threading.Thread(target=create_duckdb_table_direct, args=(item,))
        creating_threads.append(thread)
        thread.start()

    for t in creating_threads:
        t.join()

    create_duckdb_table_direct('name.basics')
    create_indexes()
    cleanup_temp()

if __name__ == "__main__":
    main()