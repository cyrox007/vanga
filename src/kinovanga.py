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
"""

import numpy as np
import pickle
from pathlib import Path
from typing import List, Optional, Union

from src.logger import setup_logger
from settings import config

logger = setup_logger(__name__)


class KinoVanga:
    """Класс для предсказания рейтинга фильма."""

    def __init__(self, model_path: Optional[str] = None):
        """
        Инициализация модели.

        Args:
            model_path: Путь к директории с моделью. Если None, используется models/ в корне проекта.
        """
        if model_path is None:
            self.model_dir = Path(f"{config.ABSPATH}/models")
        else:
            self.model_dir = Path(model_path)

        self.model = None
        self.scaler = None
        self.metadata = None
        self.director_avg = None
        self.actor_avg = None
        self.tconst_to_people = None
        self.nconst_mapping = None
        self.name_to_nconst = None

        self._load_model()

    def _load_model(self):
        """Загружает модель и все необходимые метаданные."""
        logger.info(f"Загрузка модели из {self.model_dir}")

        with open(self.model_dir / 'model.pkl', 'rb') as f:
            self.model = pickle.load(f)

        with open(self.model_dir / 'scaler.pkl', 'rb') as f:
            self.scaler = pickle.load(f)

        with open(self.model_dir / 'metadata.pkl', 'rb') as f:
            self.metadata = pickle.load(f)

        with open(self.model_dir / 'director_avg.pkl', 'rb') as f:
            self.director_avg = pickle.load(f)

        with open(self.model_dir / 'actor_avg.pkl', 'rb') as f:
            self.actor_avg = pickle.load(f)

        with open(self.model_dir / 'tconst_to_people.pkl', 'rb') as f:
            self.tconst_to_people = pickle.load(f)

        with open(self.model_dir / 'nconst_mapping.pkl', 'rb') as f:
            self.nconst_mapping = pickle.load(f)

        # Создаём обратный маппинг имя -> nconst
        self.name_to_nconst = {v.lower().strip(): k for k, v in self.nconst_mapping.items()}

        logger.info("Модель загружена успешно")

    def _find_nconst_by_name(self, name: str, is_director: bool = False) -> Optional[str]:
        """
        Ищет nconst по имени человека.

        Args:
            name: Имя человека
            is_director: Если True, проверяем только среди режиссёров

        Returns:
            nconst или None, если не найдено
        """
        if not name:
            return None

        name_lower = name.lower().strip()

        # Прямой поиск
        if name_lower in self.name_to_nconst:
            return self.name_to_nconst[name_lower]

        # Поиск по частичному совпадению
        for name_variant, nconst in self.name_to_nconst.items():
            if name_lower in name_variant or name_variant in name_lower:
                return nconst

        return None

    def _get_director_rating(self, director_name: str) -> float:
        """
        Получает средний рейтинг режиссёра.

        Args:
            director_name: Имя режиссёра

        Returns:
            Средний рейтинг или NaN, если не найдено
        """
        nconst = self._find_nconst_by_name(director_name, is_director=True)
        if nconst and nconst in self.director_avg:
            return self.director_avg[nconst]
        return np.nan

    def _get_actor_rating(self, actor_name: str) -> float:
        """
        Получает средний рейтинг актёра.

        Args:
            actor_name: Имя актёра

        Returns:
            Средний рейтинг или NaN, если не найдено
        """
        nconst = self._find_nconst_by_name(actor_name)
        if nconst and nconst in self.actor_avg:
            return self.actor_avg[nconst]
        return np.nan

    def _prepare_features(
        self,
        year: int,
        runtime: int,
        genres: Union[str, List[str]],
        director: Optional[str] = None,
        actors: Optional[List[str]] = None,
        num_votes: Optional[int] = None
    ) -> np.ndarray:
        """
        Подготавливает вектор признаков для предсказания.

        Args:
            year: Год выхода фильма
            runtime: Длительность в минутах
            genres: Жанр (строка через запятую или список)
            director: Имя режиссёра (опционально)
            actors: Список имён актёров (до 5, опционально)
            num_votes: Количество голосов (опционально, для новых фильмов можно поставить среднее)

        Returns:
            Вектор признаков
        """
        # Получаем список жанров
        if isinstance(genres, str):
            genre_list = [g.strip() for g in genres.split(',')]
        else:
            genre_list = genres

        # Создаём вектор жанров
        all_genres = self.metadata['genres']
        genre_vector = [1 if g in genre_list else 0 for g in all_genres]

        # Числовые признаки
        start_year_scaled = (year - 1900) / 100.0
        runtime_scaled = runtime / 100.0

        # Если num_votes не указан, используем медианное значение (примерно 7.5 в логарифме)
        if num_votes is None:
            num_votes_log = 7.5  # log1p(~1800)
        else:
            num_votes_log = np.log1p(num_votes)

        # Признак режиссёра (если не найден - используем среднее ~6.5)
        director_rating = self._get_director_rating(director) if director else np.nan
        if np.isnan(director_rating):
            director_rating = 6.5  # Средний рейтинг по умолчанию

        # Признаки актёров (до 5)
        actor_ratings = []
        if actors:
            for i in range(5):
                if i < len(actors):
                    rating = self._get_actor_rating(actors[i])
                    if np.isnan(rating):
                        rating = 6.5  # Средний рейтинг по умолчанию
                else:
                    rating = 6.5  # Если актёра нет, используем среднее
                actor_ratings.append(rating)
        else:
            actor_ratings = [6.5] * 5  # Все актёры неизвестны - среднее

        # Собираем все признаки
        features = genre_vector + [start_year_scaled, runtime_scaled, num_votes_log, director_rating] + actor_ratings

        return np.array(features).reshape(1, -1)

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