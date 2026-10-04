from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import uuid4

from src.actor_persona import (
    ActorPersonaStore,
    ActorPersonaValidationError,
    _confidence,
    _dt,
    _text,
)


class ActorPersonaStoryMapLinks:
    """Temporal-safe связь role appearance с узлом существующего StoryMap.

    Слой хранится в том же actor_persona.duckdb, но отдельно от character
    identity: narrative node может меняться между версиями StoryMap, не меняя
    идентичность персонажа или исторический credit актёра.
    """

    def __init__(self, store: ActorPersonaStore | str | Path | None = None) -> None:
        if isinstance(store, ActorPersonaStore):
            self.store = store
            self._owns_store = False
        else:
            self.store = ActorPersonaStore(store)
            self._owns_store = True
        self.conn = self.store.conn
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS actor_persona_storymap_links(
                link_id VARCHAR PRIMARY KEY,
                appearance_id VARCHAR NOT NULL,
                story_map_id VARCHAR NOT NULL,
                story_node_id VARCHAR NOT NULL,
                known_at TIMESTAMPTZ NOT NULL,
                source_id VARCHAR NOT NULL,
                confidence DOUBLE NOT NULL
            )
            """
        )
        self.conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_actor_persona_storymap_known
            ON actor_persona_storymap_links(appearance_id, known_at)
            """
        )

    def close(self) -> None:
        if self._owns_store:
            self.store.close()

    def __enter__(self) -> "ActorPersonaStoryMapLinks":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def add_link(self, payload: dict[str, Any]) -> str:
        appearance_id = _text(payload.get("appearance_id"), field_name="appearance_id", limit=180)
        story_map_id = _text(payload.get("story_map_id"), field_name="story_map_id", limit=180)
        story_node_id = _text(payload.get("story_node_id"), field_name="story_node_id", limit=180)
        source_id = _text(payload.get("source_id"), field_name="source_id", limit=180)
        if self.conn.execute(
            "SELECT 1 FROM actor_persona_role_appearances WHERE appearance_id=?",
            [appearance_id],
        ).fetchone() is None:
            raise ActorPersonaValidationError(f"Неизвестный appearance_id: {appearance_id}")
        if self.conn.execute(
            "SELECT 1 FROM actor_persona_sources WHERE source_id=?", [source_id]
        ).fetchone() is None:
            raise ActorPersonaValidationError(f"Неизвестный source_id: {source_id}")
        link_id = _text(payload.get("link_id") or uuid4(), field_name="link_id", limit=180)
        self.conn.execute(
            """
            INSERT INTO actor_persona_storymap_links
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                link_id,
                appearance_id,
                story_map_id,
                story_node_id,
                _dt(payload.get("known_at"), field_name="known_at"),
                source_id,
                _confidence(payload.get("confidence", 1.0)),
            ],
        )
        return link_id

    def links_as_of(self, appearance_id: str, cutoff: Any) -> list[dict[str, Any]]:
        clean_id = _text(appearance_id, field_name="appearance_id", limit=180)
        rows = self.conn.execute(
            """
            SELECT link_id, story_map_id, story_node_id, known_at, source_id, confidence
            FROM actor_persona_storymap_links
            WHERE appearance_id=? AND known_at<=?
            ORDER BY known_at, link_id
            """,
            [clean_id, _dt(cutoff, field_name="cutoff")],
        ).fetchall()
        return [
            {
                "link_id": row[0],
                "appearance_id": clean_id,
                "story_map_id": row[1],
                "story_node_id": row[2],
                "known_at": row[3],
                "source_id": row[4],
                "confidence": float(row[5]),
            }
            for row in rows
        ]
