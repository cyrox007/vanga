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
from src.normalize import extract_title_features, normalize_genre_str

logger = setup_logger(__name__)


class KinoVanga:
    """Класс для предсказания рейтинга фильма."""

    def __init__(self, model_path, db_path=None):
        self.model_path = Path(model_path)
        self.model = None
        self.db_path = Path(db_path) if db_path else Path(config.ABSPATH) / "imdb.duckdb"
        self.director_cache = {}
        self.actor_cache = {}
        self._people_cache = {}  # общий кэш для всех персон
        # Открываем соединение один раз
        self.conn = duckdb.connect(str(self.db_path), read_only=True)
        # Настройки памяти (опционально)
        self.conn.execute("SET memory_limit = '400MB'")
        self.conn.execute("SET threads = 2")
        self._load_model()

    def __del__(self):
        if hasattr(self, 'conn') and self.conn:
            self.conn.close()

    def _get_people_info(self, names: List[str]) -> dict:
        """
        Получает nconst и средний рейтинг для списка имён за один SQL-запрос.
        """
        if not names:
            return {}
        clean_names = [n for n in names if n and n.strip()]
        if not clean_names:
            return {}

        # Проверяем кэш
        cached = {}
        missing = []
        for name in clean_names:
            if name in self._people_cache:
                cached[name] = self._people_cache[name]
            else:
                missing.append(name)
        if not missing:
            return cached

        # Формируем запрос для недостающих имён
        placeholders = ','.join(['?' for _ in missing])
        query = f"""
            WITH person_names AS (
                SELECT unnest(?) AS name
            ),
            person_ids AS (
                SELECT DISTINCT
                    n.nconst,
                    n.primaryName
                FROM name_basics n
                JOIN person_names pn ON LOWER(n.primaryName) = LOWER(pn.name)
            ),
            person_stats AS (
                SELECT
                    pi.nconst,
                    pi.primaryName,
                    AVG(r.averageRating) AS avg_rating
                FROM person_ids pi
                LEFT JOIN title_principals tp ON pi.nconst = tp.nconst
                LEFT JOIN title_basics b ON tp.tconst = b.tconst
                LEFT JOIN title_ratings r ON b.tconst = r.tconst
                WHERE b.titleType = 'movie'
                  AND r.averageRating IS NOT NULL
                GROUP BY pi.nconst, pi.primaryName
            )
            SELECT
                ps.primaryName,
                ps.nconst,
                COALESCE(ps.avg_rating, 6.5) AS avg_rating
            FROM person_stats ps
            UNION ALL
            SELECT
                pn.name AS primaryName,
                NULL AS nconst,
                6.5 AS avg_rating
            FROM person_names pn
            WHERE NOT EXISTS (
                SELECT 1 FROM person_ids pi WHERE LOWER(pi.primaryName) = LOWER(pn.name)
            )
        """
        rows = self.conn.execute(query, [missing]).fetchall()
        for row in rows:
            name, nconst, avg_rating = row
            info = {'nconst': nconst, 'avg_rating': avg_rating}
            self._people_cache[name] = info
            cached[name] = info
        return cached


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

    def _prepare_features(
        self,
        year: int,
        runtime: int,
        genres: Union[str, List[str]],
        director: Optional[str] = None,
        actors: Optional[List[str]] = None,
        num_votes: Optional[int] = None,
        title: Optional[str] = None
    ):
        import time
        t0 = time.perf_counter()
        logger.info("========== НАЧАЛО _prepare_features ==========")

        # 1. Нормализация жанров
        if isinstance(genres, list):
            genres_str = ",".join(genres)
        else:
            genres_str = genres or ""
        genres_combined = normalize_genre_str(genres_str)

        # 2. Признаки из названия (если модель обучена с ними)
        title_features_dict = {}
        if title:
            # Извлекаем все признаки из названия (функция возвращает словарь)
            raw_title_features = extract_title_features(title)
            # Оставляем только те, что присутствуют в метаданных модели
            feature_names_set = set(self.metadata.get('feature_names', []))
            for key, val in raw_title_features.items():
                if key in feature_names_set:
                    title_features_dict[key] = val

        # 3. Логарифм голосов
        num_votes_log = 7.5 if num_votes is None else np.log1p(num_votes)

        # 4. Получаем информацию о всех персонах за один запрос
        # 4. Получение информации о персонах (режиссёр, актёры)
        person_names = []
        if director:
            person_names.append(director)
        if actors:
            person_names.extend(actors[:3])
        people_info = self._get_people_info(person_names) if person_names else {}

        director_info = people_info.get(director, {}) if director else {}
        director_nconst = director_info.get('nconst')
        director_avg_rating = director_info.get('avg_rating', 6.5)

        # 6. Данные актёров (до 3)
        actor_infos = []
        if actors:
            for actor in actors[:3]:
                info = people_info.get(actor, {})
                actor_infos.append({'nconst': info.get('nconst'), 'avg_rating': info.get('avg_rating', 6.5)})
        # Дополняем до 3, если не хватает
        while len(actor_infos) < 3:
            actor_infos.append({'nconst': None, 'avg_rating': 6.5})

        actor_ratings = [a['avg_rating'] for a in actor_infos]
        actor_nconsts = [a['nconst'] for a in actor_infos]

        # 7. Категориальные признаки (используем nconst, а не имена)
        director_id = director_nconst if director_nconst else 'Unknown'
        actor_ids_combined = ','.join([n for n in actor_nconsts if n]) or 'Unknown'

        logger.info(f"director_id = {director_id} (было имя: {director})")
        logger.info(f"actor_ids_combined = {actor_ids_combined} ({",".join(actors)})")

        # 8. Итоговый массив признаков
        features = {
            'startYear': year,
            'runtimeMinutes': runtime,
            'numVotes_log': num_votes_log,
            'director_avg_rating': director_avg_rating,
            'actor_1_avg_rating': actor_ratings[0],
            'actor_2_avg_rating': actor_ratings[1],
            'actor_3_avg_rating': actor_ratings[2],
            'genres_combined': genres_combined,
            'director_id': director_id,
            'actor_ids_combined': actor_ids_combined,
        }
        # Добавляем признаки из названия, если они есть
        features.update(title_features_dict)

        feature_names = self.metadata['feature_names']
        data = []
        for name in feature_names:
            if name in features:
                data.append(features[name])
            else:
                # Если признак отсутствует (не должен случиться), ставим разумное значение по умолчанию
                if name in ['genres_combined', 'director_id', 'actor_ids_combined']:
                    data.append('Unknown')
                else:
                    data.append(0.0)

        X = np.array(data, dtype=object).reshape(1, -1)
        logger.info(f"_prepare_features завершён за {time.perf_counter()-t0:.3f} сек")
        return X

    def predict(self, year, runtime, genres, director=None, 
                actors=None, num_votes=None, title=None) -> float:
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
        X = self._prepare_features(year, runtime, genres, director, actors, num_votes, title=title)

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

    def explain_prediction(self, year, runtime, genres, director=None, 
                           actors=None, num_votes=None, title=None) -> dict:
        """
        Объясняет предсказание, показывая вклад каждого признака.

        Returns:
            Словарь с объяснением предсказания
        """
        X = self._prepare_features(year, runtime, genres, director, actors, num_votes, title=title)
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