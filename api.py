from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Any

from flask import Flask, jsonify, request

from settings import config
from src.kinovanga import KinoVanga
from src.train_model import resolve_current_model_path


logger = logging.getLogger(__name__)

app = Flask(__name__)
_lock = threading.RLock()
_catalog_lock = threading.RLock()
_engine: KinoVanga | None = None
_generation: str | None = None
_last_reload_error: str | None = None


def _generation_key(model_path: Path) -> str:
    """Возвращает компактный идентификатор активного поколения модели."""
    pointer = Path(config.ABSPATH) / "models" / "current.json"
    if pointer.exists():
        payload = json.loads(pointer.read_text(encoding="utf-8"))
        generation = str(payload.get("generation") or "").strip()
        if generation:
            return generation
    return f"legacy:{model_path.stat().st_mtime_ns}"


def _ensure_engine() -> KinoVanga:
    global _engine, _generation, _last_reload_error

    model_path = resolve_current_model_path()
    generation = _generation_key(model_path)
    if _engine is not None and _generation == generation:
        return _engine

    with _lock:
        if _engine is not None and _generation == generation:
            return _engine

        try:
            candidate = KinoVanga(model_path)
        except Exception as exc:
            _last_reload_error = str(exc)
            logger.exception("Не удалось загрузить новое поколение модели Vanga")
            if _engine is not None:
                logger.warning("Продолжаем обслуживать запросы предыдущей моделью")
                return _engine
            raise

        previous = _engine
        _engine = candidate
        _generation = generation
        _last_reload_error = None

        if previous is not None:
            try:
                previous.close()
            except Exception:
                logger.exception("Не удалось закрыть старые соединения DuckDB")

        logger.info("Активировано новое поколение модели Vanga")
        return candidate


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
        engine = _ensure_engine()
        with _catalog_lock:
            items = engine.catalog.search_movies(
                query,
                limit=_search_limit(),
                year=year,
            )
    except Exception:
        logger.exception("Ошибка поиска фильмов Vanga")
        return _json_error("Поиск фильмов временно недоступен", 503)

    return jsonify({"ok": True, "items": items, "generation": _generation})


@app.get("/search/people")
def search_people():
    query = str(request.args.get("q") or "").strip()
    if len(query) < 2:
        return jsonify({"ok": True, "items": []})
    if len(query) > 120:
        return _json_error("Слишком длинный поисковый запрос", 400)

    role = str(request.args.get("role") or "actor").strip().lower()
    if role not in {"director", "actor"}:
        return _json_error("role должен быть director или actor", 400)

    try:
        engine = _ensure_engine()
        with _catalog_lock:
            items = engine.catalog.search_people(
                query,
                role=role,
                limit=_search_limit(),
            )
    except Exception:
        logger.exception("Ошибка поиска персон Vanga")
        return _json_error("Поиск персон временно недоступен", 503)

    return jsonify({"ok": True, "items": items, "generation": _generation})


@app.get("/health")
def health():
    try:
        model_path = resolve_current_model_path()
        engine = _ensure_engine()
        return jsonify(
            {
                "ok": True,
                "status": "ready",
                "model": str(model_path.name),
                "generation": _generation,
                "reload_error": _last_reload_error,
                "database": str(engine.db_path.name),
            }
        )
    except Exception as exc:
        logger.exception("Vanga healthcheck: модель недоступна")
        return jsonify(
            {
                "ok": False,
                "status": "degraded",
                "error": str(exc),
                "reload_error": _last_reload_error,
            }
        ), 503


@app.post("/predict")
def predict():
    if request.content_length is not None and request.content_length > 32 * 1024:
        return _json_error("Слишком большой запрос", 413)

    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return _json_error("Ожидается JSON-объект", 400)

    title = str(payload.get("title") or "").strip()
    director = str(payload.get("director") or "").strip()
    genres = payload.get("genres")
    actors = payload.get("actors") or []
    imdb_id = str(payload.get("imdb_id") or "").strip()

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
    if not director or len(director) > 240:
        return _json_error("Укажите режиссёра", 400)
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
    actors = [item.strip() for item in actors if item.strip()][:5]

    try:
        with _lock:
            engine = _ensure_engine()
            result: Any = engine.predict(
                title=title,
                director=director,
                year=year,
                runtime=runtime,
                genres=genres_value,
                actors=actors,
                explain=True,
            )
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
                    (result.get("input_resolution") or {}).get("title") or {}
                ).get("imdb_id")
                or None
            ),
            "generation": _generation,
            "rating": result["rating"],
            "base": result.get("base"),
            "explanation": result["explanation"],
            "contributions": result["contributions"],
            "input_resolution": result.get("input_resolution", {}),
        }
    )


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=9100, debug=False)
