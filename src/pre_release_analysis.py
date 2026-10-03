from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable


_GROUPS = {
    "screenplay": "Сценарий и сценарная команда",
    "direction": "Режиссура",
    "cast": "Актёрский состав",
    "genre": "Жанровое соответствие",
    "format": "Формат и концепция",
    "other": "Прочие сигналы модели",
}

_WORLD_KEYWORDS = {
    "ru": ("мир", "королев", "импер", "маг", "планет", "галак", "клан", "фракц", "пророч", "цивилизац", "вселен"),
    "en": ("world", "kingdom", "empire", "magic", "planet", "galaxy", "clan", "faction", "prophecy", "civilization", "universe"),
}
_CONFLICT_KEYWORDS = {
    "ru": ("борьб", "войн", "против", "спасти", "выжить", "месть", "конфликт", "угроз", "враг", "тайн"),
    "en": ("fight", "war", "against", "save", "survive", "revenge", "conflict", "threat", "enemy", "mystery"),
}
_RELATIONSHIP_KEYWORDS = {
    "ru": ("семь", "любов", "друж", "отношен", "отец", "мать", "брат", "сестр", "предател"),
    "en": ("family", "love", "friend", "relationship", "father", "mother", "brother", "sister", "betray"),
}


@dataclass(frozen=True)
class PersonHistory:
    input: str
    role: str
    imdb_id: str | None
    works_count: int
    avg_rating: float | None

    @property
    def known(self) -> bool:
        return bool(self.imdb_id and self.works_count > 0)

    def to_dict(self) -> dict:
        return {
            "input": self.input,
            "role": self.role,
            "imdb_id": self.imdb_id,
            "known": self.known,
            "works_count": self.works_count,
            "avg_rating": round(self.avg_rating, 2) if self.avg_rating is not None else None,
        }


def _table_exists(conn, table_name: str) -> bool:
    row = conn.execute(
        """
        SELECT 1
        FROM information_schema.tables
        WHERE table_schema = 'main'
          AND table_name = ?
        LIMIT 1
        """,
        [table_name],
    ).fetchone()
    return row is not None


def _person_history(conn, name: str | None, role: str, before_year: int) -> PersonHistory:
    clean = " ".join(str(name or "").strip().split())
    if not clean:
        return PersonHistory("", role, None, 0, None)

    if role == "writer":
        # Профиль должен пережить rolling deployment: старое imdb.duckdb может
        # ещё не содержать title_writers до первого ds_update новой версии.
        if not _table_exists(conn, "title_writers"):
            return PersonHistory(clean, role, None, 0, None)
        row = conn.execute(
            """
            WITH candidates AS (
                SELECT n.nconst, n.primaryName
                FROM name_basics n
                WHERE LOWER(n.primaryName) = LOWER(?)
            ), stats AS (
                SELECT
                    c.nconst,
                    COUNT(DISTINCT tw.tconst) AS works_count,
                    AVG(TRY_CAST(r.averageRating AS DOUBLE)) AS avg_rating
                FROM candidates c
                LEFT JOIN title_writers tw ON tw.nconst = c.nconst
                LEFT JOIN title_basics b ON b.tconst = tw.tconst
                LEFT JOIN title_ratings r ON r.tconst = b.tconst
                WHERE b.titleType = 'movie'
                  AND TRY_CAST(b.startYear AS INTEGER) < ?
                  AND TRY_CAST(r.averageRating AS DOUBLE) IS NOT NULL
                GROUP BY c.nconst
            )
            SELECT nconst, works_count, avg_rating
            FROM stats
            ORDER BY works_count DESC, avg_rating DESC NULLS LAST, nconst
            LIMIT 1
            """,
            [clean, int(before_year)],
        ).fetchone()
    else:
        categories = ("director",) if role == "director" else ("actor", "actress")
        placeholders = ",".join("?" for _ in categories)
        row = conn.execute(
            f"""
            WITH candidates AS (
                SELECT n.nconst, n.primaryName
                FROM name_basics n
                WHERE LOWER(n.primaryName) = LOWER(?)
            ), stats AS (
                SELECT
                    c.nconst,
                    COUNT(DISTINCT p.tconst) AS works_count,
                    AVG(TRY_CAST(r.averageRating AS DOUBLE)) AS avg_rating
                FROM candidates c
                LEFT JOIN title_principals p
                  ON p.nconst = c.nconst
                 AND p.category IN ({placeholders})
                LEFT JOIN title_basics b ON b.tconst = p.tconst
                LEFT JOIN title_ratings r ON r.tconst = b.tconst
                WHERE b.titleType = 'movie'
                  AND TRY_CAST(b.startYear AS INTEGER) < ?
                  AND TRY_CAST(r.averageRating AS DOUBLE) IS NOT NULL
                GROUP BY c.nconst
            )
            SELECT nconst, works_count, avg_rating
            FROM stats
            ORDER BY works_count DESC, avg_rating DESC NULLS LAST, nconst
            LIMIT 1
            """,
            [clean, *categories, int(before_year)],
        ).fetchone()

    if not row:
        return PersonHistory(clean, role, None, 0, None)
    return PersonHistory(
        clean,
        role,
        str(row[0]) if row[0] else None,
        int(row[1] or 0),
        float(row[2]) if row[2] is not None else None,
    )


