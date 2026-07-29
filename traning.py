from src.train_model import save_trained_model, train_catboost_model, interpret_model
from src.data_filtr import get_all_genres
from src.logger import setup_logger

logger = setup_logger(__name__)

def main():
    """
    Основная функция обучения модели CatBoost.
    Оптимизирована для работы с категориальными признаками (жанры, режиссеры, актеры).
    """
    logger.info("=" * 60)
    logger.info("ЗАПУСК ОБУЧЕНИЯ CATBOOST")
    logger.info("=" * 60)

    # Получаем список жанров
    logger.info("Получение списка жанров...")
    genres = get_all_genres()
    logger.info(f"Найдено {len(genres)} жанров: {genres}")

    # Параметры обучения
    batch_size = 10000  # размер батча для CatBoost
    max_batches = None  # обучаем на всех данных (или укажи лимит для теста)

    logger.info(f"Параметры обучения: batch_size={batch_size}, max_batches={max_batches}")
    logger.info("=" * 60)

    # Обучаем модель CatBoost
    logger.info("Начало обучения...")
    model, metadata = train_catboost_model(
        genres,
        batch_size=batch_size,
        max_batches=max_batches
    )
    
    if model is None:
        logger.error("Обучение не удалось")
        return

    # Интерпретируем модель
    logger.info("Интерпретация модели...")
    interpret_model(model, metadata)

    # Сохраняем модель и метаданные
    logger.info("Сохранение модели...")
    save_trained_model(model, metadata)

    logger.info("=" * 60)
    logger.info("ОБУЧЕНИЕ CATBOOST ЗАВЕРШЕНО УСПЕШНО")
    logger.info("=" * 60)

if __name__ == "__main__":
    main()