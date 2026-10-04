from __future__ import annotations

import logging
import threading
from contextlib import contextmanager
from typing import Any, Iterator

from flask import Flask, jsonify, request

from src.creative_kinovanga import KinoVanga
from src.pre_release_analysis import build_pre_release_profile
from src.runtime_descriptor import resolve_model_runtime_descriptor


logger = logging.getLogger(__name__)

app = Flask(__name__)
_lock = threading.RLock()
_catalog_lock = threading.RLock()
_engine: KinoVanga | None = None
_generation: str | None = None
_last_reload_error: str | None = None


def _ensure_engine() -> KinoVanga:
    """Возвращает активный engine, сохраняя предыдущий при ошибке reload.

    Descriptor поколения читается ровно один раз внутри lifecycle lock. Поэтому
    путь модели и generation не могут относиться к разным состояниям pointer.
    Ошибка чтения/разрешения pointer также входит в fallback-контур: если рабочий
    engine уже загружен, сервис продолжает обслуживать им запросы.
    """
    global _engine, _generation, _last_reload_error

    with _lock:
        try:
            descriptor = resolve_model_runtime_descriptor()
        except Exception as exc:
            _last_reload_error = str(exc)
            logger.exception("Не удалось разрешить активное поколение модели Vanga")
            if _engine is not None:
                logger.warning(
                    "Pointer недоступен; продолжаем обслуживать запросы предыдущей моделью"
                )
                return _engine
            raise

        if _engine is not None and _generation == descriptor.generation:
            return _engine

        try:
            candidate = KinoVanga(descriptor.model_path)
        except Exception as exc:
            _last_reload_error = str(exc)
            logger.exception("Не удалось загрузить новое поколение модели Vanga")
            if _engine is not None:
                logger.warning("Продолжаем обслуживать запросы предыдущей моделью")
                return _engine
            raise

        previous = _engine
        _engine = candidate
        _generation = descriptor.generation
        _last_reload_error = None

        # Все API call-sites используют _engine_session(), поэтому пока мы держим
        # lifecycle lock ни один запрос не может продолжать работу с previous.
        if previous is not None:
            try:
                with _catalog_lock:
                    previous.close()
            except Exception:
                logger.exception("Не удалось закрыть старые соединения DuckDB")

        logger.info(
            "Активировано новое поколение модели Vanga: generation=%s source=%s",
            descriptor.generation,
            descriptor.source,
        )
        return candidate


@contextmanager
def _engine_session() -> Iterator[tuple[KinoVanga, str]]:
    """Закрепляет согласованные engine+generation на всё время API-операции."""
    with _lock:
        engine = _ensure_engine()
        generation = _generation
        if generation is None:
            raise RuntimeError("Engine загружен без generation")
        yield engine, generation


def _json_error(message: str, status: int):
    return jsonify({"ok": False, "error": message}), status


def _search_limit() -> int:
    try:
        return max(1, min(int(request.args.get("limit", "8")), 12))
    except ValueError:
        return 8


@app.get("/search/movies")
def search_movies():
    query = str(request.args.get("q") or "").strip()
    if len(query) < 2:
        return jsonify({"ok": True, "items": []})
    if len(query) > 120:
        return _json_error("Слишком длинный поисковый запрос", 400)
    raw_year = str(request.args.get("year") or "").strip()
    year = None
    if raw_year:
        try:
            year = int(raw_year)
        except ValueError:
            return _json_error("year должен быть целым числом", 400)
        if not (1888 <= year <= 2100):
            return _json_error("year вне допустимого диапазона", 400)
    try:
        with _engine_session() as (engine, generation):
            with _catalog_lock:
                items = engine.catalog.search_movies(
                    query,
                    limit=_search_limit(),
                    year=year,
                )
    except Exception:
        logger.exception("Ошибка поиска фильмов Vanga")
        return _json_error("Поиск фильмов временно недоступен", 503)
    return jsonify({"ok": True, "items": items, "generation": generation})


