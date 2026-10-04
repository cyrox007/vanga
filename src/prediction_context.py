from __future__ import annotations

from typing import Any


def _clean_unique(values: list[str] | None) -> list[str]:
    result: list[str] = []
    for value in values or []:
        clean = " ".join(str(value or "").strip().split())
        if clean and clean not in result:
            result.append(clean)
    return result


def _match_canonical(match: Any, fallback: str | None) -> str | None:
    if isinstance(match, dict):
        value = " ".join(str(match.get("canonical") or "").strip().split())
        if value:
            return value
    clean = " ".join(str(fallback or "").strip().split())
    return clean or None


def _record(
    *,
    requested: str | None,
    canonical: str,
    info: dict[str, Any] | None,
) -> dict[str, Any]:
    info = info or {}
    nconst = str(info.get("nconst") or "").strip() or None
    prior_count = int(info.get("prior_count", info.get("works_count", 0)) or 0)
    avg_raw = info.get("avg_rating")
    try:
        avg_rating = float(avg_raw) if avg_raw is not None else None
    except (TypeError, ValueError):
        avg_rating = None
    return {
        "requested_input": str(requested or "") or None,
        "canonical_name": canonical,
        "imdb_id": nconst,
        "resolved": bool(nconst),
        "known": bool(nconst and prior_count > 0),
        "works_count": prior_count,
        "prior_count": prior_count,
        "avg_rating": avg_rating,
    }


def build_prediction_people_context(
    engine,
    *,
    year: int,
    requested_directors: list[str],
    requested_writer: str | None,
    requested_actors: list[str],
    input_resolution: dict[str, Any] | None,
) -> dict[str, Any]:
    """Фиксирует людей ровно после resolution, использованного rating model.

    `_get_people_info` — тот же локальный lookup/cache, которым feature builder
    получает nconst/prior_count. Поэтому API/profile больше не выполняют второй
    независимый выбор кандидата по исходной строке.
    """

    resolution = input_resolution if isinstance(input_resolution, dict) else {}
    resolved_directors = _clean_unique(resolution.get("resolved_directors"))
    if not resolved_directors:
        resolved_directors = _clean_unique(requested_directors)

    resolved_actors = _clean_unique(resolution.get("resolved_actors"))
    if not resolved_actors:
        resolved_actors = _clean_unique(requested_actors)

    writer_match = resolution.get("writer")
    resolved_writer = _match_canonical(writer_match, requested_writer)

    director_info = (
        engine._get_people_info(
            resolved_directors,
            before_year=int(year),
            role="director",
        )
        if resolved_directors
        else {}
    )
    actor_info = (
        engine._get_people_info(
            resolved_actors,
            before_year=int(year),
            role="actor",
        )
        if resolved_actors
        else {}
    )
    writer_info = (
        engine._get_people_info(
            [resolved_writer],
            before_year=int(year),
            role="writer",
        )
        if resolved_writer
        else {}
    )

    directors = [
        _record(
            requested=(requested_directors[index] if index < len(requested_directors) else None),
            canonical=name,
            info=director_info.get(name),
        )
        for index, name in enumerate(resolved_directors)
    ]
    cast = [
        _record(
            requested=(requested_actors[index] if index < len(requested_actors) else None),
            canonical=name,
            info=actor_info.get(name),
        )
        for index, name in enumerate(resolved_actors)
    ]
    writer = (
        _record(
            requested=requested_writer,
            canonical=resolved_writer,
            info=writer_info.get(resolved_writer),
        )
        if resolved_writer
        else {
            "requested_input": str(requested_writer or "") or None,
            "canonical_name": None,
            "imdb_id": None,
            "resolved": False,
            "known": False,
            "works_count": 0,
            "prior_count": 0,
            "avg_rating": None,
        }
    )

    return {
        "version": 1,
        "source": "rating_feature_resolution",
        "directors": directors,
        "writer": writer,
        "cast": cast,
        "resolved_directors": [item["canonical_name"] for item in directors],
        "resolved_writer": writer.get("canonical_name"),
        "resolved_cast": [item["canonical_name"] for item in cast],
    }
