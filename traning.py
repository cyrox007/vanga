from src.train_model import save_trained_model, train_model, interpret_model
from src.data_filtr import get_all_genres
from src.logger import setup_logger

logger = setup_logger(__name__)

def main():
    """
    Основная функция обучения модели.
    Оптимизирована для работы на ограниченных ресурсах (1 ГБ ОЗУ).
    """
    logger.info("=" * 60)
    logger.info("ЗАПУСК ОБУЧЕНИЯ МОДЕЛИ")
    logger.info("=" * 60)

    # Получаем список жанров
    logger.info("Получение списка жанров...")
    genres = get_all_genres()
    logger.info(f"Найдено {len(genres)} жанров: {genres}")

    # Параметры для экономии памяти
    batch_size = 5000  # уменьшенный размер батча
    max_batches = 10   # ограничиваем количество батчей для теста

    logger.info(f"Параметры обучения: batch_size={batch_size}, max_batches={max_batches}")
    logger.info("=" * 60)

    # Обучаем модель
    logger.info("Начало обучения...")
    model, scaler, director_avg, actor_avg, tconst_to_people, nconst_mapping = train_model(
        genres,
        batch_size=batch_size,
        max_batches=max_batches
    )

    # Создаём список имён признаков
    feature_names = genres + ['startYear', 'runtimeMinutes', 'numVotes', 'director_avg_rating'] + \
                    [f'actor_{i+1}_avg_rating' for i in range(5)]
    logger.info(f"Создан список признаков ({len(feature_names)}): {feature_names[:10]}...")

    # Интерпретируем модель
    logger.info("Интерпретация модели...")
    interpret_model(model, scaler, feature_names)

    # Сохраняем модель и все метаданные
    logger.info("Сохранение модели...")
    save_trained_model(model, scaler, genres)

    logger.info("=" * 60)
    logger.info("ОБУЧЕНИЕ ЗАВЕРШЕНО УСПЕШНО")
    logger.info("=" * 60)

if __name__ == "__main__":
    main()