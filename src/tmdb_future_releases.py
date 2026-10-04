from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from settings import config
from src.future_releases import FutureReleaseStore


TMDB_API_BASE = "https://api.themoviedb.org/3"
COLLECTOR_VERSION = 1
THEATRICAL_TYPES = {2, 3}


class TmdbFutureReleaseError(RuntimeError):
    pass


def _dt(value: Any, *, field_name: str) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value or "").strip().replace("Z", "+00:00"))
        except ValueError as exc:
            raise TmdbFutureReleaseError(f"{field_name} должен быть ISO-8601 datetime") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _fingerprint(payload: Any) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _safe_id(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


class TmdbFutureReleaseCollector:
    """Второй независимый источник региональных дат релиза для P9.

    Collector разрешает TMDb movie ID через уже известный IMDb ID проекта,
    затем читает региональные release dates. Сеть используется только на стадии
    refresh; inference читает исключительно локальный P9 registry.
    """

    def __init__(
        self,
        *,
        token: str | None = None,
        api_base: str = TMDB_API_BASE,
        cache_dir: str | Path | None = None,
        transport: Callable[[str], dict[str, Any]] | None = None,
    ) -> None:
        self.token = str(token or os.getenv("TMDB_READ_ACCESS_TOKEN") or "").strip()
        self.api_base = str(api_base or "").rstrip("/")
        if not self.api_base.startswith(("https://", "http://")):
            raise TmdbFutureReleaseError("TMDb api_base должен быть HTTP(S) URL")
        if transport is None and not self.token:
            raise TmdbFutureReleaseError(
                "Для TMDb collector нужен TMDB_READ_ACCESS_TOKEN"
            )
        self.transport = transport
        self.cache_dir = Path(
            cache_dir
            or Path(config.ABSPATH) / "data" / "future_releases" / "tmdb"
        )

    @staticmethod
    def registry_projects(
        future_db_path: str | Path,
        *,
        project_ids: list[str] | None = None,
        limit: int = 100,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        requested = [str(item).strip() for item in (project_ids or []) if str(item).strip()]
        warnings: list[dict[str, Any]] = []
        with FutureReleaseStore(future_db_path) as store:
            if requested:
                placeholders = ",".join("?" for _ in requested)
                rows = store.conn.execute(
                    f"""
                    SELECT project_id, imdb_id, canonical_title
                    FROM future_release_projects
                    WHERE project_id IN ({placeholders})
                    ORDER BY project_id
                    """,
                    requested,
                ).fetchall()
                found = {str(row[0]) for row in rows}
                for missing in sorted(set(requested) - found):
                    warnings.append({"project_id": missing, "reason": "project_not_found"})
            else:
                rows = store.conn.execute(
                    """
                    SELECT project_id, imdb_id, canonical_title
                    FROM future_release_projects
                    ORDER BY project_id
                    LIMIT ?
                    """,
                    [max(1, min(int(limit), 500))],
                ).fetchall()
        selected: list[dict[str, Any]] = []
        for row in rows:
            imdb_id = str(row[1] or "").strip()
            if not (imdb_id.startswith("tt") and imdb_id[2:].isdigit()):
                warnings.append({"project_id": str(row[0]), "reason": "imdb_id_missing"})
                continue
            selected.append(
                {
                    "project_id": str(row[0]),
                    "imdb_id": imdb_id,
                    "canonical_title": str(row[2]),
                }
            )
        return selected, warnings

    def _request(self, path: str, params: dict[str, str] | None = None) -> dict[str, Any]:
        query = f"?{urlencode(params)}" if params else ""
        url = f"{self.api_base}{path}{query}"
        if self.transport is not None:
            payload = self.transport(url)
            if not isinstance(payload, dict):
                raise TmdbFutureReleaseError("transport должен вернуть JSON-объект")
            return payload
        request = Request(
            url,
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {self.token}",
                "User-Agent": config.WIKIMEDIA_USER_AGENT,
            },
        )
        try:
            with urlopen(request, timeout=int(config.WIKIMEDIA_TIMEOUT_SECONDS)) as response:
                raw = response.read()
        except Exception as exc:
            raise TmdbFutureReleaseError(f"TMDb request failed: {exc}") from exc
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise TmdbFutureReleaseError("TMDb вернул некорректный JSON") from exc
        if not isinstance(payload, dict):
            raise TmdbFutureReleaseError("TMDb JSON должен быть объектом")
        return payload

    def _cache_raw(self, payload: dict[str, Any], *, retrieved_at: datetime) -> Path:
        fingerprint = _fingerprint(payload)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        target = self.cache_dir / (
            f"tmdb-release-{retrieved_at.strftime('%Y%m%dT%H%M%SZ')}-{fingerprint[:16]}.json"
        )
        if not target.exists():
            temporary = target.with_suffix(".json.tmp")
            temporary.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
                encoding="utf-8",
            )
            os.replace(temporary, target)
        return target

    @staticmethod
    def _resolve_tmdb_id(find_payload: dict[str, Any]) -> int | None:
        movie_results = find_payload.get("movie_results")
        if not isinstance(movie_results, list) or len(movie_results) != 1:
            return None
        item = movie_results[0]
        if not isinstance(item, dict):
            return None
        try:
            value = int(item.get("id"))
        except (TypeError, ValueError):
            return None
        return value if value > 0 else None

    @classmethod
    def normalize_release_dates(
        cls,
        release_payload: dict[str, Any],
        *,
        project: dict[str, Any],
        tmdb_id: int,
        retrieved_at: datetime,
    ) -> tuple[dict[str, list[dict[str, Any]]], list[dict[str, Any]]]:
        results = release_payload.get("results")
        if not isinstance(results, list):
            raise TmdbFutureReleaseError("TMDb release_dates не содержит results[]")
        stamp = retrieved_at.strftime("%Y%m%dT%H%M%SZ")
        source_id = f"tmdb:{tmdb_id}:release-dates:{stamp}"
        source = {
            "source_id": source_id,
            "provider": "TMDb",
            "url": f"https://www.themoviedb.org/movie/{tmdb_id}",
            "usage_basis": "licensed",
            "retrieved_at": retrieved_at.isoformat(),
        }
        windows: list[dict[str, Any]] = []
        warnings: list[dict[str, Any]] = []
        seen: set[tuple[str, str, int]] = set()
        for region in results:
            if not isinstance(region, dict):
                continue
            country = str(region.get("iso_3166_1") or "").strip().upper()
            if len(country) != 2 or not country.isalpha():
                warnings.append({"project_id": project["project_id"], "reason": "invalid_country_code"})
                continue
            dates = region.get("release_dates")
            if not isinstance(dates, list):
                continue
            for item in dates:
                if not isinstance(item, dict):
                    continue
                try:
                    release_type = int(item.get("type"))
                except (TypeError, ValueError):
                    continue
                if release_type not in THEATRICAL_TYPES:
                    continue
                raw_date = str(item.get("release_date") or "").strip()
                if not raw_date:
                    continue
                try:
                    release_at = _dt(raw_date, field_name="release_date")
                except TmdbFutureReleaseError:
                    warnings.append(
                        {
                            "project_id": project["project_id"],
                            "country": country,
                            "reason": "invalid_release_date",
                        }
                    )
                    continue
                key = (country, release_at.isoformat(), release_type)
                if key in seen:
                    continue
                seen.add(key)
                identity = _safe_id(f"{country}:{release_at.isoformat()}:{release_type}")
                windows.append(
                    {
                        "observation_id": f"tmdb-release:{tmdb_id}:{identity}:{stamp}",
                        "project_id": project["project_id"],
                        "territory": f"iso3166:{country}",
                        "release_start_at": release_at.isoformat(),
                        "release_end_at": release_at.isoformat(),
                        "precision": "exact",
                        "known_at": retrieved_at.isoformat(),
                        "source_id": source_id,
                        "confidence": 0.9 if release_type == 3 else 0.85,
                    }
                )
        return {
            "sources": [source] if windows else [],
            "projects": [],
            "people": [],
            "entities": [],
            "aliases": [],
            "release_windows": sorted(
                windows,
                key=lambda item: (item["territory"], item["release_start_at"], item["observation_id"]),
            ),
            "statuses": [],
            "project_people": [],
            "project_entities": [],
            "temporal_facts": [],
        }, warnings

    def collect_from_registry(
        self,
        future_db_path: str | Path,
        *,
        project_ids: list[str] | None = None,
        limit: int = 100,
        retrieved_at: Any | None = None,
    ) -> dict[str, Any]:
        observed = _dt(retrieved_at or datetime.now(timezone.utc), field_name="retrieved_at")
        projects, warnings = self.registry_projects(
            future_db_path,
            project_ids=project_ids,
            limit=limit,
        )
        if not projects:
            raise TmdbFutureReleaseError("В P9 registry нет проектов с IMDb ID для TMDb lookup")

        combined = {
            "sources": [], "projects": [], "people": [], "entities": [], "aliases": [],
            "release_windows": [], "statuses": [], "project_people": [],
            "project_entities": [], "temporal_facts": [],
        }
        raw_records: list[dict[str, Any]] = []
        resolved_count = 0
        for project in projects:
            find_payload = self._request(
                f"/find/{project['imdb_id']}",
                {"external_source": "imdb_id"},
            )
            tmdb_id = self._resolve_tmdb_id(find_payload)
            if tmdb_id is None:
                warnings.append({"project_id": project["project_id"], "reason": "tmdb_movie_not_resolved"})
                raw_records.append({"project_id": project["project_id"], "find": find_payload})
                continue
            release_payload = self._request(f"/movie/{tmdb_id}/release_dates")
            bundle, local_warnings = self.normalize_release_dates(
                release_payload,
                project=project,
                tmdb_id=tmdb_id,
                retrieved_at=observed,
            )
            warnings.extend(local_warnings)
            raw_records.append(
                {
                    "project_id": project["project_id"],
                    "imdb_id": project["imdb_id"],
                    "tmdb_id": tmdb_id,
                    "find": find_payload,
                    "release_dates": release_payload,
                }
            )
            for key in combined:
                combined[key].extend(bundle.get(key, []))
            resolved_count += 1

        raw_payload = {"retrieved_at": observed.isoformat(), "records": raw_records}
        cache_path = self._cache_raw(raw_payload, retrieved_at=observed)
        raw_fingerprint = _fingerprint(raw_payload)
        bundle_fingerprint = _fingerprint(combined)
        source_fingerprint = _fingerprint(
            {
                "collector": "tmdb-release-dates",
                "version": COLLECTOR_VERSION,
                "retrieved_at": observed.isoformat(),
                "raw_fingerprint_sha256": raw_fingerprint,
                "bundle_fingerprint_sha256": bundle_fingerprint,
            }
        )
        batch = {
            "version": 1,
            "batch_id": f"tmdb-release-{observed.strftime('%Y%m%dT%H%M%SZ')}-{source_fingerprint[:12]}",
            "provider": "tmdb-release-dates",
            "retrieved_at": observed.isoformat(),
            "cursor": f"projects:{len(projects)}",
            "source_fingerprint_sha256": source_fingerprint,
            "bundle": combined,
        }
        return {
            "collector": "tmdb-release-dates",
            "collector_version": COLLECTOR_VERSION,
            "project_count": len(projects),
            "resolved_project_count": resolved_count,
            "release_observation_count": len(combined["release_windows"]),
            "warning_count": len(warnings),
            "warnings": warnings,
            "raw_cache_path": str(cache_path),
            "raw_fingerprint_sha256": raw_fingerprint,
            "network_required_for_inference": False,
            "batch": batch,
        }
