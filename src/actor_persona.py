from __future__ import annotations

import json
import math
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

import duckdb

from settings import config

META_ROLE_TYPES = {
    "ordinary",
    "self_portrayal",
    "fictionalized_self",
    "explicit_persona_reference",
    "iconic_character_cameo",
    "parody_or_easter_egg",
    "meta_ensemble_casting",
}
ROLE_FUNCTIONS = {"protagonist", "antagonist", "supporting", "cameo", "unknown"}


class ActorPersonaValidationError(ValueError):
    pass


def _dt(value: Any, *, field_name: str) -> datetime:
    if isinstance(value, datetime):
        result = value
    else:
        text = str(value or "").strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            result = datetime.fromisoformat(text)
        except ValueError as exc:
            raise ActorPersonaValidationError(f"{field_name} должен быть ISO datetime") from exc
    if result.tzinfo is None:
        result = result.replace(tzinfo=timezone.utc)
    return result.astimezone(timezone.utc)


def _text(value: Any, *, field_name: str, limit: int = 300, required: bool = True) -> str:
    result = " ".join(str(value or "").strip().split())
    if required and not result:
        raise ActorPersonaValidationError(f"{field_name} обязателен")
    if len(result) > limit:
        raise ActorPersonaValidationError(f"{field_name} длиннее {limit} символов")
    return result


def _confidence(value: Any) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ActorPersonaValidationError("confidence должен быть числом") from exc
    if not 0.0 <= result <= 1.0:
        raise ActorPersonaValidationError("confidence должен быть в диапазоне 0..1")
    return result


def _json_list(value: Any, *, field_name: str) -> str:
    if value in (None, ""):
        return "[]"
    if not isinstance(value, (list, tuple, set)):
        raise ActorPersonaValidationError(f"{field_name} должен быть массивом")
    rows = []
    for item in value:
        clean = _text(item, field_name=field_name, limit=120)
        if clean not in rows:
            rows.append(clean)
    return json.dumps(rows, ensure_ascii=False)


