from __future__ import annotations

import hashlib
import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from settings import config
from src.future_releases import FutureReleaseStore


WDQS_ENDPOINT = "https://query.wikidata.org/sparql"
ENRICHER_VERSION = 1
MAX_PROJECTS_PER_BATCH = 200
RELATIONS = {
    "director",
    "writer",
    "actor",
    "based_on",
    "franchise",
    "production_company",
}


class WikidataFutureEnrichmentError(RuntimeError):
    pass


def _canonical_json(payload: Any) -> str:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _fingerprint(payload: Any) -> str:
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def _dt(value: Any, *, field_name: str) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value or "").strip().replace("Z", "+00:00"))
        except (TypeError, ValueError) as exc:
            raise WikidataFutureEnrichmentError(
                f"{field_name} должен быть ISO-8601 datetime"
            ) from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _binding(binding: dict[str, Any], key: str) -> str | None:
    item = binding.get(key)
    if not isinstance(item, dict):
        return None
    value = str(item.get("value") or "").strip()
    return value or None


def _qid(value: str | None) -> str | None:
    text = str(value or "").strip().rsplit("/", 1)[-1]
    if text.startswith("Q") and text[1:].isdigit():
        return text
    return None


def _valid_imdb_person(value: str | None) -> str | None:
    text = str(value or "").strip()
    if text.startswith("nm") and text[2:].isdigit():
        return text
    return None


def _statement_key(value: str | None) -> str:
    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()[:16]


def _ordinal(value: str | None) -> int | None:
    text = str(value or "").strip()
    match = re.match(r"^(\d+)", text)
    if not match:
        return None
    parsed = int(match.group(1))
    return parsed if parsed >= 0 else None