@app.get("/search/people")
def search_people():
    query = str(request.args.get("q") or "").strip()
    if len(query) < 2:
        return jsonify({"ok": True, "items": []})
    if len(query) > 120:
        return _json_error("Слишком длинный поисковый запрос", 400)
    role = str(request.args.get("role") or "actor").strip().lower()
    if role not in {"director", "writer", "actor"}:
        return _json_error("role должен быть director, writer или actor", 400)
    try:
        with _engine_session() as (engine, generation):
            with _catalog_lock:
                items = engine.catalog.search_people(
                    query,
                    role=role,
                    limit=_search_limit(),
                )
    except Exception:
        logger.exception("Ошибка поиска персон Vanga")
        return _json_error("Поиск персон временно недоступен", 503)
    return jsonify({"ok": True, "items": items, "generation": generation})


@app.post("/catalog/ratings")
def catalog_ratings():
    if request.content_length is not None and request.content_length > 32 * 1024:
        return _json_error("Слишком большой запрос", 413)
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return _json_error("Ожидается JSON-объект", 400)
    imdb_ids = payload.get("imdb_ids")
    if not isinstance(imdb_ids, list) or any(
        not isinstance(item, str) for item in imdb_ids
    ):
        return _json_error("imdb_ids должен быть массивом строк", 400)
    if len(imdb_ids) > 100:
        return _json_error("За один запрос можно проверить не больше 100 фильмов", 400)
    try:
        with _engine_session() as (engine, generation):
            with _catalog_lock:
                items = engine.catalog.current_ratings(imdb_ids)
    except Exception:
        logger.exception("Ошибка чтения текущих IMDb ratings")
        return _json_error("Рейтинги временно недоступны", 503)
    return jsonify({"ok": True, "items": items, "generation": generation})


@app.get("/health")
def health():
    try:
        with _engine_session() as (engine, generation):
            reload_error = _last_reload_error
            model_name = str(engine.model_path.name)
            database_name = str(engine.db_path.name)
        return jsonify(
            {
                "ok": True,
                "status": "ready",
                "model": model_name,
                "generation": generation,
                "reload_error": reload_error,
                "database": database_name,
            }
        )
    except Exception as exc:
        logger.exception("Vanga healthcheck: модель недоступна")
        return (
            jsonify(
                {
                    "ok": False,
                    "status": "degraded",
                    "error": str(exc),
                    "reload_error": _last_reload_error,
                }
            ),
            503,
        )


@app.get("/model-info")
def model_info():
    try:
        with _engine_session() as (engine, generation):
            metadata = engine.metadata if isinstance(engine.metadata, dict) else {}
            response = {
                "ok": True,
                "generation": generation,
                "schema_version": metadata.get("schema_version"),
                "feature_names": metadata.get("feature_names") or [],
                "categorical_features": metadata.get("categorical_features") or [],
                "quality": engine.quality_summary(),
                "uncertainty_available": bool(
                    metadata.get("test_abs_error_quantiles")
                ),
                "quality_gate": metadata.get("quality_gate"),
                "model_size_bytes": metadata.get("model_size_bytes"),
                "reload_error": _last_reload_error,
            }
        return jsonify(response)
    except Exception:
        logger.exception("Не удалось получить сведения о модели Vanga")
        return _json_error("Сведения о модели временно недоступны", 503)


