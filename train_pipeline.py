import yaml
import datetime
import json
import os
from src.db_utils import create_or_refresh_cache, get_db_connection
from src.train_incremental import train_model_incremental
from src.logger import setup_logger

logger = setup_logger("PIPELINE")

def main():
    logger.info("===== TRAIN PIPELINE START =====")
    
    with open("config/config.yaml", "r", encoding="utf8") as f:
        config = yaml.safe_load(f)
    
    # 1. Создаём/обновляем кеш
    logger.info("Обновление кеша IMDb...")
    count = create_or_refresh_cache(config)
    logger.info(f"Кеш обновлён. Фильмов: {count}")
    
    # 2. Подключаемся к БД
    conn = get_db_connection(config['data']['cache_db'])
    
    # 3. Обучаем модель
    logger.info("Начало обучения...")
    model, imputer, vectorizer, director_avg = train_model_incremental(conn, config)
    
    # 4. Метаданные
    metadata = {
        "trained_at": datetime.datetime.now().isoformat(),
        "total_rows": count,
        "model_path": os.path.join(config['paths']['model_dir'], config['paths']['model_filename']),
        "vectorizer_path": os.path.join(config['paths']['model_dir'], config['paths']['vectorizer_filename']),
        "imputer_path": os.path.join(config['paths']['model_dir'], config['paths']['imputer_filename']),
        "director_avg_path": os.path.join(config['paths']['model_dir'], config['paths']['director_avg_filename'])
    }
    with open(os.path.join(config['paths']['model_dir'], config['paths']['metadata_filename']), "w") as f:
        json.dump(metadata, f, indent=2)
    
    conn.close()
    logger.info("===== TRAIN PIPELINE FINISHED =====")

if __name__ == "__main__":
    main()