class ActorPersonaStore:
    """Temporal-safe registry actor→character→work и actor persona snapshots.

    Registry не используется production inference автоматически. Он хранит
    воспроизводимые факты/аннотации и строит candidate features as-of cutoff для
    последующей temporal ablation.
    """

    def __init__(self, path: str | Path | None = None) -> None:
        default_path = getattr(
            config,
            "ACTOR_PERSONA_DB_PATH",
            str(Path(config.ABSPATH) / "actor_persona.duckdb"),
        )
        self.path = Path(path or default_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = duckdb.connect(str(self.path))
        self.conn.execute("SET TimeZone='UTC'")
        self._ensure_schema()

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "ActorPersonaStore":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def _ensure_schema(self) -> None:
        statements = [
            """
            CREATE TABLE IF NOT EXISTS actor_persona_sources(
                source_id VARCHAR PRIMARY KEY,
                provider VARCHAR NOT NULL,
                url VARCHAR,
                retrieved_at TIMESTAMPTZ NOT NULL,
                usage_basis VARCHAR NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS actor_persona_characters(
                character_id VARCHAR PRIMARY KEY,
                canonical_name VARCHAR NOT NULL,
                franchise_id VARCHAR,
                universe_id VARCHAR,
                external_ids_json VARCHAR NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS actor_persona_role_appearances(
                appearance_id VARCHAR PRIMARY KEY,
                actor_id VARCHAR NOT NULL,
                work_id VARCHAR NOT NULL,
                character_id VARCHAR,
                character_name VARCHAR NOT NULL,
                work_release_at TIMESTAMPTZ,
                known_at TIMESTAMPTZ NOT NULL,
                role_function VARCHAR NOT NULL,
                meta_role_type VARCHAR NOT NULL,
                genres_json VARCHAR NOT NULL,
                archetypes_json VARCHAR NOT NULL,
                iconic BOOLEAN NOT NULL,
                plot_relevance DOUBLE,
                audience_recognition_dependency DOUBLE,
                source_id VARCHAR NOT NULL,
                confidence DOUBLE NOT NULL
            )
            """,
            """
            CREATE INDEX IF NOT EXISTS idx_actor_persona_actor_known
            ON actor_persona_role_appearances(actor_id, known_at)
            """,
            """
            CREATE INDEX IF NOT EXISTS idx_actor_persona_character
            ON actor_persona_role_appearances(character_id, known_at)
            """,
        ]
        for statement in statements:
            self.conn.execute(statement)

    def add_source(self, payload: dict[str, Any]) -> str:
        source_id = _text(payload.get("source_id") or uuid4(), field_name="source_id", limit=180)
        provider = _text(payload.get("provider"), field_name="provider", limit=120)
        url = _text(payload.get("url"), field_name="url", limit=2000, required=False) or None
        retrieved_at = _dt(payload.get("retrieved_at"), field_name="retrieved_at")
        usage_basis = _text(payload.get("usage_basis") or "structured_annotation", field_name="usage_basis", limit=300)
        self.conn.execute(
            """INSERT INTO actor_persona_sources VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(source_id) DO UPDATE SET
                 provider=excluded.provider, url=excluded.url,
                 retrieved_at=excluded.retrieved_at, usage_basis=excluded.usage_basis""",
            [source_id, provider, url, retrieved_at, usage_basis],
        )
        return source_id

    def upsert_character(self, payload: dict[str, Any]) -> str:
        character_id = _text(payload.get("character_id"), field_name="character_id", limit=180)
        name = _text(payload.get("canonical_name"), field_name="canonical_name", limit=300)
        franchise = _text(payload.get("franchise_id"), field_name="franchise_id", limit=180, required=False) or None
        universe = _text(payload.get("universe_id"), field_name="universe_id", limit=180, required=False) or None
        external = payload.get("external_ids") or {}
        if not isinstance(external, dict):
            raise ActorPersonaValidationError("external_ids должен быть объектом")
        self.conn.execute(
            """INSERT INTO actor_persona_characters VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(character_id) DO UPDATE SET
                 canonical_name=excluded.canonical_name,
                 franchise_id=excluded.franchise_id,
                 universe_id=excluded.universe_id,
                 external_ids_json=excluded.external_ids_json""",
            [character_id, name, franchise, universe, json.dumps(external, ensure_ascii=False, sort_keys=True)],
        )
        return character_id

    def add_role_appearance(self, payload: dict[str, Any]) -> str:
        appearance_id = _text(payload.get("appearance_id") or uuid4(), field_name="appearance_id", limit=180)
        actor_id = _text(payload.get("actor_id"), field_name="actor_id", limit=180)
        work_id = _text(payload.get("work_id"), field_name="work_id", limit=180)
        character_id = _text(payload.get("character_id"), field_name="character_id", limit=180, required=False) or None
        character_name = _text(payload.get("character_name") or "Unknown", field_name="character_name", limit=300)
        role_function = str(payload.get("role_function") or "unknown").strip().casefold()
        meta_role_type = str(payload.get("meta_role_type") or "ordinary").strip().casefold()
        if role_function not in ROLE_FUNCTIONS:
            raise ActorPersonaValidationError("Неизвестный role_function")
        if meta_role_type not in META_ROLE_TYPES:
            raise ActorPersonaValidationError("Неизвестный meta_role_type")
        source_id = _text(payload.get("source_id"), field_name="source_id", limit=180)
        if self.conn.execute("SELECT 1 FROM actor_persona_sources WHERE source_id=?", [source_id]).fetchone() is None:
            raise ActorPersonaValidationError(f"Неизвестный source_id: {source_id}")
        if character_id and self.conn.execute("SELECT 1 FROM actor_persona_characters WHERE character_id=?", [character_id]).fetchone() is None:
            raise ActorPersonaValidationError(f"Неизвестный character_id: {character_id}")
        release_at = None
        if payload.get("work_release_at") not in (None, ""):
            release_at = _dt(payload.get("work_release_at"), field_name="work_release_at")
        known_at = _dt(payload.get("known_at"), field_name="known_at")
        if release_at is not None and known_at > release_at and bool(payload.get("pre_release_claim", False)):
            raise ActorPersonaValidationError("pre_release_claim не может стать известен после релиза")
        def score(name: str) -> float | None:
            value = payload.get(name)
            if value in (None, ""):
                return None
            result = float(value)
            if not 0.0 <= result <= 1.0:
                raise ActorPersonaValidationError(f"{name} должен быть в диапазоне 0..1")
            return result
        self.conn.execute(
            """INSERT INTO actor_persona_role_appearances(
                 appearance_id, actor_id, work_id, character_id, character_name,
                 work_release_at, known_at, role_function, meta_role_type,
                 genres_json, archetypes_json, iconic, plot_relevance,
                 audience_recognition_dependency, source_id, confidence
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                appearance_id, actor_id, work_id, character_id, character_name,
                release_at, known_at, role_function, meta_role_type,
                _json_list(payload.get("genres"), field_name="genres"),
                _json_list(payload.get("archetypes"), field_name="archetypes"),
                bool(payload.get("iconic", False)), score("plot_relevance"),
                score("audience_recognition_dependency"), source_id,
                _confidence(payload.get("confidence", 1.0)),
            ],
        )
        return appearance_id

    def history_as_of(self, actor_id: str, cutoff: Any) -> list[dict[str, Any]]:
        actor_id = _text(actor_id, field_name="actor_id", limit=180)
        cutoff_dt = _dt(cutoff, field_name="cutoff")
        rows = self.conn.execute(
            """SELECT appearance_id, work_id, character_id, character_name,
                      work_release_at, known_at, role_function, meta_role_type,
                      genres_json, archetypes_json, iconic, plot_relevance,
                      audience_recognition_dependency, source_id, confidence
               FROM actor_persona_role_appearances
               WHERE actor_id=? AND known_at<=?
                 AND (work_release_at IS NULL OR work_release_at<?)
               ORDER BY COALESCE(work_release_at, known_at), appearance_id""",
            [actor_id, cutoff_dt, cutoff_dt],
        ).fetchall()
        return [
            {
                "appearance_id": r[0], "work_id": r[1], "character_id": r[2],
                "character_name": r[3], "work_release_at": r[4], "known_at": r[5],
                "role_function": r[6], "meta_role_type": r[7],
                "genres": json.loads(r[8]), "archetypes": json.loads(r[9]),
                "iconic": bool(r[10]), "plot_relevance": r[11],
                "audience_recognition_dependency": r[12], "source_id": r[13],
                "confidence": float(r[14]),
            }
            for r in rows
        ]

    def persona_snapshot_as_of(self, actor_id: str, cutoff: Any) -> dict[str, Any]:
        history = self.history_as_of(actor_id, cutoff)
        genres = Counter(g.casefold() for row in history for g in row["genres"])
        archetypes = Counter(a.casefold() for row in history for a in row["archetypes"])
        role_functions = Counter(row["role_function"] for row in history)
        meta_roles = Counter(row["meta_role_type"] for row in history if row["meta_role_type"] != "ordinary")
        characters = {row["character_id"] for row in history if row["character_id"]}
        repeated = Counter(row["character_id"] for row in history if row["character_id"])
        repeats = sum(1 for count in repeated.values() if count > 1)
        n = len(history)
        dominant_share = 0.0
        if role_functions and n:
            dominant_share = max(role_functions.values()) / n
        versatility = 0.0 if n <= 1 else min(1.0, (len(role_functions) + len(genres) + len(archetypes)) / max(3.0, n * 1.5))
        return {
            "actor_id": actor_id,
            "cutoff": _dt(cutoff, field_name="cutoff").isoformat(),
            "works_count": n,
            "genres": dict(genres),
            "archetypes": dict(archetypes),
            "role_functions": dict(role_functions),
            "meta_roles": dict(meta_roles),
            "iconic_roles_count": sum(1 for row in history if row["iconic"]),
            "unique_characters": len(characters),
            "repeated_characters": repeats,
            "role_versatility": round(versatility, 6),
            "dominant_role_share": round(dominant_share, 6),
        }

    def candidate_features_as_of(self, payload: dict[str, Any], cutoff: Any) -> dict[str, Any]:
        actor_id = _text(payload.get("actor_id"), field_name="actor_id", limit=180)
        snapshot = self.persona_snapshot_as_of(actor_id, cutoff)
        meta_role = str(payload.get("meta_role_type") or "ordinary").strip().casefold()
        if meta_role not in META_ROLE_TYPES:
            raise ActorPersonaValidationError("Неизвестный meta_role_type")
        current_character_id = _text(payload.get("character_id"), field_name="character_id", limit=180, required=False) or None
        current_genres = {str(x).strip().casefold() for x in (payload.get("genres") or []) if str(x).strip()}
        current_archetypes = {str(x).strip().casefold() for x in (payload.get("archetypes") or []) if str(x).strip()}
        genre_history = set(snapshot["genres"])
        archetype_history = set(snapshot["archetypes"])
        overlap = 0.0
        denom = len(current_genres | current_archetypes)
        if denom:
            overlap = (len(current_genres & genre_history) + len(current_archetypes & archetype_history)) / denom
        inversion = 0.0
        if denom and snapshot["works_count"] >= 3:
            inversion = 1.0 - min(1.0, overlap)
        reuse = 0.0
        if current_character_id:
            row = self.conn.execute(
                """SELECT COUNT(*) FROM actor_persona_role_appearances
                   WHERE actor_id=? AND character_id=? AND known_at<=?
                     AND (work_release_at IS NULL OR work_release_at<?)""",
                [actor_id, current_character_id, _dt(cutoff, field_name="cutoff"), _dt(cutoff, field_name="cutoff")],
            ).fetchone()
            reuse = 1.0 if row and int(row[0]) > 0 else 0.0
        return {
            "actor_id": actor_id,
            "history_works_count": snapshot["works_count"],
            "role_versatility": snapshot["role_versatility"],
            "role_persona_match": round(min(1.0, overlap), 6) if denom else None,
            "role_persona_inversion": round(inversion, 6) if denom else None,
            "iconic_character_reuse": reuse if current_character_id else None,
            "self_portrayal": 1.0 if meta_role == "self_portrayal" else 0.0,
            "fictionalized_self": 1.0 if meta_role == "fictionalized_self" else 0.0,
            "explicit_persona_reference": 1.0 if meta_role == "explicit_persona_reference" else 0.0,
            "meta_cast_member": 1.0 if meta_role == "meta_ensemble_casting" else 0.0,
            "known_history": snapshot["works_count"] > 0,
            "network_required_for_inference": False,
        }

    def ensemble_features_as_of(self, roles: list[dict[str, Any]], cutoff: Any) -> dict[str, Any]:
        items = [self.candidate_features_as_of(role, cutoff) for role in roles]
        if not items:
            return {"cast_size": 0, "meta_cast_density": 0.0, "cast_persona_synergy": None}
        meta_density = sum(item["meta_cast_member"] for item in items) / len(items)
        matches = [item["role_persona_match"] for item in items if item["role_persona_match"] is not None]
        synergy = sum(matches) / len(matches) if matches else None
        return {
            "cast_size": len(items),
            "meta_cast_density": round(meta_density, 6),
            "cast_persona_synergy": round(synergy, 6) if synergy is not None and math.isfinite(synergy) else None,
            "known_history_ratio": round(sum(1 for item in items if item["known_history"]) / len(items), 6),
            "network_required_for_inference": False,
        }

    def stats(self) -> dict[str, int]:
        result = {}
        for key, table in (
            ("sources", "actor_persona_sources"),
            ("characters", "actor_persona_characters"),
            ("appearances", "actor_persona_role_appearances"),
        ):
            result[key] = int(self.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
        result["actors"] = int(self.conn.execute("SELECT COUNT(DISTINCT actor_id) FROM actor_persona_role_appearances").fetchone()[0])
        return result
