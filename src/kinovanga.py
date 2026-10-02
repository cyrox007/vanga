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

from catboost import CatBoostRegressor, Pool
import numpy as np
import pickle
from pathlib import Path
from typing import List, Optional, Union
import duckdb

from src.logger import setup_logger
from settings import config
from src.catalog import CatalogSearch
from src.input_aliases import RussianInputResolver
from src.normalize import extract_title_features, normalize_genre_str

logger = setup_logger(__name__)


class KinoVanga:
    """Класс для предсказания рейтинга фильма."""

    def __init__(self, model_path, db_path=None):
        self.model_path = Path(model_path)
        self.model = None
        self.db_path = Path(db_path) if db_path else Path(config.IMDB_DB_PATH)
        self.director_cache = {}
        self.actor_cache = {}
        self._people_cache = {}  # общий кэш для всех персон
        # Открываем соединение один раз
        self.conn = duckdb.connect(str(self.db_path), read_only=True)
        # Inference держит собственное соединение: catalog search не должен
        # блокировать запрос модели во время внешнего alias lookup.
        self.conn.execute("SET memory_limit = '400MB'")
        self.conn.execute("SET threads = 2")

        self.catalog_conn = duckdb.connect(str(self.db_path), read_only=True)
        self.catalog_conn.execute("SET memory_limit = '200MB'")
        self.catalog_conn.execute("SET threads = 1")

        self.input_resolver = RussianInputResolver(self.conn)
        self.catalog_resolver = RussianInputResolver(self.catalog_conn)
        self.catalog = CatalogSearch(self.catalog_conn, self.catalog_resolver)
        self._load_model()

    def close(self) -> None:
        """Закрывает read-only соединения модели и каталога."""
        for attr in ("conn", "catalog_conn"):
            connection = getattr(self, attr, None)
            if connection is None:
                continue
            try:
                connection.close()
            except Exception:
                logger.exception("Не удалось закрыть DuckDB connection %s", attr)
            finally:
                setattr(self, attr, None)

    def __del__(self):
        self.close()

    def _get_people_info(
        self,
        names: List[str],
        before_year: int,
        *,
        role: str,
    ) -> dict:
        """Возвращает ID и history-рейтинг персоны в той же роли, что training.

        training считает director_avg_rating только по работам director, а
        actor_N_avg_rating — только по actor/actress. Inference обязан делать
        то же самое, иначе один и тот же feature означает разные вещи.
        """
        clean_names = [name.strip() for name in names if name and name.strip()]
        if not clean_names:
            return {}
        if role not in {"director", "actor"}:
            raise ValueError("role должен быть director или actor")

        categories = (
            ("director",)
            if role == "director"
            else ("actor", "actress")
        )
        placeholders = ",".join("?" for _ in categories)

        cached: dict[str, dict] = {}
        missing: list[str] = []

        for name in clean_names:
            cache_key = (role, name.casefold(), int(before_year))
            if cache_key in self._people_cache:
                cached[name] = self._people_cache[cache_key]
            else:
                missing.append(name)

        if not missing:
            return cached

        query = f"""
            WITH person_names AS (
                SELECT unnest(?) AS name
            ),
            person_ids AS (
                SELECT
                    pn.name AS requested_name,
                    n.nconst,
                    n.primaryName
                FROM person_names pn
                LEFT JOIN name_basics n
                  ON LOWER(n.primaryName) = LOWER(pn.name)
            ),
            person_stats AS (
                SELECT
                    pi.requested_name,
                    pi.nconst,
                    AVG(TRY_CAST(r.averageRating AS DOUBLE)) AS avg_rating,
                    COUNT(DISTINCT tp.tconst) AS works_count
                FROM person_ids pi
                LEFT JOIN title_principals tp
                  ON tp.nconst = pi.nconst
                 AND tp.category IN ({placeholders})
                LEFT JOIN title_basics b
                  ON b.tconst = tp.tconst
                LEFT JOIN title_ratings r
                  ON r.tconst = b.tconst
                WHERE pi.nconst IS NOT NULL
                  AND b.titleType = 'movie'
                  AND TRY_CAST(b.startYear AS INTEGER) < ?
                  AND TRY_CAST(r.averageRating AS DOUBLE) IS NOT NULL
                GROUP BY pi.requested_name, pi.nconst
            ),
            ranked AS (
                SELECT
                    requested_name,
                    nconst,
                    avg_rating,
                    works_count,
                    ROW_NUMBER() OVER (
                        PARTITION BY LOWER(requested_name)
                        ORDER BY
                            works_count DESC,
                            avg_rating DESC NULLS LAST,
                            nconst
                    ) AS rn
                FROM person_stats
            )
            SELECT
                pn.name,
                r.nconst,
                COALESCE(r.avg_rating, 6.5)
            FROM person_names pn
            LEFT JOIN ranked r
              ON LOWER(r.requested_name) = LOWER(pn.name)
             AND r.rn = 1
        """

        rows = self.conn.execute(
            query,
            [missing, *categories, int(before_year)],
        ).fetchall()
        for name, nconst, avg_rating in rows:
            info = {
                "nconst": nconst,
                "avg_rating": (
                    float(avg_rating)
                    if avg_rating is not None
                    else 6.5
                ),
            }
            cache_key = (role, str(name).casefold(), int(before_year))
            self._people_cache[cache_key] = info
            cached[str(name)] = info

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

    def quality_summary(self) -> dict:
        """Возвращает проверяемые метрики активной модели из metadata.pkl."""
        metadata = self.metadata if isinstance(self.metadata, dict) else {}

        def _float(name: str):
            value = metadata.get(name)
            try:
                return float(value) if value is not None else None
            except (TypeError, ValueError):
                return None

        def _int(name: str):
            value = metadata.get(name)
            try:
                return int(value) if value is not None else None
            except (TypeError, ValueError):
                return None

        return {
            "mae": _float("test_mae"),
            "rmse": _float("test_rmse"),
            "r2": _float("test_r2"),
            "test_year_from": _int("test_year_from"),
            "test_year_to": _int("test_year_to"),
            "test_rows": _int("test_rows"),
            "train_year_from": _int("train_year_from"),
            "train_year_to": _int("train_year_to"),
            "train_rows": _int("train_rows"),
        }

    def uncertainty_for_rating(self, rating: float) -> dict | None:
        """Строит эмпирический диапазон по ошибкам temporal holdout.

        Диапазон не является вероятностью конкретного фильма. Он показывает,
        какую абсолютную ошибку модель не превышала примерно в указанной доле
        объектов временной тестовой выборки.
        """
        metadata = self.metadata if isinstance(self.metadata, dict) else {}
        quantiles = metadata.get("test_abs_error_quantiles")
        if not isinstance(quantiles, dict):
            return None

        try:
            margin = float(quantiles["q80"])
        except (KeyError, TypeError, ValueError):
            return None
        if not np.isfinite(margin) or margin < 0:
            return None

        coverage_raw = metadata.get("uncertainty_default_coverage", 0.80)
        try:
            coverage = float(coverage_raw)
        except (TypeError, ValueError):
            coverage = 0.80
        coverage = max(0.0, min(1.0, coverage))

        center = float(rating)
        return {
            "lower": round(max(0.0, center - margin), 2),
            "upper": round(min(10.0, center + margin), 2),
            "margin": round(margin, 2),
            "coverage": round(coverage, 2),
            "method": str(
                metadata.get("uncertainty_method")
                or "temporal_holdout_absolute_error"
            ),
            "test_year_from": metadata.get("test_year_from"),
            "test_year_to": metadata.get("test_year_to"),
            "test_rows": metadata.get("test_rows"),
        }


    def _prepare_features(
        self,
        year: int,
        runtime: int,
        genres: Union[str, List[str]],
        director: Optional[str] = None,
        actors: Optional[List[str]] = None,
        num_votes: Optional[int] = None,
        title: Optional[str] = None,
    ):
        """Готовит признаки в точности в том же виде, что и training pipeline."""
        import time

        del num_votes  # пострелизный признак намеренно не используется

        t0 = time.perf_counter()
        actors = actors or []

        genres_str = ",".join(genres) if isinstance(genres, list) else (genres or "")
        genres_combined = normalize_genre_str(genres_str)

        feature_names_set = set(self.metadata.get("feature_names", []))
        title_features_dict = {}
        if title:
            for key, value in extract_title_features(title).items():
                if key in feature_names_set:
                    title_features_dict[key] = value

        director_people = (
            self._get_people_info(
                [director],
                before_year=int(year),
                role="director",
            )
            if director
            else {}
        )
        actor_people = (
            self._get_people_info(
                actors[:3],
                before_year=int(year),
                role="actor",
            )
            if actors
            else {}
        )

        director_info = director_people.get(director, {}) if director else {}
        director_id = director_info.get("nconst") or "Unknown"
        director_avg_rating = float(director_info.get("avg_rating", 6.5))

        actor_infos: list[dict] = []
        for actor in actors[:3]:
            info = actor_people.get(actor, {})
            actor_infos.append(
                {
                    "nconst": info.get("nconst") or "Unknown",
                    "avg_rating": float(info.get("avg_rating", 6.5)),
                }
            )
        while len(actor_infos) < 3:
            actor_infos.append({"nconst": "Unknown", "avg_rating": 6.5})

        features = {
            "startYear": (int(year) - 1900) / 100.0,
            "runtimeMinutes": int(runtime) / 100.0,
            "director_avg_rating": director_avg_rating,
            "actor_1_avg_rating": actor_infos[0]["avg_rating"],
            "actor_2_avg_rating": actor_infos[1]["avg_rating"],
            "actor_3_avg_rating": actor_infos[2]["avg_rating"],
            "genres_combined": genres_combined,
            "director_id": director_id,
            "actor_1_id": actor_infos[0]["nconst"],
            "actor_2_id": actor_infos[1]["nconst"],
            "actor_3_id": actor_infos[2]["nconst"],
        }
        features.update(title_features_dict)

        categorical = set(self.metadata.get("categorical_features", []))
        data = []
        for name in self.metadata["feature_names"]:
            if name in features:
                data.append(features[name])
            elif name in categorical:
                data.append("Unknown")
            else:
                data.append(0.0)

        X = np.array(data, dtype=object).reshape(1, -1)
        logger.info(
            f"_prepare_features завершён за {time.perf_counter() - t0:.3f} сек"
        )
        return X


    def predict(self, year, runtime, genres, director=None,
            actors=None, num_votes=None, title=None, explain=False) -> float:
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

        resolved_input = self.input_resolver.resolve_inputs(
            title=title,
            director=director,
            actors=actors or [],
            year=int(year),
        )
        resolved_title = resolved_input["title"]
        resolved_director = resolved_input["director"]
        resolved_actors = resolved_input["actors"]

        # Подготовка признаков. Русские названия/имена при найденном
        # соответствии уже заменены на канонические значения локального IMDb.
        X = self._prepare_features(
            year,
            runtime,
            genres,
            resolved_director,
            resolved_actors,
            num_votes,
            title=resolved_title,
        )

        # Проверка размерности
        expected_features = len(self.metadata['feature_names'])
        if X.shape[1] != expected_features:
            raise ValueError(
                f"Ожидалось {expected_features} признаков, получено {X.shape[1]}. "
                f"Проверьте корректность входных данных."
            )

        # Предсказание
        rating = float(self.model.predict(X)[0])
        rating = max(0, min(10, rating))
        rounded_rating = round(rating, 2)
        
        if not explain:
            return rounded_rating
        
        # --- Объяснение через SHAP ---
        test_pool = Pool(
            data=X,
            cat_features=self.metadata.get('cat_features_idx', []),
            feature_names=self.metadata['feature_names']
        )
        shap_values = self.model.get_feature_importance(data=test_pool, type='ShapValues')[0]
        base_value = shap_values[-1]
        contributions = dict(zip(self.metadata['feature_names'], shap_values[:-1]))
        
        return {
            'rating': rounded_rating,
            'base': base_value,
            'contributions': contributions,
            'explanation': self._format_explanation(contributions),
            'input_resolution': resolved_input["matches"],
            'uncertainty': self.uncertainty_for_rating(rounded_rating),
            'quality': self.quality_summary(),
        }

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
        """Возвращает встроенную CatBoost importance по признакам."""
        values = self.model.get_feature_importance()
        pairs = zip(self.metadata["feature_names"], values)
        return dict(
            sorted(
                pairs,
                key=lambda item: abs(float(item[1])),
                reverse=True,
            )
        )


    def explain_prediction(
        self,
        year,
        runtime,
        genres,
        director=None,
        actors=None,
        num_votes=None,
        title=None,
    ) -> dict:
        X = self._prepare_features(
            year,
            runtime,
            genres,
            director,
            actors,
            num_votes,
            title=title,
        )
        pool = Pool(
            data=X,
            cat_features=self.metadata.get("cat_features_idx", []),
            feature_names=self.metadata["feature_names"],
        )
        shap_values = self.model.get_feature_importance(
            data=pool,
            type="ShapValues",
        )[0]
        base_value = float(shap_values[-1])
        contributions = {
            name: float(value)
            for name, value in zip(
                self.metadata["feature_names"],
                shap_values[:-1],
            )
        }
        pred = float(self.model.predict(X)[0])
        rounded = round(max(0.0, min(10.0, pred)), 2)
        return {
            "rating": rounded,
            "base": base_value,
            "contributions": contributions,
            "explanation": self._format_explanation(contributions),
            "uncertainty": self.uncertainty_for_rating(rounded),
            "quality": self.quality_summary(),
        }


    def _format_explanation(self, contributions):
        parts = []
        # Сортируем по абсолютному вкладу, берём топ-5
        sorted_items = sorted(contributions.items(), key=lambda x: abs(x[1]), reverse=True)
        for name, val in sorted_items[:5]:
            if abs(val) > 0.05:  # порог значимости
                sign = '+' if val > 0 else ''
                parts.append(f"{name}: {sign}{val:.2f}")
        if parts:
            return "Рейтинг сформирован за счёт: " + "; ".join(parts)
        else:
            return "Нет значимых факторов"


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
    if model_path is None:
        from src.train_model import resolve_current_model_path
        model_path = str(resolve_current_model_path())
    kino = KinoVanga(model_path=model_path)
    return kino.predict(
        year=year,
        runtime=runtime,
        genres=genres,
        director=director,
        actors=actors,
        title=title
    )