from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import duckdb

from settings import config
from src.production_context import ProductionContextStore
from src.production_identity import (
    ProductionIdentityHistory,
    normalize_identity_alias,
)


class ProductionContextMaterializer:
    """Материализует cached Wikidata production metadata в factual registry.

    Важный temporal контракт: ``known_at`` равен фактическому ``fetched_at``.
    Materializer никогда не backdate-ит текущий Wikidata факт к дате релиза.
    При повторном наблюдении сохраняется самое раннее уже зафиксированное
    ``known_at``, поэтому повторный sync не делает старый факт искусственно новым.
    """

    STATE_KEY = "wikidata_production_materializer_cursor"

    def __init__(
        self,
        *,
        enrichment_db: str | Path | None = None,
        production_store: ProductionContextStore,
    ) -> None:
        self.enrichment_path = Path(enrichment_db or config.ENRICHMENT_DB_PATH)
        self.enrichment = duckdb.connect(str(self.enrichment_path), read_only=True)
        self.store = production_store
        self.identity = ProductionIdentityHistory(production_store)
        self._ensure_state_schema()

    def close(self) -> None:
        self.enrichment.close()

    def _ensure_state_schema(self) -> None:
        self.store.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS production_materialization_state (
                state_key VARCHAR PRIMARY KEY,
                state_value VARCHAR NOT NULL,
                updated_at TIMESTAMP WITH TIME ZONE NOT NULL
            )
            """
        )

    def get_cursor(self) -> str:
        row = self.store.conn.execute(
            "SELECT state_value FROM production_materialization_state WHERE state_key = ?",
            [self.STATE_KEY],
        ).fetchone()
        return str(row[0]) if row else ""

    def set_cursor(self, value: str) -> None:
        self.store.conn.execute(
            """
            INSERT INTO production_materialization_state(state_key, state_value, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(state_key) DO UPDATE SET
                state_value = excluded.state_value,
                updated_at = excluded.updated_at
            """,
            [self.STATE_KEY, value, datetime.now(timezone.utc)],
        )

    def reset_cursor(self) -> None:
        self.store.conn.execute(
            "DELETE FROM production_materialization_state WHERE state_key = ?",
            [self.STATE_KEY],
        )

    def load_rows(self, *, after_imdb: str, limit: int) -> list[dict[str, Any]]:
        rows = self.enrichment.execute(
            """
            SELECT
                f.imdb_id,
                f.imdb_title,
                f.imdb_year,
                f.wikidata_id,
                f.wikidata_json,
                p.metadata_json,
                p.fetched_at
            FROM film_enrichment f
            JOIN production_wikidata p USING (imdb_id)
            WHERE f.status = 'ok'
              AND p.status = 'ok'
              AND f.wikidata_id IS NOT NULL
              AND f.imdb_id > ?
            ORDER BY f.imdb_id
            LIMIT ?
            """,
            [after_imdb, limit],
        ).fetchall()
        return [
            {
                "imdb_id": str(row[0]),
                "title": str(row[1] or row[0]),
                "year": int(row[2]) if row[2] is not None else None,
                "wikidata_id": str(row[3]),
                "wikidata": json.loads(row[4] or "{}"),
                "production": json.loads(row[5] or "{}"),
                "fetched_at": row[6],
            }
            for row in rows
        ]

    @staticmethod
    def _parse_release_at(wikidata: dict[str, Any]) -> datetime | None:
        candidates: list[datetime] = []
        for raw in wikidata.get("release_dates") or []:
            text = str(raw or "").strip().replace("Z", "+00:00")
            if text.startswith("+"):
                text = text[1:]
            try:
                parsed = datetime.fromisoformat(text)
            except ValueError:
                continue
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            candidates.append(parsed.astimezone(timezone.utc))
        return min(candidates) if candidates else None

    @staticmethod
    def _canonical_name(item: dict[str, Any]) -> str:
        return str(
            item.get("label_en")
            or item.get("label_ru")
            or item.get("qid")
            or "Unknown"
        ).strip()

    def _earliest_timestamp(
        self,
        *,
        table: str,
        id_column: str,
        id_value: str,
        timestamp_column: str,
        observed_at: datetime,
    ) -> datetime:
        allowed = {
            ("production_projects", "project_id", "identity_known_at"),
            ("project_entity_links", "link_id", "known_at"),
            ("project_group_links", "link_id", "known_at"),
        }
        if (table, id_column, timestamp_column) not in allowed:
            raise ValueError("Недопустимая таблица для temporal merge")
        row = self.store.conn.execute(
            f"SELECT {timestamp_column} FROM {table} WHERE {id_column} = ? LIMIT 1",
            [id_value],
        ).fetchone()
        existing = row[0] if row else None
        return min(existing, observed_at) if existing is not None else observed_at

    def _earliest_alias_timestamp(
        self,
        *,
        table: str,
        target_column: str,
        target_id: str,
        alias_key: str,
        observed_at: datetime,
    ) -> datetime:
        allowed = {
            ("production_entity_aliases", "entity_id"),
            ("production_group_aliases", "group_id"),
        }
        if (table, target_column) not in allowed:
            raise ValueError("Недопустимая alias-таблица для temporal merge")
        row = self.store.conn.execute(
            f"SELECT known_at FROM {table} WHERE alias_key = ? AND {target_column} = ? LIMIT 1",
            [alias_key, target_id],
        ).fetchone()
        existing = row[0] if row else None
        return min(existing, observed_at) if existing is not None else observed_at

    def _upsert_aliases(
        self,
        *,
        target_kind: str,
        target_id: str,
        item: dict[str, Any],
        source_id: str,
        observed_at: datetime,
    ) -> None:
        names: list[str] = []
        for key in ("label_en", "label_ru"):
            value = str(item.get(key) or "").strip()
            if value and value not in names:
                names.append(value)
        canonical = self._canonical_name(item)
        for alias in names:
            if alias == canonical:
                continue
            alias_key = normalize_identity_alias(alias)
            if target_kind == "entity":
                known_at = self._earliest_alias_timestamp(
                    table="production_entity_aliases",
                    target_column="entity_id",
                    target_id=target_id,
                    alias_key=alias_key,
                    observed_at=observed_at,
                )
                self.identity.add_entity_alias(
                    {
                        "entity_id": target_id,
                        "alias": alias,
                        "known_at": known_at,
                        "source_id": source_id,
                    }
                )
            else:
                known_at = self._earliest_alias_timestamp(
                    table="production_group_aliases",
                    target_column="group_id",
                    target_id=target_id,
                    alias_key=alias_key,
                    observed_at=observed_at,
                )
                self.identity.add_group_alias(
                    {
                        "group_id": target_id,
                        "alias": alias,
                        "known_at": known_at,
                        "source_id": source_id,
                    }
                )

    def _materialize_entity(
        self,
        *,
        imdb_id: str,
        item: dict[str, Any],
        role: str,
        kind: str,
        source_id: str,
        observed_at: datetime,
    ) -> None:
        qid = str(item.get("qid") or "").strip()
        if not qid:
            return
        entity_id = f"wikidata:{qid}"
        self.store.upsert_entity(
            {
                "entity_id": entity_id,
                "kind": kind,
                "name": self._canonical_name(item),
                "external_id": f"wikidata:{qid}",
            }
        )
        self._upsert_aliases(
            target_kind="entity",
            target_id=entity_id,
            item=item,
            source_id=source_id,
            observed_at=observed_at,
        )
        link_id = f"wikidata:{imdb_id}:{role}:{qid}"
        known_at = self._earliest_timestamp(
            table="project_entity_links",
            id_column="link_id",
            id_value=link_id,
            timestamp_column="known_at",
            observed_at=observed_at,
        )
        self.store.link_entity(
            {
                "link_id": link_id,
                "project_id": imdb_id,
                "entity_id": entity_id,
                "role": role,
                "stage": "unknown",
                "known_at": known_at,
                "source_id": source_id,
                "note": "Автоматически материализовано из cached Wikidata; known_at не backdate-ится.",
            }
        )

    def _materialize_series(
        self,
        *,
        imdb_id: str,
        item: dict[str, Any],
        source_id: str,
        observed_at: datetime,
    ) -> None:
        qid = str(item.get("qid") or "").strip()
        if not qid:
            return
        group_id = f"wikidata:{qid}"
        self.identity.upsert_group(
            {
                "group_id": group_id,
                "kind": "franchise",
                "name": self._canonical_name(item),
                "external_id": f"wikidata:{qid}",
            }
        )
        self._upsert_aliases(
            target_kind="group",
            target_id=group_id,
            item=item,
            source_id=source_id,
            observed_at=observed_at,
        )
        link_id = f"wikidata:{imdb_id}:franchise:{qid}"
        known_at = self._earliest_timestamp(
            table="project_group_links",
            id_column="link_id",
            id_value=link_id,
            timestamp_column="known_at",
            observed_at=observed_at,
        )
        self.identity.link_group(
            {
                "link_id": link_id,
                "project_id": imdb_id,
                "group_id": group_id,
                "known_at": known_at,
                "source_id": source_id,
                "note": (
                    "Wikidata P179 (part of the series) материализуется только как franchise candidate; "
                    "shared_universe автоматически не выводится."
                ),
            }
        )

    def materialize_row(self, row: dict[str, Any]) -> dict[str, int]:
        imdb_id = row["imdb_id"]
        qid = row["wikidata_id"]
        observed_at = row["fetched_at"]
        if observed_at.tzinfo is None:
            observed_at = observed_at.replace(tzinfo=timezone.utc)
        observed_at = observed_at.astimezone(timezone.utc)
        source_id = f"wikidata:{qid}"
        self.store.upsert_source(
            {
                "source_id": source_id,
                "url": f"https://www.wikidata.org/wiki/{qid}",
                "title": f"Wikidata production metadata: {row['title']}",
                "publisher": "Wikidata",
                "retrieved_at": observed_at,
                "confidence": 0.85,
                "note": (
                    "Автоматический cached snapshot. Temporal known_at равен fetched_at; "
                    "историческое знание не восстанавливается задним числом."
                ),
            }
        )

        existing = self.store.conn.execute(
            "SELECT release_at, identity_known_at FROM production_projects WHERE project_id = ?",
            [imdb_id],
        ).fetchone()
        release_at = (
            existing[0]
            if existing and existing[0] is not None
            else self._parse_release_at(row["wikidata"])
        )
        identity_known_at = self._earliest_timestamp(
            table="production_projects",
            id_column="project_id",
            id_value=imdb_id,
            timestamp_column="identity_known_at",
            observed_at=observed_at,
        )
        self.store.upsert_project(
            {
                "project_id": imdb_id,
                "imdb_id": imdb_id,
                "title": row["title"],
                "release_at": release_at,
                "identity_known_at": identity_known_at,
            }
        )

        counts = {"production_companies": 0, "producers": 0, "series": 0}
        metadata = row["production"]
        for item in metadata.get("production_companies") or []:
            if isinstance(item, dict):
                self._materialize_entity(
                    imdb_id=imdb_id,
                    item=item,
                    role="production_company",
                    kind="production_company",
                    source_id=source_id,
                    observed_at=observed_at,
                )
                counts["production_companies"] += 1
        for item in metadata.get("producers") or []:
            if isinstance(item, dict):
                self._materialize_entity(
                    imdb_id=imdb_id,
                    item=item,
                    role="producer",
                    kind="producer",
                    source_id=source_id,
                    observed_at=observed_at,
                )
                counts["producers"] += 1
        for item in metadata.get("series") or []:
            if isinstance(item, dict):
                self._materialize_series(
                    imdb_id=imdb_id,
                    item=item,
                    source_id=source_id,
                    observed_at=observed_at,
                )
                counts["series"] += 1
        return counts

    def materialize_batch(
        self,
        *,
        after_imdb: str,
        limit: int,
    ) -> tuple[int, dict[str, int], str]:
        rows = self.load_rows(after_imdb=after_imdb, limit=limit)
        totals = {"production_companies": 0, "producers": 0, "series": 0}
        cursor = after_imdb
        for row in rows:
            counts = self.materialize_row(row)
            for key, value in counts.items():
                totals[key] += value
            cursor = row["imdb_id"]
        return len(rows), totals, cursor
