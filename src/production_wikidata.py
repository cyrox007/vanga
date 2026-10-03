from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import duckdb

from settings import config
from src.wikimedia_enrichment import WIKIDATA_SPARQL_URL, WikimediaClient


logger = logging.getLogger(__name__)

PRODUCTION_RELATIONS = {
    "production_companies": "P272",
    "producers": "P162",
    "series": "P179",
}


@dataclass(frozen=True)
class ProductionCandidate:
    imdb_id: str
    wikidata_id: str


class ProductionWikidataClient:
    """Production-specific запросы поверх общего throttled WikimediaClient."""

    def __init__(self, client: WikimediaClient | None = None) -> None:
        self.client = client or WikimediaClient()

    @staticmethod
    def _clean_qids(values: Iterable[str]) -> list[str]:
        return sorted({value for value in values if re.fullmatch(r"Q\d+", value or "")})

    def fetch_relations(self, film_qids: Iterable[str]) -> dict[str, dict[str, list[str]]]:
        qids = self._clean_qids(film_qids)
        if not qids:
            return {}
        values = " ".join("wd:" + qid for qid in qids)
        branches = []
        for field, prop in PRODUCTION_RELATIONS.items():
            branches.append(
                "{ ?film wdt:%s ?entity . BIND(%s AS ?relation) }"
                % (prop, json.dumps(field))
            )
        query = f"""
PREFIX wd: <http://www.wikidata.org/entity/>
PREFIX wdt: <http://www.wikidata.org/prop/direct/>

SELECT ?film ?relation ?entity WHERE {{
  VALUES ?film {{ {values} }}
  {" UNION ".join(branches)}
}}
"""
        payload = self.client._request_json(
            "POST",
            WIKIDATA_SPARQL_URL,
            data={"query": query, "format": "json"},
            headers={"Accept": "application/sparql-results+json"},
        )
        result: dict[str, dict[str, list[str]]] = {
            qid: {field: [] for field in PRODUCTION_RELATIONS}
            for qid in qids
        }
        for binding in payload.get("results", {}).get("bindings", []):
            film_uri = binding.get("film", {}).get("value")
            entity_uri = binding.get("entity", {}).get("value")
            relation = binding.get("relation", {}).get("value")
            if not film_uri or not entity_uri or relation not in PRODUCTION_RELATIONS:
                continue
            film_qid = film_uri.rsplit("/", 1)[-1]
            entity_qid = entity_uri.rsplit("/", 1)[-1]
            if film_qid not in result or not re.fullmatch(r"Q\d+", entity_qid):
                continue
            result[film_qid][relation].append(entity_qid)
        for fields in result.values():
            for field in fields:
                fields[field] = sorted(set(fields[field]))
        return result

    def fetch_labels(self, entity_qids: Iterable[str]) -> dict[str, dict[str, str | None]]:
        qids = self._clean_qids(entity_qids)
        if not qids:
            return {}
        values = " ".join("wd:" + qid for qid in qids)
        query = f"""
PREFIX wd: <http://www.wikidata.org/entity/>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>

SELECT ?item ?labelEn ?labelRu WHERE {{
  VALUES ?item {{ {values} }}
  OPTIONAL {{ ?item rdfs:label ?labelEn . FILTER(LANG(?labelEn) = "en") }}
  OPTIONAL {{ ?item rdfs:label ?labelRu . FILTER(LANG(?labelRu) = "ru") }}
}}
"""
        payload = self.client._request_json(
            "POST",
            WIKIDATA_SPARQL_URL,
            data={"query": query, "format": "json"},
            headers={"Accept": "application/sparql-results+json"},
        )
        result: dict[str, dict[str, str | None]] = {
            qid: {"en": None, "ru": None} for qid in qids
        }
        for binding in payload.get("results", {}).get("bindings", []):
            item_uri = binding.get("item", {}).get("value")
            if not item_uri:
                continue
            qid = item_uri.rsplit("/", 1)[-1]
            if qid not in result:
                continue
            label_en = binding.get("labelEn", {}).get("value")
            label_ru = binding.get("labelRu", {}).get("value")
            if label_en:
                result[qid]["en"] = str(label_en)
            if label_ru:
                result[qid]["ru"] = str(label_ru)
        return result

    def fetch_batch(self, film_qids: Iterable[str]) -> dict[str, dict[str, Any]]:
        relations = self.fetch_relations(film_qids)
        entity_qids: set[str] = set()
        for fields in relations.values():
            for values in fields.values():
                entity_qids.update(values)
        labels = self.fetch_labels(entity_qids)

        result: dict[str, dict[str, Any]] = {}
        for film_qid, fields in relations.items():
            payload: dict[str, Any] = {}
            for field, qids in fields.items():
                payload[field] = [
                    {
                        "qid": qid,
                        "label_en": labels.get(qid, {}).get("en"),
                        "label_ru": labels.get(qid, {}).get("ru"),
                    }
                    for qid in qids
                ]
            result[film_qid] = payload
        return result


