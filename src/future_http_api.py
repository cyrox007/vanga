from __future__ import annotations

from typing import Any

from flask import Blueprint, jsonify, request

from src.future_prediction_payload import FuturePredictionPayloadError
from src.future_regional_release import (
    FutureRegionalReleaseError,
    FutureRegionalReleaseService,
    RegionalTemporalFuturePredictionPayloadBuilder,
)
from src.future_releases import FutureReleaseError


future_api = Blueprint("future_api", __name__)


def _error(message: str, status: int):
    return jsonify({"ok": False, "error": message}), status


def _required_text(value: Any, *, field_name: str, limit: int = 180) -> str:
    text = str(value or "").strip()
    if not text:
        raise FutureRegionalReleaseError(f"{field_name} обязателен")
    if len(text) > limit:
        raise FutureRegionalReleaseError(f"{field_name} длиннее {limit} символов")
    return text


def _optional_text(value: Any, *, field_name: str, limit: int = 5000) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if len(text) > limit:
        raise FutureRegionalReleaseError(f"{field_name} длиннее {limit} символов")
    return text


def _bool_arg(value: Any, *, field_name: str, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().casefold()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    raise FutureRegionalReleaseError(
        f"{field_name} должен быть boolean (true/false или 1/0)"
    )


def _territory(value: Any) -> str:
    text = _required_text(value, field_name="territory", limit=16)
    # Полную проверку ISO выполняет FutureRegionalReleaseService. Здесь отдельно
    # запрещаем логические псевдо-территории, чтобы HTTP-контракт был явным.
    if text.casefold() in {"worldwide", "unspecified"}:
        raise FutureRegionalReleaseError(
            "territory должен быть ISO 3166-1 alpha-2, worldwide fallback запрещён"
        )
    return text


@future_api.get("/future/catalog")
def future_catalog():
    """Возвращает cache-only каталог будущих релизов для одного рынка."""
    try:
        cutoff = _required_text(request.args.get("cutoff"), field_name="cutoff", limit=64)
        territory = _territory(request.args.get("territory"))
        from_at = _optional_text(request.args.get("from_at"), field_name="from_at", limit=64)
        to_at = _optional_text(request.args.get("to_at"), field_name="to_at", limit=64)
        include_conflicts = _bool_arg(
            request.args.get("include_conflicts"),
            field_name="include_conflicts",
            default=True,
        )
        payload = FutureRegionalReleaseService().catalog_as_of(
            cutoff,
            territory=territory,
            from_at=from_at,
            to_at=to_at,
            include_conflicts=include_conflicts,
        )
    except (FutureRegionalReleaseError, FutureReleaseError, ValueError) as exc:
        return _error(str(exc), 400)
    except Exception:
        # В отличие от /predict этот endpoint не загружает модель и не должен
        # скрывать ошибки входных данных под model-unavailable.
        return _error("Региональный каталог временно недоступен", 503)

    return jsonify({"ok": True, **payload})


@future_api.post("/future/prediction-payload")
def future_prediction_payload():
    """Собирает готовый payload для /predict на заданном региональном cutoff."""
    if request.content_length is not None and request.content_length > 48 * 1024:
        return _error("Слишком большой запрос", 413)
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return _error("Ожидается JSON-объект", 400)

    try:
        project_id = _required_text(body.get("project_id"), field_name="project_id")
        cutoff = _required_text(body.get("cutoff"), field_name="cutoff", limit=64)
        territory = _territory(body.get("territory"))
        synopsis = _optional_text(body.get("synopsis"), field_name="synopsis")
        allow_current_imdb_snapshot = _bool_arg(
            body.get("allow_current_imdb_snapshot"),
            field_name="allow_current_imdb_snapshot",
            default=False,
        )

        result = RegionalTemporalFuturePredictionPayloadBuilder().build(
            project_id,
            cutoff,
            territory=territory,
            runtime_override=body.get("runtime_override"),
            genres_override=body.get("genres_override"),
            synopsis=synopsis,
            allow_current_imdb_snapshot=allow_current_imdb_snapshot,
        )
    except FutureReleaseError as exc:
        message = str(exc)
        status = 404 if "Неизвестный project_id" in message else 400
        return _error(message, status)
    except (FutureRegionalReleaseError, FuturePredictionPayloadError, ValueError) as exc:
        return _error(str(exc), 400)
    except Exception:
        return _error("Не удалось собрать региональный prediction payload", 503)

    # request может быть None: это штатное состояние, когда blockers не позволяют
    # выполнять прогноз. Клиент должен показать blockers, а не вызывать /predict.
    return jsonify({"ok": True, **result})
