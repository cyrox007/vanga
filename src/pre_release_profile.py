from __future__ import annotations

from typing import Any

from src.pre_release_analysis import (
    _coverage_level,
    _person_history,
    build_pre_release_profile,
)


def _clean_unique(values: list[str] | None) -> list[str]:
    result: list[str] = []
    for value in values or []:
        clean = " ".join(str(value or "").strip().split())
        if clean and clean not in result:
            result.append(clean)
    return result


def _ratio_known(items: list[Any]) -> float:
    if not items:
        return 0.0
    return sum(1 for item in items if item.known) / len(items)


def build_resolved_pre_release_profile(
    *,
    conn,
    year: int,
    runtime: int,
    requested_directors: list[str],
    resolved_directors: list[str],
    requested_writer: str | None,
    resolved_writer: str | None,
    requested_actors: list[str],
    resolved_actors: list[str],
    synopsis: str | None,
    rating: float,
    uncertainty: dict | None,
    contributions: dict,
) -> dict:
    """Строит diagnostic profile из тех же canonical inputs, что и rating model.

    Legacy profile исторически повторно искал исходные строки и учитывал только
    primary director + top-3 cast. Из-за этого русское имя могло быть успешно
    разрешено основной моделью, но отображаться неизвестным в profile. Здесь
    сначала используем уже resolved canonical inputs, а coverage считаем по всей
    режиссёрской команде и всему переданному cast.
    """

    requested_directors = _clean_unique(requested_directors)
    resolved_directors = _clean_unique(resolved_directors) or requested_directors
    requested_actors = _clean_unique(requested_actors)
    resolved_actors = _clean_unique(resolved_actors) or requested_actors
    clean_writer = " ".join(str(resolved_writer or requested_writer or "").strip().split())

    primary_director = resolved_directors[0] if resolved_directors else ""
    profile = build_pre_release_profile(
        conn=conn,
        year=year,
        runtime=runtime,
        director=primary_director,
        writer=clean_writer or None,
        actors=resolved_actors,
        synopsis=synopsis,
        rating=rating,
        uncertainty=uncertainty,
        contributions=contributions,
    )

    director_histories = [
        _person_history(conn, name, "director", year)
        for name in resolved_directors
    ]
    writer_history = _person_history(conn, clean_writer or None, "writer", year)
    cast_histories = [
        _person_history(conn, name, "actor", year)
        for name in resolved_actors
    ]

    director_known_ratio = _ratio_known(director_histories)
    cast_known_ratio = _ratio_known(cast_histories)
    writer_known = 1.0 if writer_history.known else 0.0

    # Этот score отражает полный person context schema v15, а не legacy top-3.
    familiarity = round(
        max(
            0.0,
            min(
                1.0,
                0.35 * director_known_ratio
                + 0.25 * writer_known
                + 0.40 * cast_known_ratio,
            ),
        ),
        3,
    )
    synopsis_present = bool(str(synopsis or "").strip())
    coverage = round(
        max(0.0, min(1.0, 0.85 * familiarity + (0.15 if synopsis_present else 0.0))),
        3,
    )

    provided_people = list(director_histories)
    if str(requested_writer or resolved_writer or "").strip():
        provided_people.append(writer_history)
    provided_people.extend(cast_histories)
    known_people_count = sum(1 for item in provided_people if item.known)
    resolved_people_count = sum(1 for item in provided_people if item.resolved)
    provided_people_count = len(provided_people)

    unknown_directors = sum(1 for item in director_histories if not item.known)
    unknown_cast = sum(1 for item in cast_histories if not item.known)
    missing_feature_count = unknown_directors + (0 if writer_history.known else 1) + unknown_cast
    abstention_recommended = familiarity < 0.20
    abstention_reason = "insufficient_person_history" if abstention_recommended else None
    abstention_message = (
        "Исторических данных о полной творческой команде слишком мало: точный рейтинг лучше не интерпретировать как надёжный прогноз."
        if abstention_recommended
        else None
    )

    warnings: list[str] = []
    if unknown_directors:
        warnings.append(
            f"Недостаточно истории для {unknown_directors} из {len(director_histories)} режиссёров"
        )
    if not writer_history.known:
        warnings.append("Сценарист не указан либо его историческая выборка недостаточна")
    if unknown_cast:
        warnings.append(
            f"Недостаточно истории для {unknown_cast} из {len(cast_histories)} актёров полного cast"
        )
    if not synopsis_present:
        warnings.append("Синопсис не передан: сценарно-структурный слой ограничен")
    if abstention_message:
        warnings.append(abstention_message)

    data_coverage = profile["data_coverage"]
    data_coverage.update(
        {
            "contract": "resolved_full_team_v3",
            "score": coverage,
            "percent": int(round(coverage * 100)),
            "level": _coverage_level(coverage),
            "model_familiarity": {
                "score": familiarity,
                "percent": int(round(familiarity * 100)),
                "level": _coverage_level(familiarity),
            },
            "known_people_count": known_people_count,
            "resolved_people_count": resolved_people_count,
            "provided_people_count": provided_people_count,
            "known_people_ratio": (
                round(known_people_count / provided_people_count, 3)
                if provided_people_count
                else 0.0
            ),
            "resolved_people_ratio": (
                round(resolved_people_count / provided_people_count, 3)
                if provided_people_count
                else 0.0
            ),
            "missing_feature_count": missing_feature_count,
            "abstention": {
                "recommended": abstention_recommended,
                "reason": abstention_reason,
                "message": abstention_message,
            },
            "warnings": warnings,
            "director": (
                director_histories[0].to_dict()
                if director_histories
                else _person_history(conn, None, "director", year).to_dict()
            ),
            "writer": writer_history.to_dict(),
            # Сохраняем старое поле для UI, но явно это только legacy top-3.
            "actors": [item.to_dict() for item in cast_histories[:3]],
            "director_team": [item.to_dict() for item in director_histories],
            "full_cast": [item.to_dict() for item in cast_histories],
            "director_team_known_ratio": round(director_known_ratio, 3),
            "full_cast_known_ratio": round(cast_known_ratio, 3),
        }
    )

    profile["method"] = "pre_release_profile_v3"
    profile["input_parity"] = {
        "uses_prediction_resolution": True,
        "requested_directors": requested_directors,
        "resolved_directors": resolved_directors,
        "requested_writer": str(requested_writer or "") or None,
        "resolved_writer": clean_writer or None,
        "requested_cast": requested_actors,
        "resolved_cast": resolved_actors,
    }
    profile["director_team"] = {
        "requested": requested_directors,
        "resolved": resolved_directors,
        "count": len(resolved_directors),
    }
    profile["cast"] = {
        "requested": requested_actors,
        "resolved": resolved_actors,
        "count": len(resolved_actors),
        "legacy_personal_slots": min(3, len(resolved_actors)),
    }

    if _coverage_level(coverage) == "low" or abstention_recommended:
        profile["potential"]["level"] = "uncertain"

    return profile
