import duckdb
from settings import config


if __name__ == "__main__":
    conn = duckdb.connect(f"{config.ABSPATH}/imdb.duckdb")

    result = conn.execute("""
        SELECT * FROM title_basics 
        WHERE titleType = 'movie' 
        ORDER BY primaryTitle 
        ASC LIMIT 5
        """).fetchdf()
    print(result)

    conn.close()