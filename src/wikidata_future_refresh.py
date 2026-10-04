from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.future_release_import import BUNDLE_ARRAYS
from src.wikidata_future_enrichment import WikidataFutureEnricher
from src.wikidata_future_facts import WikidataFutureFactsCollector


REFRESH_VERSION = 1


def _fingerprint(payload: Any) -> str:
    raw = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _observed(value: Any | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if isinstance(value, datetime):
        result = value
    else:
        result = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    if result.tzinfo is None:
        result = result.replace(tzinfo=timezone.utc)
    return result.astimezone(timezone.utc)


class WikidataFutureRefresh:
    """Объединяет P9 Wikidata enrichment и temporal facts в один batch.

    Оба collector-а получают один ``retrieved_at``. Их сырые ответы кешируются
    независимо, но в registry импортируется единый bundle. Поэтому обновление
    команды/source context и runtime/genres атомарно на уровне P9 registry.
    """

    def __init__(
        self,
        *,
        enricher: WikidataFutureEnricher | None = None,
        facts: WikidataFutureFactsCollector | None = None,
    ) -> None:
        self.enricher = enricher or WikidataFutureEnricher()
        self.facts = facts or WikidataFutureFactsCollector()

    @staticmethod
    def _merge_bundles(*bundles: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
        merged: dict[str, list[dict[str, Any]]] = {
            key: [] for key in BUNDLE_ARRAYS
        }
        for bundle in bundles:
            for key in BUNDLE_ARRAYS:
                raw = bundle.get(key, [])
                if not isinstance(raw, list):
                    raise ValueError(f"bundle.{key} должен быть массивом")
                merged[key].extend(dict(item) for item in raw)

        for key in BUNDLE_ARRAYS:
            id_keys = {
                "sources": "source_id",
                "projects": "project_id",
                "people": "person_id",
                "entities": "entity_id",
                "aliases": "alias_id",
                "release_windows": "observation_id",
                "statuses": "observation_id",
                "project_people": "link_id",
                "project_entities": "link_id",
                "temporal_facts": "observation_id",
            }
            identity_key = id_keys[key]
            unique: dict[str, dict[str, Any]] = {}
            anonymous: list[dict[str, Any]] = []
            for item in merged[key]:
                identity = item.get(identity_key)
                if identity in {None, ""}:
                    anonymous.append(item)
                    continue
                text = str(identity)
                previous = unique.get(text)
                if previous is not None and previous != item:
                    raise ValueError(
                        f"Конфликт duplicate {key}.{identity_key}={text}"
                    )
                unique[text] = item
            merged[key] = [unique[name] for name in sorted(unique)] + anonymous
        return merged

    def collect_from_registry(
        self,
        future_db_path: str | Path,
        *,
        project_ids: list[str] | None = None,
        limit: int = 100,
        retrieved_at: Any | None = None,
    ) -> dict[str, Any]:
        observed = _observed(retrieved_at)
        observed_iso = observed.isoformat()

        team_result = self.enricher.collect_from_registry(
            future_db_path,
            project_ids=project_ids,
            limit=limit,
            retrieved_at=observed,
        )
        facts_result = self.facts.collect_from_registry(
            future_db_path,
            project_ids=project_ids,
            limit=limit,
            retrieved_at=observed,
        )

        team_projects = list(team_result.get("project_ids") or [])
        fact_projects = list(facts_result.get("project_ids") or [])
        if team_projects != fact_projects:
            raise ValueError(
                "Wikidata collectors выбрали разные project sets; atomic refresh отменён"
            )

        bundle = self._merge_bundles(
            team_result["batch"]["bundle"],
            facts_result["batch"]["bundle"],
        )
        child_fingerprints = {
            "team": team_result["batch"]["source_fingerprint_sha256"],
            "facts": facts_result["batch"]["source_fingerprint_sha256"],
        }
        source_fingerprint = _fingerprint(
            {
                "collector": "wikidata-wdqs-refresh",
                "version": REFRESH_VERSION,
                "retrieved_at": observed_iso,
                "projects": team_projects,
                "children": child_fingerprints,
            }
        )
        batch = {
            "version": 1,
            "batch_id": (
                f"wikidata-refresh-{observed.strftime('%Y%m%dT%H%M%SZ')}-"
                f"{source_fingerprint[:12]}"
            ),
            "provider": "wikidata-wdqs-refresh",
            "retrieved_at": observed_iso,
            "cursor": f"projects:{len(team_projects)}",
            "source_fingerprint_sha256": source_fingerprint,
            "bundle": bundle,
        }
        warnings = list(team_result.get("warnings") or []) + list(
            facts_result.get("warnings") or []
        )
        return {
            "collector": "wikidata-wdqs-refresh",
            "collector_version": REFRESH_VERSION,
            "retrieved_at": observed_iso,
            "project_count": len(team_projects),
            "project_ids": team_projects,
            "warning_count": len(warnings),
            "warnings": warnings,
            "people_count": len(bundle["people"]),
            "entity_count": len(bundle["entities"]),
            "project_people_count": len(bundle["project_people"]),
            "project_entity_count": len(bundle["project_entities"]),
            "temporal_fact_count": len(bundle["temporal_facts"]),
            "child_fingerprints": child_fingerprints,
            "raw_cache_paths": [
                team_result.get("raw_cache_path"),
                facts_result.get("raw_cache_path"),
            ],
            "network_required_for_inference": False,
            "batch": batch,
        }
