import pandas as pd

# 1. Загрузка данных с обработкой ошибок
def load_data():
    # Указываем dtype для проблемных колонок и low_memory=False
    df_basics = pd.read_csv(
        'https://datasets.imdbws.com/title.basics.tsv.gz',
        sep='\t',
        compression='gzip',
        dtype={'originalTitle': 'str', 'primaryTitle': 'str'},
        low_memory=False
    )

    df_ratings = pd.read_csv(
        'https://datasets.imdbws.com/title.ratings.tsv.gz',
        sep='\t',
        compression='gzip'
    )

    df_crew = pd.read_csv(
        'https://datasets.imdbws.com/title.crew.tsv.gz',
        sep='\t',
        compression='gzip',
        dtype={'directors': 'str', 'writers': 'str'}
    )

    return df_basics, df_ratings, df_crew