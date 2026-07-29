"""
КиноВанга - модуль предсказания рейтинга фильма.

Модель принимает на вход:
- название фильма (не используется напрямую, но может быть полезно для поиска)
- год выхода
- длительность (в минутах)
- жанр (список или строка через запятую)
- режиссёр (имя)
- до 5 актёров (имена)

Возвращает предсказанный рейтинг (0-10).

ВАЖНО: Статистика по режиссёрам и актёрам вычисляется на лету через DuckDB,
а не загружается из файлов. Это позволяет работать с ограниченной памятью.
"""

from catboost import CatBoostRegressor
import numpy as np
import pickle
from pathlib import Path
from typing import List, Optional, Union
import duckdb

from src.logger import setup_logger
from settings import config

logger = setup_logger(__name__)


class KinoVanga:
    """Класс для предсказания рейтинга фильма."""

    def __init__(self, model_path, db_path=None):
        self.model_path = Path(model_path)
        self.model = None
        self.db_path = (
            Path(db_path)
            if db_path
            else Path(config.ABSPATH) / "imdb.duckdb"
        )
        self._load_model()


    def _load_model(self):
        logger.info(f"Загрузка модели из {self.model_path}")

        # Загружаем CatBoost модель
        self.model = CatBoostRegressor()
        self.model.load_model(self.model_path)

        logger.info("CatBoost модель загружена")

        # Загружаем метаданные
        metadata_path = self.model_path.parent / "metadata.pkl"

        if not metadata_path.exists():
            raise FileNotFoundError(f"Не найден файл метаданных: {metadata_path}")

        with open(metadata_path, "rb") as f:
            self.metadata = pickle.load(f)

        logger.info(f"Метаданные загружены: {metadata_path}")

    def _get_director_rating(self, director_name: str) -> float:
        """
        Получает средний рейтинг режиссёра через DuckDB.

        Args:
            director_name: Имя режиссёра

        Returns:
            Средний рейтинг или NaN, если не найдено
        """
        if not director_name:
            return np.nan

        conn = duckdb.connect(self.db_path)
        query = """
            WITH movie_ratings AS (
                SELECT b.tconst, r.averageRating
                FROM title_basics b
                JOIN title_ratings r ON b.tconst = r.tconst
                WHERE b.titleType = 'movie' AND r.averageRating IS NOT NULL
            )
            SELECT AVG(mr.averageRating) AS avg_rating
            FROM title_principals p
            JOIN movie_ratings mr ON p.tconst = mr.tconst
            JOIN name_basics n ON p.nconst = n.nconst
            WHERE p.category = 'director'
              AND LOWER(n.primaryName) = LOWER(?)
            GROUP BY p.nconst
            HAVING COUNT(*) >= 2
            LIMIT 1
        """
        try:
            result = conn.execute(query, [director_name]).fetchone()
            conn.close()
            if result and result[0] is not None:
                return result[0]
        except Exception as e:
            logger.warning(f"Ошибка при получении рейтинга режиссёра {director_name}: {e}")
            conn.close()
        return np.nan

    def _get_actor_rating(self, actor_name: str) -> float:
        """
        Получает средний рейтинг актёра через DuckDB.

        Args:
            actor_name: Имя актёра

        Returns:
            Средний рейтинг или NaN, если не найдено
        """
        if not actor_name:
            return np.nan

        conn = duckdb.connect(self.db_path)
        query = """
            WITH movie_ratings AS (
                SELECT b.tconst, r.averageRating
                FROM title_basics b
                JOIN title_ratings r ON b.tconst = r.tconst
                WHERE b.titleType = 'movie' AND r.averageRating IS NOT NULL
            )
            SELECT AVG(mr.averageRating) AS avg_rating
            FROM title_principals p
            JOIN movie_ratings mr ON p.tconst = mr.tconst
            JOIN name_basics n ON p.nconst = n.nconst
            WHERE p.category IN ('actor', 'actress')
              AND LOWER(n.primaryName) = LOWER(?)
            GROUP BY p.nconst
            HAVING COUNT(*) >= 3
            LIMIT 1
        """
        try:
            result = conn.execute(query, [actor_name]).fetchone()
            conn.close()
            if result and result[0] is not None:
                return result[0]
        except Exception as e:
            logger.warning(f"Ошибка при получении рейтинга актёра {actor_name}: {e}")
            conn.close()
        return np.nan

    def _prepare_features(
        self,
        year: int,
        runtime: int,
        genres: Union[str, List[str]],
        director: Optional[str] = None,
        actors: Optional[List[str]] = None,
        num_votes: Optional[int] = None
    ):

        if isinstance(genres, list):
            genres_combined = ",".join(genres)
        else:
            genres_combined = genres or "Unknown"


        # количество голосов
        if num_votes is None:
            num_votes_log = 7.5
        else:
            num_votes_log = np.log1p(num_votes)


        # режиссёр
        director_avg_rating = self._get_director_rating(director)

        if np.isnan(director_avg_rating):
            director_avg_rating = 6.5


        # актёры
        actor_ratings = []

        actors = actors or []

        for i in range(3):
            if i < len(actors):
                rating = self._get_actor_rating(actors[i])

                if np.isnan(rating):
                    rating = 6.5

            else:
                rating = 6.5

            actor_ratings.append(rating)


        director_id = director or "Unknown"


        actor_ids_combined = ",".join(
            actors[:5]
        ) if actors else "Unknown"


        data = [
            year,
            runtime,
            num_votes_log,
            director_avg_rating,
            actor_ratings[0],
            actor_ratings[1],
            actor_ratings[2],
            genres_combined,
            director_id,
            actor_ids_combined
        ]


        return np.array(data, dtype=object).reshape(1, -1)

    def predict(
        self,
        year: int,
        runtime: int,
        genres: Union[str, List[str]],
        director: Optional[str] = None,
        actors: Optional[List[str]] = None,
        num_votes: Optional[int] = None,
        title: Optional[str] = None
    ) -> float:
        """
        Предсказывает рейтинг фильма.

        Args:
            title: Название фильма (не используется в модели, но может быть полезно для логов)
            year: Год выхода
            runtime: Длительность в минутах
            genres: Жанр (строка через запятую или список)
            director: Имя режиссёра
            actors: Список имён актёров (до 5)
            num_votes: Количество голосов (для новых фильмов можно не указывать)

        Returns:
            Предсказанный рейтинг (0-10)
        """
        if title:
            logger.info(f"Предсказание для фильма: {title} ({year})")

        # Подготовка признаков
        X = self._prepare_features(year, runtime, genres, director, actors, num_votes)

        # Проверка размерности
        expected_features = len(self.metadata['feature_names'])
        if X.shape[1] != expected_features:
            raise ValueError(
                f"Ожидалось {expected_features} признаков, получено {X.shape[1]}. "
                f"Проверьте корректность входных данных."
            )

        # Предсказание
        rating = self.model.predict(X)[0]

        # Ограничиваем диапазон 0-10
        rating = max(0, min(10, rating))

        logger.info(f"Предсказанный рейтинг: {rating:.2f}")

        return round(rating, 2)

    def predict_batch(self, movies: List[dict]) -> List[float]:
        """
        Предсказывает рейтинги для нескольких фильмов.

        Args:
            movies: Список словарей с параметрами фильмов

        Returns:
            Список предсказанных рейтингов
        """
        ratings = []
        for movie in movies:
            rating = self.predict(**movie)
            ratings.append(rating)
        return ratings

    def get_feature_importance(self) -> dict:
        """
        Возвращает важность признаков (коэффициенты модели).

        Returns:
            Словарь {признак: коэффициент}
        """
        coef_scaled = self.model.coef_ / self.scaler.scale_
        feature_names = self.metadata['feature_names']

        importance = dict(zip(feature_names, coef_scaled))

        # Сортируем по абсолютному значению
        importance_sorted = sorted(importance.items(), key=lambda x: abs(x[1]), reverse=True)

        return dict(importance_sorted)

    def explain_prediction(
        self,
        year: int,
        runtime: int,
        genres: Union[str, List[str]],
        director: Optional[str] = None,
        actors: Optional[List[str]] = None,
        num_votes: Optional[int] = None,
        title: Optional[str] = None
    ) -> dict:
        """
        Объясняет предсказание, показывая вклад каждого признака.

        Returns:
            Словарь с объяснением предсказания
        """
        X = self._prepare_features(year, runtime, genres, director, actors, num_votes)
        X_scaled = self.scaler.transform(X)

        # Вклад каждого признака
        contributions = X_scaled[0] * self.model.coef_

        feature_names = self.metadata['feature_names']
        explanation = {}

        for name, contrib in zip(feature_names, contributions):
            explanation[name] = contrib

        # Добавляем базовое значение (intercept)
        explanation['base_value'] = self.model.intercept_[0]
        explanation['predicted_rating'] = self.predict(year, runtime, genres, director, actors, num_votes, title)

        # Топ-5 положительных и отрицательных влияний
        sorted_contrib = sorted(explanation.items(), key=lambda x: x[1], reverse=True)
        explanation['top_positive'] = sorted_contrib[:5]
        explanation['top_negative'] = sorted_contrib[-5:]

        return explanation


# Удобная функция для быстрого использования
def predict_movie_rating(
    title: str,
    year: int,
    runtime: int,
    genres: Union[str, List[str]],
    director: str,
    actors: List[str] = None,
    model_path: Optional[str] = None
) -> float:
    """
    Быстрое предсказание рейтинга фильма.

    Args:
        title: Название фильма
        year: Год выхода
        runtime: Длительность в минутах
        genres: Жанр (строка через запятую или список)
        director: Имя режиссёра
        actors: Список имён актёров (до 5)
        model_path: Путь к модели (опционально)

    Returns:
        Предсказанный рейтинг
    """
    kino = KinoVanga(model_path=model_path)
    return kino.predict(
        year=year,
        runtime=runtime,
        genres=genres,
        director=director,
        actors=actors,
        title=title
    )