def _group_for_feature(name: str) -> str:
    if name.startswith("writer_"):
        return "screenplay"
    if name.startswith("director_"):
        return "direction"
    if name.startswith("actor_"):
        return "cast"
    if name == "genres_combined":
        return "genre"
    if name in {"runtimeMinutes", "startYear"} or name.startswith("title_"):
        return "format"
    return "other"


def _group_contributions(contributions: dict) -> list[dict]:
    grouped: dict[str, dict] = {}
    for key, raw in (contributions or {}).items():
        try:
            value = float(raw)
        except (TypeError, ValueError):
            continue
        group = _group_for_feature(str(key))
        bucket = grouped.setdefault(group, {"value": 0.0, "factors": []})
        bucket["value"] += value
        bucket["factors"].append({"key": str(key), "value": round(value, 4)})

    result = []
    for group, data in grouped.items():
        factors = sorted(data["factors"], key=lambda item: abs(item["value"]), reverse=True)
        result.append(
            {
                "key": group,
                "label": _GROUPS[group],
                "value": round(float(data["value"]), 4),
                "factors": factors,
            }
        )
    return sorted(result, key=lambda item: abs(item["value"]), reverse=True)


def analyze_synopsis(synopsis: str | None, runtime: int) -> dict:
    text = " ".join(str(synopsis or "").strip().split())
    words = re.findall(r"[\wÀ-ÿА-Яа-яЁё'-]+", text, flags=re.UNICODE)
    sentences = [part.strip() for part in re.split(r"[.!?]+", text) if part.strip()]
    lower = text.casefold()

    def count_hits(groups: dict[str, Iterable[str]]) -> int:
        return sum(lower.count(keyword) for values in groups.values() for keyword in values)

    world = count_hits(_WORLD_KEYWORDS)
    conflict = count_hits(_CONFLICT_KEYWORDS)
    relationships = count_hits(_RELATIONSHIP_KEYWORDS)
    word_count = len(words)
    sentence_count = len(sentences)
    avg_sentence_words = round(word_count / sentence_count, 1) if sentence_count else 0.0

    if word_count == 0:
        density = "none"
    elif word_count < 45:
        density = "short"
    elif word_count > 180 or avg_sentence_words > 27:
        density = "dense"
    else:
        density = "moderate"

    signals: list[dict] = []
    if word_count and word_count < 45:
        signals.append(
            {
                "kind": "insufficient_synopsis",
                "severity": "medium",
                "label": "Синопсис слишком короткий для глубокого структурного вывода",
            }
        )
    if world >= 3 and runtime < 110:
        signals.append(
            {
                "kind": "worldbuilding_compression",
                "severity": "medium",
                "label": "Есть признаки насыщенного мира при сравнительно небольшом хронометраже",
            }
        )
    if conflict >= 4 and word_count >= 90:
        signals.append(
            {
                "kind": "conflict_density",
                "severity": "low",
                "label": "Синопсис содержит несколько конфликтных сигналов; возможна высокая сюжетная плотность",
            }
        )

    return {
        "available": bool(text),
        "method": "lexical_structure_v1",
        "word_count": word_count,
        "sentence_count": sentence_count,
        "avg_sentence_words": avg_sentence_words,
        "density": density,
        "worldbuilding_signals": world,
        "conflict_signals": conflict,
        "relationship_signals": relationships,
        "risk_signals": signals,
        "note": (
            "Структурные сигналы синопсиса являются эвристикой и пока не являются отдельной обученной целью модели."
        ),
    }