class WikidataFutureEnricher:
    """Второй P9 pass: команда и factual context для уже найденных проектов.

    Сеть используется только на collector-стадии. Нормализованный результат
    совместим с ``FutureReleaseBatchImporter`` и содержит только факты,
    наблюдавшиеся к ``retrieved_at``. Inference этот класс не вызывает.
    """

    def __init__(
        self,
        *,
        endpoint: str = WDQS_ENDPOINT,
        cache_dir: str | Path | None = None,
        transport: Callable[[str], dict[str, Any]] | None = None,
        sleep_fn: Callable[[float], None] = time.sleep,
        monotonic_fn: Callable[[], float] = time.monotonic,
    ) -> None:
        self.endpoint = endpoint
        self.cache_dir = Path(
            cache_dir
            or (
                Path(config.ABSPATH)
                / "data"
                / "future_releases"
                / "wikidata"
                / "enrichment"
            )
        )
        self.transport = transport
        self.sleep_fn = sleep_fn
        self.monotonic_fn = monotonic_fn
        self._last_request_at: float | None = None

    @staticmethod
    def build_query(qids: list[str]) -> str:
        unique = sorted(set(str(item).strip() for item in qids if str(item).strip()))
        if not unique:
            raise WikidataFutureEnrichmentError("Нужен хотя бы один Wikidata QID")
        if len(unique) > MAX_PROJECTS_PER_BATCH:
            raise WikidataFutureEnrichmentError(
                f"За один enrichment batch поддерживается не больше {MAX_PROJECTS_PER_BATCH} проектов"
            )
        invalid = [item for item in unique if _qid(item) != item]
        if invalid:
            raise WikidataFutureEnrichmentError(
                "Некорректные Wikidata QID: " + ", ".join(invalid[:10])
            )
        values = " ".join(f"wd:{item}" for item in unique)
        return f"""
PREFIX wd: <http://www.wikidata.org/entity/>
PREFIX wdt: <http://www.wikidata.org/prop/direct/>
PREFIX p: <http://www.wikidata.org/prop/>
PREFIX ps: <http://www.wikidata.org/prop/statement/>
PREFIX pq: <http://www.wikidata.org/prop/qualifier/>
PREFIX wikibase: <http://wikiba.se/ontology#>
PREFIX bd: <http://www.bigdata.com/rdf#>

SELECT ?film ?filmImdb ?relation ?statement ?target ?targetLabel ?targetImdb ?ordinal WHERE {{
  VALUES ?film {{ {values} }}
  OPTIONAL {{ ?film wdt:P345 ?filmImdb . }}
  {{
    ?film p:P57 ?statement .
    ?statement ps:P57 ?target ; wikibase:rank ?rank .
    FILTER(?rank != wikibase:DeprecatedRank)
    BIND("director" AS ?relation)
  }}
  UNION
  {{
    ?film p:P58 ?statement .
    ?statement ps:P58 ?target ; wikibase:rank ?rank .
    FILTER(?rank != wikibase:DeprecatedRank)
    BIND("writer" AS ?relation)
  }}
  UNION
  {{
    ?film p:P161 ?statement .
    ?statement ps:P161 ?target ; wikibase:rank ?rank .
    FILTER(?rank != wikibase:DeprecatedRank)
    OPTIONAL {{ ?statement pq:P1545 ?ordinal . }}
    BIND("actor" AS ?relation)
  }}
  UNION
  {{
    ?film p:P144 ?statement .
    ?statement ps:P144 ?target ; wikibase:rank ?rank .
    FILTER(?rank != wikibase:DeprecatedRank)
    BIND("based_on" AS ?relation)
  }}
  UNION
  {{
    ?film p:P179 ?statement .
    ?statement ps:P179 ?target ; wikibase:rank ?rank .
    FILTER(?rank != wikibase:DeprecatedRank)
    BIND("franchise" AS ?relation)
  }}
  UNION
  {{
    ?film p:P272 ?statement .
    ?statement ps:P272 ?target ; wikibase:rank ?rank .
    FILTER(?rank != wikibase:DeprecatedRank)
    BIND("production_company" AS ?relation)
  }}
  OPTIONAL {{ ?target wdt:P345 ?targetImdb . }}
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en,ru". }}
}}
ORDER BY ?film ?relation ?ordinal ?target ?statement
""".strip()

    def _throttle(self) -> None:
        minimum = float(config.WIKIMEDIA_MIN_INTERVAL_SECONDS)
        if self._last_request_at is not None:
            elapsed = self.monotonic_fn() - self._last_request_at
            if elapsed < minimum:
                self.sleep_fn(minimum - elapsed)
        self._last_request_at = self.monotonic_fn()

    def _request(self, query: str) -> dict[str, Any]:
        if self.transport is not None:
            payload = self.transport(query)
            if not isinstance(payload, dict):
                raise WikidataFutureEnrichmentError(
                    "transport должен вернуть JSON-объект"
                )
            return payload

        self._throttle()
        body = urlencode({"query": query, "format": "json"}).encode("utf-8")
        request = Request(
            self.endpoint,
            data=body,
            headers={
                "Accept": "application/sparql-results+json",
                "Content-Type": "application/x-www-form-urlencoded; charset=utf-8",
                "User-Agent": config.WIKIMEDIA_USER_AGENT,
            },
            method="POST",
        )
        try:
            with urlopen(
                request,
                timeout=int(config.WIKIMEDIA_TIMEOUT_SECONDS),
            ) as response:
                raw = response.read()
        except Exception as exc:
            raise WikidataFutureEnrichmentError(
                f"WDQS enrichment request failed: {exc}"
            ) from exc
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise WikidataFutureEnrichmentError(
                "WDQS enrichment вернул некорректный JSON"
            ) from exc
        if not isinstance(payload, dict):
            raise WikidataFutureEnrichmentError("WDQS JSON должен быть объектом")
        return payload

    @staticmethod
    def _bindings(raw: dict[str, Any]) -> list[dict[str, Any]]:
        results = raw.get("results")
        if not isinstance(results, dict):
            raise WikidataFutureEnrichmentError("WDQS JSON не содержит results")
        bindings = results.get("bindings")
        if not isinstance(bindings, list):
            raise WikidataFutureEnrichmentError(
                "WDQS JSON не содержит results.bindings[]"
            )
        if any(not isinstance(item, dict) for item in bindings):
            raise WikidataFutureEnrichmentError(
                "results.bindings[] должен содержать объекты"
            )
        return list(bindings)

    def _cache_raw(
        self,
        raw: dict[str, Any],
        *,
        retrieved_at: datetime,
        raw_fingerprint: str,
    ) -> Path:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        name = (
            f"wikidata-enrich-{retrieved_at.strftime('%Y%m%dT%H%M%SZ')}-"
            f"{raw_fingerprint[:16]}.json"
        )
        target = self.cache_dir / name
        if target.exists():
            existing = json.loads(target.read_text(encoding="utf-8"))
            if _fingerprint(existing) != raw_fingerprint:
                raise WikidataFutureEnrichmentError(
                    f"Raw cache collision: {target} содержит другой payload"
                )
            return target
        temporary = target.with_suffix(target.suffix + ".tmp")
        temporary.write_text(
            json.dumps(raw, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        os.replace(temporary, target)
        return target

    @staticmethod
    def registry_projects(
        future_db_path: str | Path,
        *,
        project_ids: list[str] | None = None,
        limit: int = 100,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        limit = max(1, min(int(limit), MAX_PROJECTS_PER_BATCH))
        requested = [
            str(item).strip()
            for item in (project_ids or [])
            if str(item).strip()
        ]
        warnings: list[dict[str, Any]] = []
        with FutureReleaseStore(future_db_path) as store:
            if requested:
                placeholders = ",".join("?" for _ in requested)
                rows = store.conn.execute(
                    f"""
                    SELECT project_id, wikidata_id, imdb_id, canonical_title
                    FROM future_release_projects
                    WHERE project_id IN ({placeholders})
                    ORDER BY project_id
                    """,
                    requested,
                ).fetchall()
                found_ids = {str(row[0]) for row in rows}
                for missing in sorted(set(requested) - found_ids):
                    warnings.append(
                        {"project_id": missing, "reason": "project_not_found"}
                    )
            else:
                rows = store.conn.execute(
                    """
                    SELECT project_id, wikidata_id, imdb_id, canonical_title
                    FROM future_release_projects
                    ORDER BY project_id
                    LIMIT ?
                    """,
                    [limit],
                ).fetchall()

        selected: list[dict[str, Any]] = []
        for row in rows[:limit]:
            qid = _qid(row[1])
            if qid is None:
                warnings.append(
                    {"project_id": str(row[0]), "reason": "wikidata_id_missing"}
                )
                continue
            selected.append(
                {
                    "project_id": str(row[0]),
                    "wikidata_id": qid,
                    "imdb_id": str(row[2]) if row[2] else None,
                    "canonical_title": str(row[3]),
                }
            )
        return selected, warnings

    @classmethod
    def normalize(
        cls,
        raw: dict[str, Any],
        *,
        projects: list[dict[str, Any]],
        retrieved_at: datetime,
    ) -> tuple[dict[str, list[dict[str, Any]]], list[dict[str, Any]]]:
        project_by_qid = {
            str(item["wikidata_id"]): dict(item)
            for item in projects
        }
        warnings: list[dict[str, Any]] = []
        logical_rows: dict[
            tuple[str, str, str],
            list[dict[str, Any]],
        ] = {}

        for index, binding in enumerate(cls._bindings(raw)):
            film_qid = _qid(_binding(binding, "film"))
            target_qid = _qid(_binding(binding, "target"))
            relation = str(_binding(binding, "relation") or "").strip()
            statement = _binding(binding, "statement")
            label = _binding(binding, "targetLabel")
            if (
                film_qid is None
                or target_qid is None
                or relation not in RELATIONS
                or not statement
                or not label
            ):
                warnings.append(
                    {"row": index, "reason": "required_binding_missing"}
                )
                continue
            project = project_by_qid.get(film_qid)
            if project is None:
                warnings.append(
                    {
                        "row": index,
                        "wikidata_id": film_qid,
                        "reason": "unknown_project_binding",
                    }
                )
                continue

            film_imdb = _binding(binding, "filmImdb")
            if (
                film_imdb
                and project.get("imdb_id")
                and str(project["imdb_id"]) != film_imdb
            ):
                warnings.append(
                    {
                        "row": index,
                        "project_id": project["project_id"],
                        "reason": "project_imdb_id_mismatch",
                        "registry_imdb_id": project["imdb_id"],
                        "wikidata_imdb_id": film_imdb,
                    }
                )

            item = {
                "row": index,
                "project_id": str(project["project_id"]),
                "film_qid": film_qid,
                "relation": relation,
                "statement": statement,
                "target_qid": target_qid,
                "target_label": label,
                "target_imdb": _binding(binding, "targetImdb"),
                "billing_order": _ordinal(_binding(binding, "ordinal")),
            }
            key = (item["project_id"], relation, target_qid)
            logical_rows.setdefault(key, []).append(item)

        sources: list[dict[str, Any]] = []
        people: dict[str, dict[str, Any]] = {}
        entities: dict[str, dict[str, Any]] = {}
        project_people: list[dict[str, Any]] = []
        project_entities: list[dict[str, Any]] = []
        stamp = retrieved_at.strftime("%Y%m%dT%H%M%SZ")

        for logical_key in sorted(logical_rows):
            candidates = logical_rows[logical_key]
            relation = str(logical_key[1])
            target_qid = str(logical_key[2])
            if relation == "actor":
                ordinals = sorted(
                    {
                        int(item["billing_order"])
                        for item in candidates
                        if item["billing_order"] is not None
                    }
                )
                if len(ordinals) > 1:
                    warnings.append(
                        {
                            "project_id": logical_key[0],
                            "target_qid": target_qid,
                            "reason": "actor_billing_order_conflict",
                            "values": ordinals,
                        }
                    )
                candidates.sort(
                    key=lambda item: (
                        item["billing_order"]
                        if item["billing_order"] is not None
                        else 2147483647,
                        item["statement"],
                    )
                )
            else:
                candidates.sort(key=lambda item: item["statement"])
            chosen = candidates[0]

            statement_key = _statement_key(chosen["statement"])
            source_id = (
                f"wikidata:{chosen['film_qid']}:enrich:{relation}:"
                f"{statement_key}:{stamp}"
            )
            sources.append(
                {
                    "source_id": source_id,
                    "provider": "Wikidata",
                    "url": f"https://www.wikidata.org/wiki/{chosen['film_qid']}",
                    "usage_basis": "public_record",
                    "retrieved_at": retrieved_at.isoformat(),
                }
            )

            if relation in {"director", "writer", "actor"}:
                raw_imdb = chosen.get("target_imdb")
                imdb_id = _valid_imdb_person(raw_imdb)
                if raw_imdb and imdb_id is None:
                    warnings.append(
                        {
                            "project_id": chosen["project_id"],
                            "target_qid": target_qid,
                            "reason": "invalid_person_imdb_id",
                            "value": raw_imdb,
                        }
                    )
                person_id = f"wikidata:{target_qid}"
                people[person_id] = {
                    "person_id": person_id,
                    "imdb_id": imdb_id,
                    "wikidata_id": target_qid,
                    "canonical_name": chosen["target_label"],
                }
                project_people.append(
                    {
                        "link_id": (
                            f"wikidata-link:{chosen['film_qid']}:{relation}:"
                            f"{target_qid}:{statement_key}:{stamp}"
                        ),
                        "project_id": chosen["project_id"],
                        "person_id": person_id,
                        "role": relation,
                        "billing_order": (
                            chosen["billing_order"] if relation == "actor" else None
                        ),
                        "known_at": retrieved_at.isoformat(),
                        "source_id": source_id,
                        "confidence": 0.9,
                    }
                )
                continue

            if relation == "based_on":
                entity_kind = "source_work"
                relation_type = "based_on"
                confidence = 0.85
            elif relation == "franchise":
                entity_kind = "franchise"
                relation_type = "part_of_franchise"
                confidence = 0.8
            else:
                entity_kind = "production_company"
                relation_type = "produced_by"
                confidence = 0.8
            entity_id = f"wikidata:{entity_kind}:{target_qid}"
            entities[entity_id] = {
                "entity_id": entity_id,
                "kind": entity_kind,
                "canonical_name": chosen["target_label"],
                "external_id": target_qid,
            }
            project_entities.append(
                {
                    "link_id": (
                        f"wikidata-entity-link:{chosen['film_qid']}:{relation}:"
                        f"{target_qid}:{statement_key}:{stamp}"
                    ),
                    "project_id": chosen["project_id"],
                    "entity_id": entity_id,
                    "relation_type": relation_type,
                    "known_at": retrieved_at.isoformat(),
                    "source_id": source_id,
                    "confidence": confidence,
                }
            )

        bundle = {
            "sources": sorted(sources, key=lambda item: item["source_id"]),
            "projects": [],
            "people": [people[key] for key in sorted(people)],
            "entities": [entities[key] for key in sorted(entities)],
            "aliases": [],
            "release_windows": [],
            "statuses": [],
            "project_people": sorted(
                project_people,
                key=lambda item: (item["project_id"], item["role"], item["person_id"]),
            ),
            "project_entities": sorted(
                project_entities,
                key=lambda item: (
                    item["project_id"],
                    item["relation_type"],
                    item["entity_id"],
                ),
            ),
        }
        return bundle, warnings

    def collect_from_registry(
        self,
        future_db_path: str | Path,
        *,
        project_ids: list[str] | None = None,
        limit: int = 100,
        retrieved_at: Any | None = None,
    ) -> dict[str, Any]:
        observed = _dt(
            retrieved_at or datetime.now(timezone.utc),
            field_name="retrieved_at",
        )
        projects, selection_warnings = self.registry_projects(
            future_db_path,
            project_ids=project_ids,
            limit=limit,
        )
        if not projects:
            raise WikidataFutureEnrichmentError(
                "В P9 registry нет проектов с Wikidata QID для enrichment"
            )
        qids = [str(item["wikidata_id"]) for item in projects]
        query = self.build_query(qids)
        raw = self._request(query)
        raw_fingerprint = _fingerprint(raw)
        cache_path = self._cache_raw(
            raw,
            retrieved_at=observed,
            raw_fingerprint=raw_fingerprint,
        )
        bundle, normalization_warnings = self.normalize(
            raw,
            projects=projects,
            retrieved_at=observed,
        )
        project_fingerprint = _fingerprint(
            [
                {
                    "project_id": item["project_id"],
                    "wikidata_id": item["wikidata_id"],
                }
                for item in projects
            ]
        )
        source_fingerprint = _fingerprint(
            {
                "collector": "wikidata-wdqs-enrichment",
                "version": ENRICHER_VERSION,
                "retrieved_at": observed.isoformat(),
                "project_fingerprint_sha256": project_fingerprint,
                "raw_fingerprint_sha256": raw_fingerprint,
            }
        )
        batch = {
            "version": 1,
            "batch_id": (
                f"wikidata-enrich-{observed.strftime('%Y%m%dT%H%M%SZ')}-"
                f"{source_fingerprint[:12]}"
            ),
            "provider": "wikidata-wdqs-enrichment",
            "retrieved_at": observed.isoformat(),
            "cursor": f"projects:{len(projects)}:{project_fingerprint[:16]}",
            "source_fingerprint_sha256": source_fingerprint,
            "bundle": bundle,
        }
        warnings = selection_warnings + normalization_warnings
        return {
            "collector": "wikidata-wdqs-enrichment",
            "collector_version": ENRICHER_VERSION,
            "project_count": len(projects),
            "project_ids": [item["project_id"] for item in projects],
            "raw_row_count": len(self._bindings(raw)),
            "raw_fingerprint_sha256": raw_fingerprint,
            "raw_cache_path": str(cache_path),
            "warning_count": len(warnings),
            "warnings": warnings,
            "people_count": len(bundle["people"]),
            "entity_count": len(bundle["entities"]),
            "project_people_count": len(bundle["project_people"]),
            "project_entity_count": len(bundle["project_entities"]),
            "network_required_for_inference": False,
            "batch": batch,
        }
