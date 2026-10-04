from __future__ import annotations

import calendar
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


WDQS_ENDPOINT = "https://query.wikidata.org/sparql"
COLLECTOR_VERSION = 3


class WikidataFutureReleaseError(RuntimeError):
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
        except ValueError as exc:
            raise WikidataFutureReleaseError(
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


def _qid(uri_or_qid: str | None) -> str | None:
    text = str(uri_or_qid or "").strip()
    if not text:
        return None
    value = text.rsplit("/", 1)[-1]
    if value.startswith("Q") and value[1:].isdigit():
        return value
    return None


def _safe_statement_key(value: str | None) -> str:
    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()[:16]


def _release_window(value: str, precision: int) -> tuple[datetime, datetime, str] | None:
    point = _dt(value, field_name="releaseDate")
    if precision >= 11:
        exact = point.replace(microsecond=0)
        return exact, exact, "exact"
    if precision == 10:
        start = datetime(point.year, point.month, 1, tzinfo=timezone.utc)
        last_day = calendar.monthrange(point.year, point.month)[1]
        end = datetime(
            point.year,
            point.month,
            last_day,
            23,
            59,
            59,
            tzinfo=timezone.utc,
        )
        return start, end, "month"
    if precision == 9:
        start = datetime(point.year, 1, 1, tzinfo=timezone.utc)
        end = datetime(point.year, 12, 31, 23, 59, 59, tzinfo=timezone.utc)
        return start, end, "year"
    return None


def _territory(binding: dict[str, Any]) -> tuple[str, str | None]:
    """Возвращает безопасный territory key и Wikidata QID qualifier-а.

    Отсутствие P291 принципиально не трактуется как worldwide. Такой statement
    остаётся доступным как ``unspecified``, но не может автоматически разблокировать
    default worldwide prediction.
    """

    raw = _binding(binding, "territory")
    if raw is None:
        return "unspecified", None
    qid = _qid(raw)
    if qid is None:
        return "unspecified", None
    return f"wikidata:{qid}", qid


class WikidataFutureReleaseCollector:
    """WDQS discovery collector -> P9 fingerprinted batch.

    Collector выполняет сеть только на стадии discovery. Результат сначала
    сохраняется в raw cache, затем нормализуется в batch contract. Inference этот
    класс не вызывает и читает только локальный P9 registry.

    Release date читается на уровне P577 statement. Qualifier P291 сохраняется как
    отдельная территория; отсутствие qualifier не подменяется ``worldwide``.
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
            raise WikidataFutureReleaseError("WDQS endpoint должен быть HTTP(S) URL")
        self.endpoint = endpoint
        self.cache_dir = Path(
            cache_dir
            or (Path(config.ABSPATH) / "data" / "future_releases" / "wikidata")
        )
        self.transport = transport
        self.sleep_fn = sleep_fn
        self.monotonic_fn = monotonic_fn
        self._last_request_at: float | None = None

    @staticmethod
    def build_query(from_at: datetime, to_at: datetime, *, limit: int) -> str:
        if from_at >= to_at:
            raise WikidataFutureReleaseError("from_at должен быть раньше to_at")
        limit = max(1, min(int(limit), 5000))
        from_iso = from_at.strftime("%Y-%m-%dT%H:%M:%SZ")
        to_iso = to_at.strftime("%Y-%m-%dT%H:%M:%SZ")
        return f"""
PREFIX wd: <http://www.wikidata.org/entity/>
PREFIX wdt: <http://www.wikidata.org/prop/direct/>
PREFIX p: <http://www.wikidata.org/prop/>
PREFIX pq: <http://www.wikidata.org/prop/qualifier/>
PREFIX psv: <http://www.wikidata.org/prop/statement/value/>
PREFIX wikibase: <http://wikiba.se/ontology#>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
PREFIX xsd: <http://www.w3.org/2001/XMLSchema#>

SELECT ?film ?filmLabelEn ?filmLabelRu ?imdb ?releaseStatement
       ?releaseDate ?precision ?territory WHERE {{
  ?film wdt:P31/wdt:P279* wd:Q11424 ;
        p:P577 ?releaseStatement .
  ?releaseStatement wikibase:rank ?rank ;
                    psv:P577 ?releaseValue .
  FILTER(?rank != wikibase:DeprecatedRank)
  ?releaseValue wikibase:timeValue ?releaseDate ;
                wikibase:timePrecision ?precision .
  FILTER(?releaseDate >= "{from_iso}"^^xsd:dateTime)
  FILTER(?releaseDate < "{to_iso}"^^xsd:dateTime)
  OPTIONAL {{ ?releaseStatement pq:P291 ?territory . }}
  OPTIONAL {{ ?film wdt:P345 ?imdb . }}
  OPTIONAL {{
    ?film rdfs:label ?filmLabelEn .
    FILTER(LANG(?filmLabelEn) = "en")
  }}
  OPTIONAL {{
    ?film rdfs:label ?filmLabelRu .
    FILTER(LANG(?filmLabelRu) = "ru")
  }}
}}
ORDER BY ?releaseDate ?film ?releaseStatement ?territory
LIMIT {limit}
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
                raise WikidataFutureReleaseError("transport должен вернуть JSON-объект")
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
            with urlopen(request, timeout=int(config.WIKIMEDIA_TIMEOUT_SECONDS)) as response:
                raw = response.read()
        except Exception as exc:
            raise WikidataFutureReleaseError(f"WDQS request failed: {exc}") from exc
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise WikidataFutureReleaseError("WDQS вернул некорректный JSON") from exc
        if not isinstance(payload, dict):
            raise WikidataFutureReleaseError("WDQS JSON должен быть объектом")
        return payload

    def _cache_raw(
        self,
        raw: dict[str, Any],
        *,
        retrieved_at: datetime,
        raw_fingerprint: str,
    ) -> Path:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        name = (
            f"wikidata-{retrieved_at.strftime('%Y%m%dT%H%M%SZ')}-"
            f"{raw_fingerprint[:16]}.json"
        )
        target = self.cache_dir / name
        if target.exists():
            existing = json.loads(target.read_text(encoding="utf-8"))
            if _fingerprint(existing) != raw_fingerprint:
                raise WikidataFutureReleaseError(
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
    def _bindings(raw: dict[str, Any]) -> list[dict[str, Any]]:
        results = raw.get("results")
        if not isinstance(results, dict):
            raise WikidataFutureReleaseError("WDQS JSON не содержит results")
        bindings = results.get("bindings")
        if not isinstance(bindings, list):
            raise WikidataFutureReleaseError("WDQS JSON не содержит results.bindings[]")
        if any(not isinstance(item, dict) for item in bindings):
            raise WikidataFutureReleaseError("results.bindings[] должен содержать объекты")
        return list(bindings)

    @classmethod
    def normalize(
        cls,
        raw: dict[str, Any],
        *,
        retrieved_at: datetime,
    ) -> tuple[dict[str, list[dict[str, Any]]], list[dict[str, Any]]]:
        projects: dict[str, dict[str, Any]] = {}
        sources: dict[str, dict[str, Any]] = {}
        aliases: dict[str, dict[str, Any]] = {}
        releases: dict[str, dict[str, Any]] = {}
        warnings: list[dict[str, Any]] = []
        retrieval_key = retrieved_at.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

        for index, binding in enumerate(cls._bindings(raw)):
            qid = _qid(_binding(binding, "film"))
            title = (
                _binding(binding, "filmLabelEn")
                or _binding(binding, "filmLabelRu")
                or _binding(binding, "filmLabel")
            )
            release_value = _binding(binding, "releaseDate")
            precision_raw = _binding(binding, "precision")
            statement = _binding(binding, "releaseStatement")
            if not qid or not title or not release_value or precision_raw is None:
                warnings.append({"row": index, "reason": "required_binding_missing"})
                continue
            try:
                precision = int(float(precision_raw))
            except ValueError:
                warnings.append({"row": index, "reason": "invalid_time_precision"})
                continue
            window = _release_window(release_value, precision)
            if window is None:
                warnings.append(
                    {
                        "row": index,
                        "qid": qid,
                        "reason": "unsupported_time_precision",
                        "precision": precision,
                    }
                )
                continue
            start, end, precision_name = window
            imdb = _binding(binding, "imdb")
            if imdb and (not imdb.startswith("tt") or not imdb[2:].isdigit()):
                warnings.append({"row": index, "qid": qid, "reason": "invalid_imdb_id"})
                imdb = None

            territory, territory_qid = _territory(binding)
            if territory == "unspecified":
                warnings.append(
                    {
                        "row": index,
                        "qid": qid,
                        "reason": "release_territory_unspecified",
                    }
                )

            project_id = f"wikidata:{qid}"
            projects[project_id] = {
                "project_id": project_id,
                "imdb_id": imdb,
                "wikidata_id": qid,
                "canonical_title": title,
            }
            statement_key = _safe_statement_key(statement or f"{qid}:{release_value}:{precision}")

            # Source — конкретный retrieval snapshot, а не вечный ID statement.
            source_id = f"wikidata:{qid}:release:{statement_key}:{retrieval_key}"
            sources[source_id] = {
                "source_id": source_id,
                "provider": "Wikidata",
                "url": f"https://www.wikidata.org/wiki/{qid}",
                "usage_basis": "public_record",
                "retrieved_at": retrieved_at.astimezone(timezone.utc).isoformat(),
            }

            title_key = hashlib.sha256(title.casefold().encode("utf-8")).hexdigest()[:12]
            alias_id = f"wikidata-alias:{qid}:{title_key}:{retrieval_key}"
            aliases[alias_id] = {
                "alias_id": alias_id,
                "project_id": project_id,
                "alias": title,
                "known_at": retrieved_at.astimezone(timezone.utc).isoformat(),
                "source_id": source_id,
                "confidence": 0.9,
            }

            territory_key = territory_qid or "unspecified"
            observation_id = (
                f"wikidata-release:{qid}:{statement_key}:{territory_key}:{retrieval_key}"
            )
            releases[observation_id] = {
                "observation_id": observation_id,
                "project_id": project_id,
                "territory": territory,
                "release_start_at": start.astimezone(timezone.utc).isoformat(),
                "release_end_at": end.astimezone(timezone.utc).isoformat(),
                "precision": precision_name,
                "known_at": retrieved_at.astimezone(timezone.utc).isoformat(),
                "source_id": source_id,
                "confidence": 0.85 if territory_qid else 0.7,
            }

        bundle = {
            "sources": [sources[key] for key in sorted(sources)],
            "projects": [projects[key] for key in sorted(projects)],
            "people": [],
            "entities": [],
            "aliases": [aliases[key] for key in sorted(aliases)],
            "release_windows": [releases[key] for key in sorted(releases)],
            "statuses": [],
            "project_people": [],
            "project_entities": [],
        }
        return bundle, warnings

    def collect(
        self,
        from_at: Any,
        to_at: Any,
        *,
        limit: int = 1000,
        retrieved_at: Any | None = None,
    ) -> dict[str, Any]:
        start = _dt(from_at, field_name="from_at")
        end = _dt(to_at, field_name="to_at")
        observed = _dt(
            retrieved_at or datetime.now(timezone.utc),
            field_name="retrieved_at",
        )
        if start >= end:
            raise WikidataFutureReleaseError("from_at должен быть раньше to_at")
        if end <= observed:
            raise WikidataFutureReleaseError(
                "to_at должен указывать хотя бы частично на будущее относительно retrieved_at"
            )
        query = self.build_query(start, end, limit=limit)
        raw = self._request(query)
        raw_fingerprint = _fingerprint(raw)
        cache_path = self._cache_raw(
            raw,
            retrieved_at=observed,
            raw_fingerprint=raw_fingerprint,
        )
        bundle, warnings = self.normalize(raw, retrieved_at=observed)
        source_fingerprint = _fingerprint(
            {
                "collector": "wikidata-wdqs",
                "version": COLLECTOR_VERSION,
                "retrieved_at": observed.isoformat(),
                "raw_fingerprint_sha256": raw_fingerprint,
            }
        )
        batch = {
            "version": 1,
            "batch_id": (
                f"wikidata-{observed.strftime('%Y%m%dT%H%M%SZ')}-"
                f"{source_fingerprint[:12]}"
            ),
            "provider": "wikidata-wdqs",
            "retrieved_at": observed.isoformat(),
            "cursor": f"{start.isoformat()}..{end.isoformat()}",
            "source_fingerprint_sha256": source_fingerprint,
            "bundle": bundle,
        }
        return {
            "collector": "wikidata-wdqs",
            "collector_version": COLLECTOR_VERSION,
            "query": {
                "from_at": start.isoformat(),
                "to_at": end.isoformat(),
                "limit": max(1, min(int(limit), 5000)),
            },
            "raw_fingerprint_sha256": raw_fingerprint,
            "raw_cache_path": str(cache_path),
            "warning_count": len(warnings),
            "warnings": warnings,
            "row_count": len(self._bindings(raw)),
            "project_count": len(bundle["projects"]),
            "release_observation_count": len(bundle["release_windows"]),
            "batch": batch,
        }
