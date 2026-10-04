from __future__ import annotations

import hashlib
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from settings import config
from src.wikidata_future_enrichment import (
    MAX_PROJECTS_PER_BATCH,
    WDQS_ENDPOINT,
    WikidataFutureEnricher,
    WikidataFutureEnrichmentError,
    _binding,
    _dt,
    _fingerprint,
    _qid,
)


FACTS_COLLECTOR_VERSION = 1


class WikidataFutureFactsError(WikidataFutureEnrichmentError):
    pass


def _observation_id(project_qid: str, fact_type: str, retrieved_at: datetime, value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
    return (
        f"wikidata-fact:{project_qid}:{fact_type}:"
        f"{retrieved_at.strftime('%Y%m%dT%H%M%SZ')}:{digest}"
    )[:180]


def _source_snapshot_id(
    project_qid: str,
    retrieved_at: datetime,
    *,
    runtime_values: list[int],
    genre_values: list[str],
) -> str:
    digest = _fingerprint(
        {
            "runtime": runtime_values,
            "genres": genre_values,
        }
    )[:12]
    return (
        f"wikidata:{project_qid}:facts:"
        f"{retrieved_at.strftime('%Y%m%dT%H%M%SZ')}:{digest}"
    )[:160]


class WikidataFutureFactsCollector:
    """Собирает temporal-safe runtime и genres для проектов P9 registry.

    Collector отделён от inference и отдаёт обычный fingerprinted P9 batch.
    Запрос использует только SPARQL 1.1 конструкции: без
    ``SERVICE wikibase:label`` и других Blazegraph-only расширений. Endpoint
    задаётся явно, поэтому переход с WDQS v1 на v2 не требует менять контракт.

    Каждый sync создаёт immutable ``source_id`` snapshot, а temporal facts получают
    стабильный ``source_stream_id`` вида ``wikidata:<QID>:facts``. Поэтому история
    источника сохраняется без перезаписи provenance, а as-of resolver всё равно
    понимает последовательные snapshots как один логический поток.
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
        endpoint = str(endpoint or "").strip()
        if not endpoint.startswith(("https://", "http://")):
            raise WikidataFutureFactsError("WDQS endpoint должен быть HTTP(S) URL")
        self.endpoint = endpoint
        self.cache_dir = Path(
            cache_dir
            or (
                Path(config.ABSPATH)
                / "data"
                / "future_releases"
                / "wikidata"
                / "facts"
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
            raise WikidataFutureFactsError("Нужен хотя бы один Wikidata QID")
        if len(unique) > MAX_PROJECTS_PER_BATCH:
            raise WikidataFutureFactsError(
                f"За один facts batch поддерживается не больше {MAX_PROJECTS_PER_BATCH} проектов"
            )
        invalid = [item for item in unique if _qid(item) != item]
        if invalid:
            raise WikidataFutureFactsError(
                "Некорректные Wikidata QID: " + ", ".join(invalid[:10])
            )
        values = " ".join(f"wd:{item}" for item in unique)
        return f"""
PREFIX wd: <http://www.wikidata.org/entity/>
PREFIX wdt: <http://www.wikidata.org/prop/direct/>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>

SELECT ?film ?runtime ?genre ?genreLabelEn ?genreLabelRu WHERE {{
  VALUES ?film {{ {values} }}
  OPTIONAL {{ ?film wdt:P2047 ?runtime . }}
  OPTIONAL {{
    ?film wdt:P136 ?genre .
    OPTIONAL {{
      ?genre rdfs:label ?genreLabelEn .
      FILTER(LANG(?genreLabelEn) = "en")
    }}
    OPTIONAL {{
      ?genre rdfs:label ?genreLabelRu .
      FILTER(LANG(?genreLabelRu) = "ru")
    }}
  }}
}}
ORDER BY ?film ?genre
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
                raise WikidataFutureFactsError("transport должен вернуть JSON-объект")
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
            raise WikidataFutureFactsError(
                f"WDQS facts request failed: {exc}"
            ) from exc
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise WikidataFutureFactsError("WDQS facts вернул некорректный JSON") from exc
        if not isinstance(payload, dict):
            raise WikidataFutureFactsError("WDQS JSON должен быть объектом")
        return payload

    @staticmethod
    def _bindings(raw: dict[str, Any]) -> list[dict[str, Any]]:
        return WikidataFutureEnricher._bindings(raw)

    def _cache_raw(
        self,
        raw: dict[str, Any],
        *,
        retrieved_at: datetime,
        raw_fingerprint: str,
    ) -> Path:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        name = (
            f"wikidata-facts-{retrieved_at.strftime('%Y%m%dT%H%M%SZ')}-"
            f"{raw_fingerprint[:16]}.json"
        )
        target = self.cache_dir / name
        if target.exists():
            existing = json.loads(target.read_text(encoding="utf-8"))
            if _fingerprint(existing) != raw_fingerprint:
                raise WikidataFutureFactsError(
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
        runtimes: dict[str, set[int]] = {}
        genres: dict[str, set[str]] = {}
        warnings: list[dict[str, Any]] = []

        for index, binding in enumerate(cls._bindings(raw)):
            film_qid = _qid(_binding(binding, "film"))
            if film_qid is None or film_qid not in project_by_qid:
                warnings.append(
                    {
                        "row": index,
                        "wikidata_id": film_qid,
                        "reason": "unknown_project_binding",
                    }
                )
                continue

            runtime_raw = _binding(binding, "runtime")
            if runtime_raw:
                try:
                    runtime = int(round(float(runtime_raw)))
                except (TypeError, ValueError):
                    warnings.append(
                        {
                            "row": index,
                            "project_id": project_by_qid[film_qid]["project_id"],
                            "reason": "invalid_runtime",
                            "value": runtime_raw,
                        }
                    )
                else:
                    if 1 <= runtime <= 1000:
                        runtimes.setdefault(film_qid, set()).add(runtime)
                    else:
                        warnings.append(
                            {
                                "row": index,
                                "project_id": project_by_qid[film_qid]["project_id"],
                                "reason": "runtime_out_of_range",
                                "value": runtime,
                            }
                        )

            genre_qid = _qid(_binding(binding, "genre"))
            if genre_qid:
                label = str(
                    _binding(binding, "genreLabelEn")
                    or _binding(binding, "genreLabelRu")
                    or ""
                ).strip()
                if label:
                    genres.setdefault(film_qid, set()).add(label)
                else:
                    warnings.append(
                        {
                            "row": index,
                            "project_id": project_by_qid[film_qid]["project_id"],
                            "genre_qid": genre_qid,
                            "reason": "genre_label_missing",
                        }
                    )

        sources: list[dict[str, Any]] = []
        facts: list[dict[str, Any]] = []
        observed_iso = retrieved_at.isoformat()
        all_qids = sorted(set(runtimes) | set(genres))
        for film_qid in all_qids:
            project = project_by_qid[film_qid]
            runtime_values = sorted(runtimes.get(film_qid, set()))
            genre_values = sorted(genres.get(film_qid, set()), key=str.casefold)
            source_stream_id = f"wikidata:{film_qid}:facts"
            source_id = _source_snapshot_id(
                film_qid,
                retrieved_at,
                runtime_values=runtime_values,
                genre_values=genre_values,
            )
            sources.append(
                {
                    "source_id": source_id,
                    "provider": "Wikidata",
                    "url": f"https://www.wikidata.org/wiki/{film_qid}",
                    "usage_basis": "public_record",
                    "retrieved_at": observed_iso,
                }
            )

            if len(runtime_values) == 1:
                runtime = runtime_values[0]
                facts.append(
                    {
                        "observation_id": _observation_id(
                            film_qid,
                            "runtime",
                            retrieved_at,
                            runtime,
                        ),
                        "project_id": str(project["project_id"]),
                        "fact_type": "runtime_minutes",
                        "value": runtime,
                        "known_at": observed_iso,
                        "source_id": source_id,
                        "source_stream_id": source_stream_id,
                        "confidence": 0.9,
                    }
                )
            elif len(runtime_values) > 1:
                warnings.append(
                    {
                        "project_id": project["project_id"],
                        "reason": "runtime_conflict_within_wikidata",
                        "values": runtime_values,
                    }
                )

            if genre_values:
                facts.append(
                    {
                        "observation_id": _observation_id(
                            film_qid,
                            "genres",
                            retrieved_at,
                            genre_values,
                        ),
                        "project_id": str(project["project_id"]),
                        "fact_type": "genres",
                        "value": genre_values,
                        "known_at": observed_iso,
                        "source_id": source_id,
                        "source_stream_id": source_stream_id,
                        "confidence": 0.85,
                    }
                )

        bundle = {
            "sources": sources,
            "projects": [],
            "people": [],
            "entities": [],
            "aliases": [],
            "release_windows": [],
            "statuses": [],
            "project_people": [],
            "project_entities": [],
            "temporal_facts": sorted(
                facts,
                key=lambda item: (
                    item["project_id"],
                    item["fact_type"],
                    item["observation_id"],
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
        projects, selection_warnings = WikidataFutureEnricher.registry_projects(
            future_db_path,
            project_ids=project_ids,
            limit=limit,
        )
        if not projects:
            raise WikidataFutureFactsError(
                "В P9 registry нет проектов с Wikidata QID для facts enrichment"
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
                "collector": "wikidata-wdqs-facts",
                "version": FACTS_COLLECTOR_VERSION,
                "retrieved_at": observed.isoformat(),
                "project_fingerprint_sha256": project_fingerprint,
                "raw_fingerprint_sha256": raw_fingerprint,
            }
        )
        batch = {
            "version": 1,
            "batch_id": (
                f"wikidata-facts-{observed.strftime('%Y%m%dT%H%M%SZ')}-"
                f"{source_fingerprint[:12]}"
            ),
            "provider": "wikidata-wdqs-facts",
            "retrieved_at": observed.isoformat(),
            "cursor": f"projects:{len(projects)}:{project_fingerprint[:16]}",
            "source_fingerprint_sha256": source_fingerprint,
            "bundle": bundle,
        }
        warnings = selection_warnings + normalization_warnings
        return {
            "collector": "wikidata-wdqs-facts",
            "collector_version": FACTS_COLLECTOR_VERSION,
            "endpoint": self.endpoint,
            "project_count": len(projects),
            "project_ids": [item["project_id"] for item in projects],
            "raw_row_count": len(self._bindings(raw)),
            "raw_fingerprint_sha256": raw_fingerprint,
            "raw_cache_path": str(cache_path),
            "warning_count": len(warnings),
            "warnings": warnings,
            "temporal_fact_count": len(bundle["temporal_facts"]),
            "network_required_for_inference": False,
            "batch": batch,
        }
