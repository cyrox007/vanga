from src.train_model import train_catboost_model, save_trained_model, interpret_model
from src.data_filtr import get_all_genres
from src.logger import setup_logger

logger = setup_logger(__name__)

def main():
    logger.info("=" * 60)
    logger.info("ЗАПУСК ОБУЧЕНИЯ CATBOOST")
    logger.info("=" * 60)

    # 2. Получаем список жанров
    genres = get_all_genres()
    logger.info(f"Найдено жанров: {len(genres)}")

    # 3. Обучаем модель
    model, metadata = train_catboost_model(genres, batch_size=10000, max_batches=None)

    # 4. Интерпретация
    interpret_model(model, metadata)

    # 5. Сохраняем модель и метаданные
    save_trained_model(model, metadata)

    logger.info("ОБУЧЕНИЕ ЗАВЕРШЕНО УСПЕШНО")

if __name__ == "__main__":
    main()