class ProductionWikidataCache:
    """Sidecar cache внутри enrichment.duckdb для production metadata."""

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path or config.ENRICHMENT_DB_PATH)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = duckdb.connect(str(self.path))
        self._ensure_schema()

    def close(self) -> None:
        self.conn.close()

    def _ensure_schema(self) -> None:
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS production_wikidata (
                imdb_id VARCHAR PRIMARY KEY,
                wikidata_id VARCHAR NOT NULL,
                metadata_json VARCHAR NOT NULL,
                status VARCHAR NOT NULL,
                error VARCHAR,
                fetched_at TIMESTAMP WITH TIME ZONE NOT NULL
            )
            """
        )
        # enrichment_state уже создаётся основным EnrichmentStore, но sidecar
        # обязан работать и при отдельном запуске на существующей БД.
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS enrichment_state (
                state_key VARCHAR PRIMARY KEY,
                state_value VARCHAR NOT NULL,
                updated_at TIMESTAMP WITH TIME ZONE NOT NULL
            )
            """
        )

    def get_state(self, key: str, default: str = "") -> str:
        row = self.conn.execute(
            "SELECT state_value FROM enrichment_state WHERE state_key = ?",
            [key],
        ).fetchone()
        return str(row[0]) if row else default

    def set_state(self, key: str, value: str) -> None:
        self.conn.execute(
            """
            INSERT INTO enrichment_state(state_key, state_value, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(state_key) DO UPDATE SET
                state_value = excluded.state_value,
                updated_at = excluded.updated_at
            """,
            [key, value, datetime.now(timezone.utc)],
        )

    def reset_state(self, key: str) -> None:
        self.conn.execute("DELETE FROM enrichment_state WHERE state_key = ?", [key])

    def load_candidates(
        self,
        *,
        after_imdb: str,
        limit: int,
        retry_errors: bool = False,
    ) -> list[ProductionCandidate]:
        if retry_errors:
            rows = self.conn.execute(
                """
                SELECT p.imdb_id, p.wikidata_id
                FROM production_wikidata p
                WHERE p.status = 'error'
                ORDER BY p.fetched_at, p.imdb_id
                LIMIT ?
                """,
                [limit],
            ).fetchall()
        else:
            rows = self.conn.execute(
                """
                SELECT f.imdb_id, f.wikidata_id
                FROM film_enrichment f
                LEFT JOIN production_wikidata p USING (imdb_id)
                WHERE f.status = 'ok'
                  AND f.wikidata_id IS NOT NULL
                  AND f.imdb_id > ?
                  AND p.imdb_id IS NULL
                ORDER BY f.imdb_id
                LIMIT ?
                """,
                [after_imdb, limit],
            ).fetchall()
        return [
            ProductionCandidate(imdb_id=str(row[0]), wikidata_id=str(row[1]))
            for row in rows
        ]

    def upsert(
        self,
        candidate: ProductionCandidate,
        *,
        metadata: dict[str, Any] | None,
        status: str,
        error: str | None = None,
        fetched_at: datetime | None = None,
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO production_wikidata(
                imdb_id, wikidata_id, metadata_json, status, error, fetched_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(imdb_id) DO UPDATE SET
                wikidata_id = excluded.wikidata_id,
                metadata_json = excluded.metadata_json,
                status = excluded.status,
                error = excluded.error,
                fetched_at = excluded.fetched_at
            """,
            [
                candidate.imdb_id,
                candidate.wikidata_id,
                json.dumps(metadata or {}, ensure_ascii=False, sort_keys=True),
                status,
                error,
                fetched_at or datetime.now(timezone.utc),
            ],
        )


def enrich_production_batch(
    candidates: list[ProductionCandidate],
    *,
    client: ProductionWikidataClient,
    cache: ProductionWikidataCache,
) -> tuple[int, int]:
    if not candidates:
        return 0, 0
    try:
        metadata_by_film = client.fetch_batch(candidate.wikidata_id for candidate in candidates)
    except Exception as exc:
        logger.exception("Не удалось получить batch production metadata")
        for candidate in candidates:
            cache.upsert(candidate, metadata=None, status="error", error=str(exc)[:2000])
        return 0, len(candidates)

    success = 0
    failed = 0
    for candidate in candidates:
        metadata = metadata_by_film.get(candidate.wikidata_id)
        if metadata is None:
            cache.upsert(
                candidate,
                metadata={},
                status="error",
                error="Wikidata не вернула production metadata для film QID",
            )
            failed += 1
            continue
        cache.upsert(candidate, metadata=metadata, status="ok")
        success += 1
    return success, failed
