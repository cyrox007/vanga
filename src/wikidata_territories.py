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
from src.future_releases import FutureReleaseStore
from src.future_territories import FutureTerritoryError, FutureTerritoryStore, _dt


WDQS_ENDPOINT = "https://query.wikidata.org/sparql"
COLLECTOR_VERSION = 1


class WikidataTerritoryError(FutureTerritoryError):
    pass


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


def _fingerprint(payload: Any) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class WikidataTerritoryCollector:
    """Получает ISO 3166-1 коды для Wikidata territory QID, уже встреченных в P9."""

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
            raise WikidataTerritoryError("WDQS endpoint должен быть HTTP(S) URL")
        self.endpoint = endpoint
        self.cache_dir = Path(
            cache_dir
            or Path(config.ABSPATH) / "data" / "future_releases" / "wikidata" / "territories"
        )
        self.transport = transport
        self.sleep_fn = sleep_fn
        self.monotonic_fn = monotonic_fn
        self._last_request_at: float | None = None

    @staticmethod
    def registry_qids(
        future_db_path: str | Path,
        *,
        limit: int = 500,
    ) -> list[str]:
        with FutureReleaseStore(future_db_path) as store:
            rows = store.conn.execute(
                """
                SELECT DISTINCT territory
                FROM future_release_windows
                WHERE territory LIKE 'wikidata:q%'
                ORDER BY territory
                LIMIT ?
                """,
                [max(1, min(int(limit), 5000))],
            ).fetchall()
        result: list[str] = []
        for row in rows:
            raw = str(row[0]).casefold()
            suffix = raw.split(":", 1)[1] if ":" in raw else ""
            if suffix.startswith("q") and suffix[1:].isdigit():
                result.append(f"Q{suffix[1:]}")
        return result

    @staticmethod
    def build_query(qids: list[str]) -> str:
        unique = sorted(set(str(item).strip() for item in qids if str(item).strip()))
        if not unique:
            raise WikidataTerritoryError("Нужен хотя бы один territory QID")
        invalid = [item for item in unique if not (item.startswith("Q") and item[1:].isdigit())]
        if invalid:
            raise WikidataTerritoryError("Некорректные territory QID: " + ", ".join(invalid[:10]))
        values = " ".join(f"wd:{item}" for item in unique)
        return f"""
PREFIX wd: <http://www.wikidata.org/entity/>
PREFIX wdt: <http://www.wikidata.org/prop/direct/>

SELECT ?territory ?isoCode WHERE {{
  VALUES ?territory {{ {values} }}
  OPTIONAL {{ ?territory wdt:P297 ?isoCode . }}
}}
ORDER BY ?territory ?isoCode
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
                raise WikidataTerritoryError("transport должен вернуть JSON-объект")
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
            raise WikidataTerritoryError(f"WDQS territory request failed: {exc}") from exc
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise WikidataTerritoryError("WDQS territory вернул некорректный JSON") from exc
        if not isinstance(payload, dict):
            raise WikidataTerritoryError("WDQS territory JSON должен быть объектом")
        return payload

    @staticmethod
    def _bindings(payload: dict[str, Any]) -> list[dict[str, Any]]:
        results = payload.get("results")
        if not isinstance(results, dict) or not isinstance(results.get("bindings"), list):
            raise WikidataTerritoryError("WDQS territory JSON не содержит results.bindings[]")
        return [item for item in results["bindings"] if isinstance(item, dict)]

    @classmethod
    def normalize(
        cls,
        payload: dict[str, Any],
        *,
        retrieved_at: datetime,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        grouped: dict[str, set[str]] = {}
        warnings: list[dict[str, Any]] = []
        for index, binding in enumerate(cls._bindings(payload)):
            qid = _qid(_binding(binding, "territory"))
            if qid is None:
                warnings.append({"row": index, "reason": "territory_qid_missing"})
                continue
            iso = str(_binding(binding, "isoCode") or "").strip().upper()
            if not iso:
                warnings.append({"territory": qid, "reason": "iso_code_missing"})
                continue
            if len(iso) != 2 or not iso.isalpha():
                warnings.append({"territory": qid, "reason": "invalid_iso_code", "value": iso})
                continue
            grouped.setdefault(qid, set()).add(iso)

        records: list[dict[str, Any]] = []
        stamp = retrieved_at.strftime("%Y%m%dT%H%M%SZ")
        for qid in sorted(grouped):
            values = sorted(grouped[qid])
            if len(values) != 1:
                warnings.append(
                    {"territory": qid, "reason": "iso_code_conflict_within_wikidata", "values": values}
                )
                continue
            iso = values[0]
            source_id = f"wikidata:{qid}:iso3166:{stamp}"
            records.append(
                {
                    "source": {
                        "source_id": source_id,
                        "provider": "Wikidata",
                        "url": f"https://www.wikidata.org/wiki/{qid}",
                        "usage_basis": "public_record",
                        "retrieved_at": retrieved_at.isoformat(),
                    },
                    "mapping": {
                        "mapping_id": f"wikidata-territory:{qid}:{iso}:{stamp}",
                        "source_territory": f"wikidata:{qid}",
                        "canonical_territory": f"iso3166:{iso}",
                        "known_at": retrieved_at.isoformat(),
                        "source_id": source_id,
                        "confidence": 0.95,
                    },
                }
            )
        return records, warnings

    def collect(
        self,
        future_db_path: str | Path,
        *,
        qids: list[str] | None = None,
        limit: int = 500,
        retrieved_at: Any | None = None,
    ) -> dict[str, Any]:
        observed = _dt(retrieved_at or datetime.now(timezone.utc), field_name="retrieved_at")
        selected = sorted(set(qids or self.registry_qids(future_db_path, limit=limit)))
        if not selected:
            raise WikidataTerritoryError("В P9 registry нет Wikidata territory QID для канонизации")
        query = self.build_query(selected)
        raw = self._request(query)
        records, warnings = self.normalize(raw, retrieved_at=observed)
        fingerprint = _fingerprint({"qids": selected, "raw": raw, "retrieved_at": observed.isoformat()})
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        cache_path = self.cache_dir / (
            f"territories-{observed.strftime('%Y%m%dT%H%M%SZ')}-{fingerprint[:16]}.json"
        )
        if not cache_path.exists():
            temporary = cache_path.with_suffix(".json.tmp")
            temporary.write_text(json.dumps(raw, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
            os.replace(temporary, cache_path)
        return {
            "collector": "wikidata-territory-iso",
            "collector_version": COLLECTOR_VERSION,
            "retrieved_at": observed.isoformat(),
            "territory_count": len(selected),
            "mapping_count": len(records),
            "warning_count": len(warnings),
            "warnings": warnings,
            "raw_cache_path": str(cache_path),
            "raw_fingerprint_sha256": fingerprint,
            "records": records,
        }

    @staticmethod
    def apply(future_db_path: str | Path, result: dict[str, Any]) -> dict[str, Any]:
        records = result.get("records")
        if not isinstance(records, list):
            raise WikidataTerritoryError("collector result не содержит records[]")
        with FutureReleaseStore(future_db_path) as registry:
            territories = FutureTerritoryStore(registry)
            registry.conn.execute("BEGIN")
            try:
                for record in records:
                    if not isinstance(record, dict):
                        raise WikidataTerritoryError("records[] должен содержать объекты")
                    source = record.get("source")
                    mapping = record.get("mapping")
                    if not isinstance(source, dict) or not isinstance(mapping, dict):
                        raise WikidataTerritoryError("record должен содержать source и mapping")
                    registry.upsert_source(source)
                    territories.add_mapping(mapping)
                registry.conn.execute("COMMIT")
            except Exception:
                registry.conn.execute("ROLLBACK")
                raise
        return {"applied_mapping_count": len(records)}
