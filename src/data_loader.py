
from pathlib import Path

import pandas as pd
import gzip
import urllib.request

from src.logger import setup_logger

from settings import config

logger = setup_logger(__name__)


def download_imdb_dataset(dataset_name):
    """Download and parse an IMDB dataset."""
    url = f"https://datasets.imdbws.com/{dataset_name}.tsv.gz"

    logger.info(f"Downloading {dataset_name}...")

    data_dir = Path(f"{config.ABSPATH}/data/imdb/")
    data_dir.mkdir(parents=True, exist_ok=True)

    local_path = data_dir / f"{dataset_name}.tsv.gz"

    # Проверяем, существует ли файл локально и не нужно ли принудительно перекачать
    if local_path.exists():
        logger.info(f"Файл {local_path} уже существует. Пропускаем скачивание.")
    else:
        logger.info(f"Скачиваем {dataset_name} с {url} ...")
        try:
            urllib.request.urlretrieve(url, local_path)
            logger.info(f"Файл сохранён в {local_path}")
        except urllib.error.URLError as e:
            logger.error(f"Ошибка при скачивании: {e}")
            raise  # пробрасываем исключение, чтобы остановить выполнение

    return