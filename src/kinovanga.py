"""
КиноВанга — inference-модуль предсказания рейтинга фильма.

Все исторические признаки вычисляются из локальной IMDb DuckDB только по данным,
которые существовали до года прогнозируемого фильма. Новые feature-блоки
активируются по metadata конкретного поколения модели, поэтому старые поколения
не получают лишних запросов и сохраняют прежний контракт.
"""

from pathlib import Path
from typing import List, Optional, Union
import pickle

from catboost import CatBoostRegressor, Pool
import duckdb
import numpy as np

from settings import config
from src.catalog import CatalogSearch
from src.creative_team_features import load_person_context
from src.input_aliases import RussianInputResolver
from src.logger import setup_logger
from src.normalize import extract_title_features, normalize_genre_str


logger = setup_logger(__name__)


class KinoVanga:
    """Предсказание рейтинга и explainability для активного поколения Vanga."""

    def __init__(self, model_path, db_path=None):
        self.model_path = Path(model_path)
        self.model = None
        self.db_path = Path(db_path) if db_path else Path(config.IMDB_DB_PATH)
        self.director_cache = {}
        self.actor_cache = {}
        self._people_cache = {}

        self.conn = duckdb.connect(str(self.db_path), read_only=True)
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
        """Возвращает IMDb ID и role-specific history до года прогноза."""
        clean_names = [name.strip() for name in names if name and name.strip()]
        if not clean_names:
            return {}
        if role not in {"director", "writer", "actor"}:
            raise ValueError("role должен быть director, writer или actor")

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

        if role == "writer":
            query = """
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
                        COUNT(DISTINCT CASE
                            WHEN r.tconst IS NOT NULL THEN tw.tconst
                        END) AS works_count
                    FROM person_ids pi
                    LEFT JOIN title_writers tw
                      ON tw.nconst = pi.nconst
                    LEFT JOIN title_basics b
                      ON b.tconst = tw.tconst
                     AND b.titleType = 'movie'
                     AND TRY_CAST(b.startYear AS INTEGER) < ?
                    LEFT JOIN title_ratings r
                      ON r.tconst = b.tconst
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
                    COALESCE(r.avg_rating, 6.5),
                    COALESCE(r.works_count, 0)
                FROM person_names pn
                LEFT JOIN ranked r
                  ON LOWER(r.requested_name) = LOWER(pn.name)
                 AND r.rn = 1
            """
            rows = self.conn.execute(
                query,
                [missing, int(before_year)],
            ).fetchall()
        else:
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
                        COUNT(DISTINCT CASE
                            WHEN r.tconst IS NOT NULL THEN tp.tconst
                        END) AS works_count
                    FROM person_ids pi
                    LEFT JOIN title_principals tp
                      ON tp.nconst = pi.nconst
                     AND tp.category IN ({placeholders})
                    LEFT JOIN title_basics b
                      ON b.tconst = tp.tconst
                     AND b.titleType = 'movie'
                     AND TRY_CAST(b.startYear AS INTEGER) < ?
                    LEFT JOIN title_ratings r
                      ON r.tconst = b.tconst
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
                    COALESCE(r.avg_rating, 6.5),
                    COALESCE(r.works_count, 0)
                FROM person_names pn
                LEFT JOIN ranked r
                  ON LOWER(r.requested_name) = LOWER(pn.name)
                 AND r.rn = 1
            """
            rows = self.conn.execute(
                query,
                [missing, *categories, int(before_year)],
            ).fetchall()

        for name, nconst, avg_rating, works_count in rows:
            prior_count = int(works_count or 0)
            info = {
                "nconst": nconst,
                "avg_rating": (
                    float(avg_rating)
                    if avg_rating is not None
                    else 6.5
                ),
                "works_count": prior_count,
                "prior_count": prior_count,
                "known": prior_count > 0,
            }
            cache_key = (role, str(name).casefold(), int(before_year))
            self._people_cache[cache_key] = info
            cached[str(name)] = info

        return cached

    def _load_model(self):
        logger.info("Загрузка модели из %s", self.model_path)
        self.model = CatBoostRegressor()
        self.model.load_model(self.model_path)
        logger.info("CatBoost модель загружена")

        metadata_path = self.model_path.parent / "metadata.pkl"
        if not metadata_path.exists():
            raise FileNotFoundError(f"Не найден файл метаданных: {metadata_path}")
        with open(metadata_path, "rb") as handle:
            self.metadata = pickle.load(handle)
        logger.info("Метаданные загружены: %s", metadata_path)

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
        """Строит эмпирический диапазон по ошибкам temporal holdout."""
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

    @staticmethod
    def _empty_creative_context() -> dict:
        return {
            "genre_avg_rating": 6.5,
            "genre_prior_count": 0,
            "genre_known": 0.0,
            "recent_avg_rating": 6.5,
            "recent_count": 0,
            "recent_known": 0.0,
        }

    def _prepare_features(
        self,
        year: int,
        runtime: int,
        genres: Union[str, List[str]],
        director: Optional[str] = None,
        writer: Optional[str] = None,
        actors: Optional[List[str]] = None,
        num_votes: Optional[int] = None,
        title: Optional[str] = None,
    ):
        """Готовит признаки в точности в том же виде, что и training pipeline."""
        import time

        del num_votes
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
        writer_features_enabled = {
            "writer_id",
            "writer_avg_rating",
        }.issubset(feature_names_set)
        writer_people = (
            self._get_people_info(
                [writer],
                before_year=int(year),
                role="writer",
            )
            if writer and writer_features_enabled
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
        director_prior_count = int(director_info.get("prior_count", 0) or 0)
        director_known = 1.0 if director_prior_count > 0 else 0.0

        writer_info = writer_people.get(writer, {}) if writer else {}
        writer_id = writer_info.get("nconst") or "Unknown"
        writer_avg_rating = float(writer_info.get("avg_rating", 6.5))
        writer_prior_count = int(writer_info.get("prior_count", 0) or 0)
        writer_known = 1.0 if writer_prior_count > 0 else 0.0

        actor_infos: list[dict] = []
        for actor in actors[:3]:
            info = actor_people.get(actor, {})
            prior_count = int(info.get("prior_count", 0) or 0)
            actor_infos.append(
                {
                    "nconst": info.get("nconst") or "Unknown",
                    "avg_rating": float(info.get("avg_rating", 6.5)),
                    "prior_count": prior_count,
                    "known": 1.0 if prior_count > 0 else 0.0,
                }
            )
        while len(actor_infos) < 3:
            actor_infos.append(
                {
                    "nconst": "Unknown",
                    "avg_rating": 6.5,
                    "prior_count": 0,
                    "known": 0.0,
                }
            )

        creative_feature_names = {
            "director_genre_avg_rating",
            "director_genre_prior_count",
            "director_genre_known",
            "director_recent_avg_rating",
            "director_recent_count",
            "director_recent_known",
            "writer_genre_avg_rating",
            "writer_genre_prior_count",
            "writer_genre_known",
            "writer_recent_avg_rating",
            "writer_recent_count",
            "writer_recent_known",
            "director_is_writer",
        }
        creative_enabled = bool(feature_names_set & creative_feature_names)
        if creative_enabled:
            director_context = load_person_context(
                self.conn,
                nconst=None if director_id == "Unknown" else director_id,
                role="director",
                before_year=int(year),
                genres=genres_combined,
            )
            writer_context = load_person_context(
                self.conn,
                nconst=None if writer_id == "Unknown" else writer_id,
                role="writer",
                before_year=int(year),
                genres=genres_combined,
            )
        else:
            director_context = self._empty_creative_context()
            writer_context = self._empty_creative_context()

        director_is_writer = (
            1.0
            if creative_enabled
            and director_id != "Unknown"
            and writer_id != "Unknown"
            and director_id == writer_id
            else 0.0
        )

        features = {
            "startYear": (int(year) - 1900) / 100.0,
            "runtimeMinutes": int(runtime) / 100.0,
            "director_avg_rating": director_avg_rating,
            "director_prior_count": float(director_prior_count),
            "director_known": director_known,
            "writer_avg_rating": writer_avg_rating,
            "writer_prior_count": float(writer_prior_count),
            "writer_known": writer_known,
            "actor_1_avg_rating": actor_infos[0]["avg_rating"],
            "actor_1_prior_count": float(actor_infos[0]["prior_count"]),
            "actor_1_known": actor_infos[0]["known"],
            "actor_2_avg_rating": actor_infos[1]["avg_rating"],
            "actor_2_prior_count": float(actor_infos[1]["prior_count"]),
            "actor_2_known": actor_infos[1]["known"],
            "actor_3_avg_rating": actor_infos[2]["avg_rating"],
            "actor_3_prior_count": float(actor_infos[2]["prior_count"]),
            "actor_3_known": actor_infos[2]["known"],
            "director_genre_avg_rating": director_context["genre_avg_rating"],
            "director_genre_prior_count": float(
                director_context["genre_prior_count"]
            ),
            "director_genre_known": director_context["genre_known"],
            "director_recent_avg_rating": director_context["recent_avg_rating"],
            "director_recent_count": float(director_context["recent_count"]),
            "director_recent_known": director_context["recent_known"],
            "writer_genre_avg_rating": writer_context["genre_avg_rating"],
            "writer_genre_prior_count": float(writer_context["genre_prior_count"]),
            "writer_genre_known": writer_context["genre_known"],
            "writer_recent_avg_rating": writer_context["recent_avg_rating"],
            "writer_recent_count": float(writer_context["recent_count"]),
            "writer_recent_known": writer_context["recent_known"],
            "director_is_writer": director_is_writer,
            "genres_combined": genres_combined,
            "director_id": director_id,
            "writer_id": writer_id,
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
            "_prepare_features завершён за %.3f сек",
            time.perf_counter() - t0,
        )
        return X

    def predict(
        self,
        year,
        runtime,
        genres,
        director=None,
        writer=None,
        actors=None,
        num_votes=None,
        title=None,
        explain=False,
    ) -> float:
        """Предсказывает рейтинг фильма."""
        if title:
            logger.info("Предсказание для фильма: %s (%s)", title, year)

        writer_features_enabled = {
            "writer_id",
            "writer_avg_rating",
        }.issubset(set(self.metadata.get("feature_names", [])))
        resolved_input = self.input_resolver.resolve_inputs(
            title=title,
            director=director,
            writer=writer if writer_features_enabled else None,
            actors=actors or [],
            year=int(year),
        )
        resolved_title = resolved_input["title"]
        resolved_director = resolved_input["director"]
        resolved_writer = resolved_input["writer"]
        resolved_actors = resolved_input["actors"]

        X = self._prepare_features(
            year,
            runtime,
            genres,
            resolved_director,
            resolved_writer,
            resolved_actors,
            num_votes,
            title=resolved_title,
        )

        expected_features = len(self.metadata["feature_names"])
        if X.shape[1] != expected_features:
            raise ValueError(
                f"Ожидалось {expected_features} признаков, получено {X.shape[1]}. "
                "Проверьте корректность входных данных."
            )

        rating = float(self.model.predict(X)[0])
        rounded_rating = round(max(0, min(10, rating)), 2)
        if not explain:
            return rounded_rating

        test_pool = Pool(
            data=X,
            cat_features=self.metadata.get("cat_features_idx", []),
            feature_names=self.metadata["feature_names"],
        )
        shap_values = self.model.get_feature_importance(
            data=test_pool,
            type="ShapValues",
        )[0]
        base_value = shap_values[-1]
        contributions = dict(
            zip(self.metadata["feature_names"], shap_values[:-1])
        )
        return {
            "rating": rounded_rating,
            "base": base_value,
            "contributions": contributions,
            "explanation": self._format_explanation(contributions),
            "input_resolution": resolved_input["matches"],
            "uncertainty": self.uncertainty_for_rating(rounded_rating),
            "quality": self.quality_summary(),
        }

    def predict_batch(self, movies: List[dict]) -> List[float]:
        """Предсказывает рейтинги для нескольких фильмов."""
        return [self.predict(**movie) for movie in movies]

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
        writer=None,
        actors=None,
        num_votes=None,
        title=None,
    ) -> dict:
        X = self._prepare_features(
            year,
            runtime,
            genres,
            director,
            writer,
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
        sorted_items = sorted(
            contributions.items(),
            key=lambda item: abs(item[1]),
            reverse=True,
        )
        for name, value in sorted_items[:5]:
            if abs(value) > 0.05:
                sign = "+" if value > 0 else ""
                parts.append(f"{name}: {sign}{value:.2f}")
        if parts:
            return "Рейтинг сформирован за счёт: " + "; ".join(parts)
        return "Нет значимых факторов"


def predict_movie_rating(
    title: str,
    year: int,
    runtime: int,
    genres: Union[str, List[str]],
    director: str,
    writer: Optional[str] = None,
    actors: List[str] = None,
    model_path: Optional[str] = None,
) -> float:
    """Удобная функция для быстрого одиночного предсказания."""
    if model_path is None:
        from src.train_model import resolve_current_model_path

        model_path = str(resolve_current_model_path())
    kino = KinoVanga(model_path=model_path)
    return kino.predict(
        year=year,
        runtime=runtime,
        genres=genres,
        director=director,
        writer=writer,
        actors=actors,
        title=title,
    )
