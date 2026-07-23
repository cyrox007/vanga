import pandas as pd

def load_data(basics_url, ratings_url, crew_url, sample_frac=None, random_state=None):
    """
    Загружает три датасета IMDb.
    Если указан sample_frac, возвращает только часть данных (после объединения).
    """
    df_basics = pd.read_csv(
        basics_url,
        sep='\t',
        compression='gzip',
        dtype={'originalTitle': 'str', 'primaryTitle': 'str'},
        low_memory=False
    )
    df_ratings = pd.read_csv(
        ratings_url,
        sep='\t',
        compression='gzip'
    )
    df_crew = pd.read_csv(
        crew_url,
        sep='\t',
        compression='gzip',
        dtype={'directors': 'str', 'writers': 'str'}
    )
    return df_basics, df_ratings, df_crew