@app.post("/predict")
def predict():
    if request.content_length is not None and request.content_length > 48 * 1024:
        return _json_error("Слишком большой запрос", 413)
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return _json_error("Ожидается JSON-объект", 400)

    title = str(payload.get("title") or "").strip()
    legacy_director = str(payload.get("director") or "").strip()
    raw_directors = payload.get("directors")
    if raw_directors is None:
        directors = [legacy_director] if legacy_director else []
    elif isinstance(raw_directors, list) and all(
        isinstance(item, str) for item in raw_directors
    ):
        directors = []
        for item in raw_directors:
            clean = item.strip()
            if clean and clean not in directors:
                directors.append(clean)
        if legacy_director and legacy_director not in directors:
            directors.insert(0, legacy_director)
    else:
        return _json_error("directors должен быть массивом строк", 400)
    directors = directors[:8]
    director = directors[0] if directors else ""

    writer = str(payload.get("writer") or "").strip()
    synopsis = str(payload.get("synopsis") or "").strip()
    genres = payload.get("genres")
    actors = payload.get("actors") or []
    imdb_id = str(payload.get("imdb_id") or "").strip()
    source = payload.get("source") or {}

    try:
        year = int(payload.get("year"))
        runtime = int(payload.get("runtime"))
    except (TypeError, ValueError):
        return _json_error("year и runtime должны быть целыми числами", 400)

    if not (1888 <= year <= 2100):
        return _json_error("year вне допустимого диапазона", 400)
    if not (1 <= runtime <= 1000):
        return _json_error("runtime вне допустимого диапазона", 400)
    if not title or len(title) > 240:
        return _json_error("Укажите название фильма", 400)
    if imdb_id and (len(imdb_id) > 16 or not imdb_id.startswith("tt")):
        return _json_error("Некорректный imdb_id", 400)
    if not directors:
        return _json_error("Укажите хотя бы одного режиссёра", 400)
    if any(len(item) > 240 for item in directors):
        return _json_error("Слишком длинное имя режиссёра", 400)
    if len(writer) > 240:
        return _json_error("Слишком длинное имя сценариста", 400)
    if len(synopsis) > 5000:
        return _json_error("Синопсис не должен превышать 5000 символов", 400)
    if not isinstance(source, dict):
        return _json_error("source должен быть JSON-объектом", 400)

    clean_source: dict[str, Any] = {}
    for key in ("type", "title", "author", "format"):
        value = str(source.get(key) or "").strip()
        if len(value) > 240:
            return _json_error(f"source.{key} слишком длинное", 400)
        if value:
            clean_source[key] = value
    if source.get("series_size") is not None:
        try:
            series_size = int(source.get("series_size"))
        except (TypeError, ValueError):
            return _json_error("source.series_size должен быть целым числом", 400)
        if not (1 <= series_size <= 10000):
            return _json_error("source.series_size вне допустимого диапазона", 400)
        clean_source["series_size"] = series_size

    if isinstance(genres, str):
        genres_value: str | list[str] = genres.strip()
    elif isinstance(genres, list) and all(isinstance(item, str) for item in genres):
        genres_value = [item.strip() for item in genres if item.strip()]
    else:
        return _json_error("genres должен быть строкой или массивом строк", 400)
    if not genres_value:
        return _json_error("Укажите жанр", 400)
    if not isinstance(actors, list) or any(not isinstance(item, str) for item in actors):
        return _json_error("actors должен быть массивом строк", 400)
    actors = [item.strip() for item in actors if item.strip()][:32]
    if any(len(item) > 240 for item in actors):
        return _json_error("Слишком длинное имя актёра", 400)

    try:
        with _engine_session() as (engine, generation):
            result: Any = engine.predict(
                title=title,
                director=director,
                directors=directors,
                writer=writer or None,
                year=year,
                runtime=runtime,
                genres=genres_value,
                actors=actors,
                explain=True,
            )
            profile = build_pre_release_profile(
                conn=engine.conn,
                year=year,
                runtime=runtime,
                director=director,
                writer=writer or None,
                actors=actors,
                synopsis=synopsis or None,
                rating=float(result["rating"]),
                uncertainty=result.get("uncertainty"),
                contributions=result.get("contributions") or {},
            )
            profile["source"] = clean_source
            profile["director_team"] = {
                "requested": directors,
                "count": len(directors),
            }
            profile["cast"] = {
                "requested": actors,
                "count": len(actors),
                "legacy_personal_slots": min(3, len(actors)),
            }
    except Exception:
        logger.exception("Ошибка предсказания Vanga")
        return _json_error("Модель временно не смогла выполнить предсказание", 503)

    return jsonify(
        {
            "ok": True,
            "title": title,
            "imdb_id": (
                imdb_id
                or (
                    ((result.get("input_resolution") or {}).get("title") or {}).get(
                        "imdb_id"
                    )
                )
                or None
            ),
            "generation": generation,
            "rating": result["rating"],
            "base": result.get("base"),
            "uncertainty": result.get("uncertainty"),
            "quality": result.get("quality") or {},
            "explanation": result["explanation"],
            "contributions": result["contributions"],
            "input_resolution": result.get("input_resolution", {}),
            "pre_release_profile": profile,
        }
    )


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=9100, debug=False)
