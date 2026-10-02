from __future__ import annotations

import json
import logging
import re
import time
from collections import OrderedDict
from dataclasses import asdict, dataclass
from typing import Iterable

import duckdb

from src.wikimedia_enrichment import WIKIDATA_SPARQL_URL, WikimediaClient


logger = logging.getLogger(__name__)

_CYRILLIC_RE = re.compile(r"[А-Яа-яЁё]")
_IMDB_TITLE_RE = re.compile(r"tt\d+")
_IMDB_PERSON_RE = re.compile(r"nm\d+")


@dataclass(frozen=True)
class AliasMatch:
    """Результат сопоставления русского имени/названия с IMDb."""

    input: str
    canonical: str
    imdb_id: str
    source: str = "wikidata"

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


class RussianInputResolver:
    """Разрешает русские названия и имена через Wikidata → локальный IMDb.

    Внешний источник используется только для поиска IMDb ID. Каноническое
    название/имя и проверка существования берутся из локальной imdb.duckdb.
    Если Wikimedia недоступна или соответствие не найдено, inference не падает:
    исходная строка остаётся рабочим fallback.
    """

    def __init__(
        self,
        imdb_conn: duckdb.DuckDBPyConnection,
        *,
        client: WikimediaClient | None = None,
    ) -> None:
        self.conn = imdb_conn
        self.client = client or WikimediaClient(
            min_interval_seconds=0.2,
            timeout_seconds=4,
        )
        self._cache: OrderedDict[
            tuple[str, str, int | None],
            AliasMatch | None,
        ] = OrderedDict()
        self._cache_limit = 2048

    @staticmethod
    def needs_resolution(value: str | None) -> bool:
        return bool(value and _CYRILLIC_RE.search(value))

    def _remember(
        self,
        key: tuple[str, str, int | None],
        match: AliasMatch | None,
    ) -> None:
        """Ограничивает alias-cache, чтобы публичный поиск не раздувал RAM."""
        if key in self._cache:
            self._cache.move_to_end(key)
        self._cache[key] = match
        while len(self._cache) > self._cache_limit:
            self._cache.popitem(last=False)

    @staticmethod
    def _literal(value: str) -> str:
        return json.dumps(value, ensure_ascii=False)

    @staticmethod
    def _variants(value: str) -> list[str]:
        clean = " ".join(value.strip().split())
        variants = [clean]
        titled = clean.title()
        if titled not in variants:
            variants.append(titled)
        return variants

    def _query_wikidata(self, values: Iterable[str]) -> dict[str, list[str]]:
        rows: list[str] = []
        seen: set[tuple[str, str]] = set()
        for raw in values:
            clean = " ".join(raw.strip().split())
            for variant in self._variants(clean):
                key = (clean.casefold(), variant)
                if key in seen:
                    continue
                seen.add(key)
                rows.append(
                    f"({self._literal(clean.casefold())} "
                    f"{self._literal(variant)}@ru)"
                )

        if not rows:
            return {}

        query = f"""
PREFIX wdt: <http://www.wikidata.org/prop/direct/>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
PREFIX skos: <http://www.w3.org/2004/02/skos/core#>

SELECT DISTINCT ?query ?imdb WHERE {{
  VALUES (?query ?label) {{
    {" ".join(rows)}
  }}

  {{
    ?item rdfs:label ?label .
  }}
  UNION
  {{
    ?item skos:altLabel ?label .
  }}

  ?item wdt:P345 ?imdb .
}}
"""
        # Inference не должен зависать на внешнем enrichment. Один короткий
        # запрос без retry: при проблеме просто используем исходный ввод.
        self.client._throttle()
        response = self.client.session.post(
            WIKIDATA_SPARQL_URL,
            data={"query": query, "format": "json"},
            headers={"Accept": "application/sparql-results+json"},
            timeout=min(self.client.timeout_seconds, 4),
        )
        self.client._last_request_at = time.monotonic()
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise RuntimeError("Wikidata вернула неожиданный JSON")

        result: dict[str, list[str]] = {}
        for binding in payload.get("results", {}).get("bindings", []):
            query_value = str(binding.get("query", {}).get("value") or "").casefold()
            imdb_id = str(binding.get("imdb", {}).get("value") or "")
            if not query_value or not imdb_id:
                continue
            result.setdefault(query_value, []).append(imdb_id)
        return result

    def _resolve_title_candidates(
        self,
        raw: str,
        imdb_ids: Iterable[str],
        year: int | None,
    ) -> AliasMatch | None:
        candidates: list[tuple[int, str, str]] = []
        for imdb_id in imdb_ids:
            if not _IMDB_TITLE_RE.fullmatch(imdb_id):
                continue
            row = self.conn.execute(
                """
                SELECT primaryTitle, TRY_CAST(startYear AS INTEGER)
                FROM title_basics
                WHERE tconst = ?
                LIMIT 1
                """,
                [imdb_id],
            ).fetchone()
            if not row:
                continue
            canonical = str(row[0] or "").strip()
            candidate_year = int(row[1]) if row[1] is not None else None
            if not canonical:
                continue
            distance = (
                abs(candidate_year - year)
                if year is not None and candidate_year is not None
                else 9999
            )
            candidates.append((distance, imdb_id, canonical))

        if not candidates:
            return None

        candidates.sort(key=lambda item: (item[0], item[1]))
        _, imdb_id, canonical = candidates[0]
        return AliasMatch(input=raw, canonical=canonical, imdb_id=imdb_id)

    def _person_supports_role(self, imdb_id: str, role: str) -> bool:
        categories = (
            ("director",)
            if role == "director"
            else ("actor", "actress")
        )
        placeholders = ",".join("?" for _ in categories)
        row = self.conn.execute(
            f"""
            SELECT 1
            FROM title_principals
            WHERE nconst = ?
              AND category IN ({placeholders})
            LIMIT 1
            """,
            [imdb_id, *categories],
        ).fetchone()
        return row is not None

    def _resolve_person_candidates(
        self,
        raw: str,
        imdb_ids: Iterable[str],
        role: str,
    ) -> AliasMatch | None:
        fallback: AliasMatch | None = None
        for imdb_id in imdb_ids:
            if not _IMDB_PERSON_RE.fullmatch(imdb_id):
                continue
            row = self.conn.execute(
                """
                SELECT primaryName
                FROM name_basics
                WHERE nconst = ?
                LIMIT 1
                """,
                [imdb_id],
            ).fetchone()
            if not row or not row[0]:
                continue

            match = AliasMatch(
                input=raw,
                canonical=str(row[0]).strip(),
                imdb_id=imdb_id,
            )
            if self._person_supports_role(imdb_id, role):
                return match
            if fallback is None:
                fallback = match
        return fallback

    def resolve_inputs(
        self,
        *,
        title: str | None,
        director: str | None,
        actors: list[str] | None,
        year: int | None,
    ) -> dict:
        actors = actors or []
        requested: list[tuple[str, str, int | None]] = []

        if self.needs_resolution(title):
            requested.append(("title", str(title), year))
        if self.needs_resolution(director):
            requested.append(("director", str(director), None))
        for actor in actors[:3]:
            if self.needs_resolution(actor):
                requested.append(("actor", str(actor), None))

        missing_values: list[str] = []
        for role, raw, item_year in requested:
            key = (role, raw.casefold(), item_year)
            if key not in self._cache:
                missing_values.append(raw)

        wikidata_matches: dict[str, list[str]] = {}
        if missing_values:
            try:
                wikidata_matches = self._query_wikidata(missing_values)
            except Exception as exc:
                logger.warning(
                    "Не удалось разрешить русские названия/имена через Wikidata: %s",
                    exc,
                )

            for role, raw, item_year in requested:
                key = (role, raw.casefold(), item_year)
                if key in self._cache:
                    continue
                ids = wikidata_matches.get(raw.casefold(), [])
                if role == "title":
                    match = self._resolve_title_candidates(raw, ids, item_year)
                else:
                    match = self._resolve_person_candidates(raw, ids, role)
                self._remember(key, match)

        title_match = (
            self._cache.get(("title", str(title).casefold(), year))
            if self.needs_resolution(title)
            else None
        )
        director_match = (
            self._cache.get(("director", str(director).casefold(), None))
            if self.needs_resolution(director)
            else None
        )

        actor_matches: list[AliasMatch | None] = []
        for actor in actors:
            if self.needs_resolution(actor):
                actor_matches.append(
                    self._cache.get(("actor", actor.casefold(), None))
                )
            else:
                actor_matches.append(None)

        resolved_actors = [
            match.canonical if match else actor
            for actor, match in zip(actors, actor_matches)
        ]

        return {
            "title": title_match.canonical if title_match else title,
            "director": director_match.canonical if director_match else director,
            "actors": resolved_actors,
            "matches": {
                "title": title_match.to_dict() if title_match else None,
                "director": director_match.to_dict() if director_match else None,
                "actors": [
                    match.to_dict() if match else None
                    for match in actor_matches
                ],
            },
        }