def build_pre_release_profile(
    *,
    conn,
    year: int,
    runtime: int,
    director: str,
    writer: str | None,
    actors: list[str],
    synopsis: str | None,
    rating: float,
    uncertainty: dict | None,
    contributions: dict,
) -> dict:
    director_history = _person_history(conn, director, "director", year)
    writer_history = _person_history(conn, writer, "writer", year)
    actor_histories = [
        _person_history(conn, actor, "actor", year)
        for actor in actors[:3]
    ]

    weighted: list[tuple[float, bool]] = [(0.30, director_history.known)]
    weighted.append((0.25, writer_history.known))
    actor_weight = 0.30 / 3.0
    for index in range(3):
        known = index < len(actor_histories) and actor_histories[index].known
        weighted.append((actor_weight, known))
    synopsis_present = bool(str(synopsis or "").strip())
    weighted.append((0.15, synopsis_present))

    coverage = sum(weight for weight, known in weighted if known)
    coverage = round(max(0.0, min(1.0, coverage)), 3)
    if coverage >= 0.80:
        coverage_level = "high"
    elif coverage >= 0.55:
        coverage_level = "medium"
    else:
        coverage_level = "low"

    warnings: list[str] = []
    if not director_history.known:
        warnings.append("Мало или нет исторических данных о режиссёре в этой роли")
    if not writer_history.known:
        warnings.append("Сценарист не указан либо его историческая выборка недостаточна")
    unknown_actors = sum(1 for item in actor_histories if not item.known)
    if unknown_actors:
        warnings.append(f"Недостаточно истории для {unknown_actors} из {len(actor_histories)} переданных ключевых актёров")
    if not synopsis_present:
        warnings.append("Синопсис не передан: сценарно-структурный слой ограничен")

    groups = _group_contributions(contributions)
    strengths = [item for item in groups if item["value"] >= 0.08][:3]
    weaknesses = [item for item in groups if item["value"] <= -0.08][:3]

    synopsis_analysis = analyze_synopsis(synopsis, runtime)
    for signal in synopsis_analysis["risk_signals"]:
        weaknesses.append(
            {
                "key": f"synopsis:{signal['kind']}",
                "label": signal["label"],
                "value": None,
                "factors": [],
                "evidence": "synopsis_heuristic",
            }
        )

    lower = None
    upper = None
    if isinstance(uncertainty, dict):
        try:
            lower = float(uncertainty.get("lower"))
            upper = float(uncertainty.get("upper"))
        except (TypeError, ValueError):
            lower = upper = None

    expected = float(rating)
    if coverage_level == "low":
        potential_level = "uncertain"
    elif upper is not None and upper >= 8.0:
        potential_level = "high_upside"
    elif expected >= 7.0 or (upper is not None and upper >= 7.2):
        potential_level = "promising"
    else:
        potential_level = "moderate"

    return {
        "method": "pre_release_profile_v1",
        "validated_target": False,
        "expected_rating": round(expected, 2),
        "potential": {
            "level": potential_level,
            "range_lower": round(lower, 2) if lower is not None else None,
            "range_upper": round(upper, 2) if upper is not None else None,
            "note": (
                "Потенциал — диагностический профиль поверх рейтинга, SHAP, исторического покрытия команды и доступного синопсиса; это не отдельная обученная целевая переменная."
            ),
        },
        "data_coverage": {
            "score": coverage,
            "percent": int(round(coverage * 100)),
            "level": coverage_level,
            "warnings": warnings,
            "director": director_history.to_dict(),
            "writer": writer_history.to_dict(),
            "actors": [item.to_dict() for item in actor_histories],
        },
        "synopsis": synopsis_analysis,
        "dimensions": groups,
        "likely_strengths": strengths,
        "likely_weaknesses": weaknesses[:5],
        "disclaimer": (
            "Сильные и слабые стороны являются вероятными pre-release сигналами. Они не описывают фактическое качество ещё не вышедшего фильма."
        ),
    }
