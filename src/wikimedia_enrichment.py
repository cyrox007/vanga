from __future__ import annotations

import json
import logging
import random
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from html import unescape
from pathlib import Path
from typing import Iterable
from urllib.parse import unquote, urlparse

import duckdb
import requests
from bs4 import BeautifulSoup

from settings import config


logger = logging.getLogger(__name__)

WIKIDATA_SPARQL_URL = "https://query.wikidata.org/sparql"
WIKIPEDIA_API = {
    "en": "https://en.wikipedia.org/w/api.php",
    "ru": "https://ru.wikipedia.org/w/api.php",
}
PLOT_SECTION_NAMES = {
    "en": {"plot", "plot summary", "synopsis", "premise"},
    "ru": {"сюжет", "содержание", "краткое содержание", "сюжет фильма"},
}

STRUCTURED_PROPERTIES = {
    "directors": "P57",
    "cast_members": "P161",
    "screenwriters": "P58",
    "production_companies": "P272",
    "countries": "P495",
    "languages": "P364",
    "genres": "P136",
    "series": "P179",
    "based_on": "P144",
    "release_dates": "P577",
}


@dataclass(frozen=True)
class MovieCandidate:
    imdb_id: str
    title: str
    year: int | None


@dataclass(frozen=True)
class WikidataMatch:
    imdb_id: str
    qid: str
    enwiki_url: str | None
    ruwiki_url: str | None


class WikimediaRequestError(RuntimeError):
    pass


class WikimediaClient:
    """Экономный HTTP-клиент Wikimedia с throttling и retry/backoff."""

    def __init__(
        self,
        *,
        user_agent: str | None = None,
        min_interval_seconds: float | None = None,
        timeout_seconds: int | None = None,
        session: requests.Session | None = None,
    ) -> None:
        self.user_agent = user_agent or config.WIKIMEDIA_USER_AGENT
        if not self.user_agent:
            raise ValueError("VANGA_WIKIMEDIA_USER_AGENT не должен быть пустым")

        self.min_interval_seconds = (
            config.WIKIMEDIA_MIN_INTERVAL_SECONDS
            if min_interval_seconds is None
            else max(0.0, min_interval_seconds)
        )
        self.timeout_seconds = timeout_seconds or config.WIKIMEDIA_TIMEOUT_SECONDS
        self.session = session or requests.Session()
        self.session.headers.update(
            {
                "User-Agent": self.user_agent,
                "Accept": "application/json",
            }
        )
        self._last_request_at = 0.0

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_request_at
        remaining = self.min_interval_seconds - elapsed
        if remaining > 0:
            time.sleep(remaining)

    def _request_json(self, method: str, url: str, **kwargs) -> dict:
        last_error: Exception | None = None
        for attempt in range(5):
            self._throttle()
            try:
                response = self.session.request(
                    method,
                    url,
                    timeout=self.timeout_seconds,
                    **kwargs,
                )
                self._last_request_at = time.monotonic()

                if response.status_code == 429 or response.status_code >= 500:
                    retry_after = response.headers.get("Retry-After")
                    if retry_after and retry_after.isdigit():
                        delay = min(60.0, float(retry_after))
                    else:
                        delay = min(30.0, (2 ** attempt) + random.random())
                    logger.warning(
                        "Wikimedia вернула HTTP %s; повтор через %.1f сек.",
                        response.status_code,
                        delay,
                    )
                    time.sleep(delay)
                    continue

                response.raise_for_status()
                payload = response.json()
                if not isinstance(payload, dict):
                    raise WikimediaRequestError("Wikimedia вернула неожиданный JSON")
                return payload
            except (requests.RequestException, ValueError, WikimediaRequestError) as exc:
                last_error = exc
                if attempt == 4:
                    break
                delay = min(30.0, (2 ** attempt) + random.random())
                logger.warning(
                    "Ошибка Wikimedia (%s); повтор через %.1f сек.",
                    exc,
                    delay,
                )
                time.sleep(delay)

        raise WikimediaRequestError(str(last_error or "Неизвестная ошибка Wikimedia"))

    @staticmethod
    def _sparql_string(value: str) -> str:
        return json.dumps(value, ensure_ascii=False)

    def match_imdb_ids(self, imdb_ids: Iterable[str]) -> dict[str, WikidataMatch]:
        ids = [item for item in imdb_ids if re.fullmatch(r"tt\d+", item or "")]
        if not ids:
            return {}

        values = " ".join(self._sparql_string(item) for item in ids)
        query = f"""
PREFIX wdt: <http://www.wikidata.org/prop/direct/>
PREFIX schema: <http://schema.org/>

SELECT ?imdb ?item ?enwiki ?ruwiki WHERE {{
  VALUES ?imdb {{ {values} }}
  ?item wdt:P345 ?imdb .
  OPTIONAL {{
    ?enwiki schema:about ?item ;
            schema:isPartOf <https://en.wikipedia.org/> .
  }}
  OPTIONAL {{
    ?ruwiki schema:about ?item ;
            schema:isPartOf <https://ru.wikipedia.org/> .
  }}
}}
"""
        payload = self._request_json(
            "POST",
            WIKIDATA_SPARQL_URL,
            data={"query": query, "format": "json"},
            headers={"Accept": "application/sparql-results+json"},
        )

        result: dict[str, WikidataMatch] = {}
        for binding in payload.get("results", {}).get("bindings", []):
            imdb_id = binding.get("imdb", {}).get("value")
            item_uri = binding.get("item", {}).get("value")
            if not imdb_id or not item_uri:
                continue
            qid = item_uri.rsplit("/", 1)[-1]
            if not re.fullmatch(r"Q\d+", qid):
                continue
            result[imdb_id] = WikidataMatch(
                imdb_id=imdb_id,
                qid=qid,
                enwiki_url=binding.get("enwiki", {}).get("value"),
                ruwiki_url=binding.get("ruwiki", {}).get("value"),
            )
        return result

    def fetch_structured(self, qids: Iterable[str]) -> dict[str, dict[str, list[str]]]:
        clean_qids = [qid for qid in qids if re.fullmatch(r"Q\d+", qid or "")]
        if not clean_qids:
            return {}

        values = " ".join("wd:" + qid for qid in clean_qids)
        optional_lines: list[str] = []
        select_lines: list[str] = []
        for field, prop in STRUCTURED_PROPERTIES.items():
            variable = "v_" + field
            optional_lines.append(f"OPTIONAL {{ ?item wdt:{prop} ?{variable} . }}")
            select_lines.append(
                f'(GROUP_CONCAT(DISTINCT STR(?{variable}); separator="|") AS ?{field})'
            )

        query = f"""
PREFIX wd: <http://www.wikidata.org/entity/>
PREFIX wdt: <http://www.wikidata.org/prop/direct/>

SELECT ?item
  {" ".join(select_lines)}
WHERE {{
  VALUES ?item {{ {values} }}
  {" ".join(optional_lines)}
}}
GROUP BY ?item
"""
        payload = self._request_json(
            "POST",
            WIKIDATA_SPARQL_URL,
            data={"query": query, "format": "json"},
            headers={"Accept": "application/sparql-results+json"},
        )

        result: dict[str, dict[str, list[str]]] = {}
        for binding in payload.get("results", {}).get("bindings", []):
            item_uri = binding.get("item", {}).get("value")
            if not item_uri:
                continue
            qid = item_uri.rsplit("/", 1)[-1]
            fields: dict[str, list[str]] = {}
            for field in STRUCTURED_PROPERTIES:
                raw = binding.get(field, {}).get("value", "")
                values_for_field = [self._compact_wikidata_value(v) for v in raw.split("|") if v]
                fields[field] = values_for_field
            result[qid] = fields
        return result

    @staticmethod
    def _compact_wikidata_value(value: str) -> str:
        entity_prefix = "http://www.wikidata.org/entity/"
        if value.startswith(entity_prefix):
            return value[len(entity_prefix):]
        return value

    @staticmethod
    def page_title_from_url(url: str | None) -> str | None:
        if not url:
            return None
        parsed = urlparse(url)
        marker = "/wiki/"
        if marker not in parsed.path:
            return None
        return unquote(parsed.path.split(marker, 1)[1]).replace("_", " ")

    def fetch_plot(self, lang: str, page_title: str) -> dict | None:
        api_url = WIKIPEDIA_API[lang]
        metadata = self._request_json(
            "GET",
            api_url,
            params={
                "action": "parse",
                "page": page_title,
                "prop": "sections|revid|displaytitle",
                "format": "json",
                "formatversion": "2",
                "redirects": "1",
            },
        )
        parse = metadata.get("parse")
        if not isinstance(parse, dict):
            return None

        section_index = self._find_plot_section(lang, parse.get("sections", []))
        if section_index is None:
            return {
                "title": page_title,
                "revision_id": parse.get("revid"),
                "section": None,
                "text": None,
            }

        section_payload = self._request_json(
            "GET",
            api_url,
            params={
                "action": "parse",
                "page": page_title,
                "prop": "text|revid",
                "section": section_index,
                "format": "json",
                "formatversion": "2",
                "redirects": "1",
            },
        )
        parsed_section = section_payload.get("parse")
        if not isinstance(parsed_section, dict):
            return None

        html = parsed_section.get("text") or ""
        text = self._clean_wikipedia_html(html)
        return {
            "title": page_title,
            "revision_id": parsed_section.get("revid") or parse.get("revid"),
            "section": section_index,
            "text": text or None,
        }

    @staticmethod
    def _find_plot_section(lang: str, sections: list[dict]) -> str | None:
        wanted = PLOT_SECTION_NAMES[lang]
        for section in sections:
            line = unescape(str(section.get("line") or "")).strip().casefold()
            line = re.sub(r"\s+", " ", line)
            if line in wanted:
                index = section.get("index")
                return str(index) if index is not None else None
        return None

    @staticmethod
    def _clean_wikipedia_html(html: str) -> str:
        soup = BeautifulSoup(html, "html.parser")
        for node in soup.select(
            "script, style, table, sup.reference, .mw-editsection, .navbox, .infobox, figure"
        ):
            node.decompose()
        text = soup.get_text("\n", strip=True)
        lines = [re.sub(r"\s+", " ", line).strip() for line in text.splitlines()]
        return "\n".join(line for line in lines if line)


class EnrichmentStore:
    """Отдельная DuckDB для enrichment, чтобы не блокировать runtime imdb.duckdb."""

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
            CREATE TABLE IF NOT EXISTS film_enrichment (
                imdb_id VARCHAR PRIMARY KEY,
                imdb_title VARCHAR,
                imdb_year INTEGER,
                wikidata_id VARCHAR,
                wikidata_json VARCHAR,
                enwiki_title VARCHAR,
                enwiki_revision BIGINT,
                enwiki_plot VARCHAR,
                ruwiki_title VARCHAR,
                ruwiki_revision BIGINT,
                ruwiki_plot VARCHAR,
                status VARCHAR NOT NULL,
                error VARCHAR,
                fetched_at TIMESTAMP WITH TIME ZONE NOT NULL
            )
            """
        )
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
        self.conn.execute(
            "DELETE FROM enrichment_state WHERE state_key = ?",
            [key],
        )

    def load_retry_candidates(self, limit: int) -> list[MovieCandidate]:
        rows = self.conn.execute(
            """
            SELECT imdb_id, imdb_title, imdb_year
            FROM film_enrichment
            WHERE status = 'error'
            ORDER BY fetched_at
            LIMIT ?
            """,
            [limit],
        ).fetchall()
        return [
            MovieCandidate(
                imdb_id=str(row[0]),
                title=str(row[1] or ""),
                year=int(row[2]) if row[2] is not None else None,
            )
            for row in rows
        ]

    def upsert(
        self,
        candidate: MovieCandidate,
        *,
        match: WikidataMatch | None,
        structured: dict[str, list[str]] | None,
        en_plot: dict | None,
        ru_plot: dict | None,
        status: str,
        error: str | None = None,
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO film_enrichment(
                imdb_id, imdb_title, imdb_year,
                wikidata_id, wikidata_json,
                enwiki_title, enwiki_revision, enwiki_plot,
                ruwiki_title, ruwiki_revision, ruwiki_plot,
                status, error, fetched_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(imdb_id) DO UPDATE SET
                imdb_title = excluded.imdb_title,
                imdb_year = excluded.imdb_year,
                wikidata_id = excluded.wikidata_id,
                wikidata_json = excluded.wikidata_json,
                enwiki_title = excluded.enwiki_title,
                enwiki_revision = excluded.enwiki_revision,
                enwiki_plot = excluded.enwiki_plot,
                ruwiki_title = excluded.ruwiki_title,
                ruwiki_revision = excluded.ruwiki_revision,
                ruwiki_plot = excluded.ruwiki_plot,
                status = excluded.status,
                error = excluded.error,
                fetched_at = excluded.fetched_at
            """,
            [
                candidate.imdb_id,
                candidate.title,
                candidate.year,
                match.qid if match else None,
                json.dumps(structured or {}, ensure_ascii=False, sort_keys=True),
                en_plot.get("title") if en_plot else None,
                en_plot.get("revision_id") if en_plot else None,
                en_plot.get("text") if en_plot else None,
                ru_plot.get("title") if ru_plot else None,
                ru_plot.get("revision_id") if ru_plot else None,
                ru_plot.get("text") if ru_plot else None,
                status,
                error,
                datetime.now(timezone.utc),
            ],
        )


def load_candidates(
    imdb_db_path: str | Path,
    *,
    after_tconst: str,
    since_year: int,
    limit: int,
) -> list[MovieCandidate]:
    conn = duckdb.connect(str(imdb_db_path), read_only=True)
    try:
        rows = conn.execute(
            """
            SELECT
                tconst,
                primaryTitle,
                TRY_CAST(startYear AS INTEGER)
            FROM title_basics
            WHERE titleType = 'movie'
              AND tconst > ?
              AND TRY_CAST(startYear AS INTEGER) >= ?
            ORDER BY tconst
            LIMIT ?
            """,
            [after_tconst, since_year, limit],
        ).fetchall()
        return [
            MovieCandidate(
                imdb_id=str(row[0]),
                title=str(row[1] or ""),
                year=int(row[2]) if row[2] is not None else None,
            )
            for row in rows
        ]
    finally:
        conn.close()


def enrich_batch(
    candidates: list[MovieCandidate],
    *,
    client: WikimediaClient,
    store: EnrichmentStore,
) -> tuple[int, int]:
    if not candidates:
        return 0, 0

    matches = client.match_imdb_ids(candidate.imdb_id for candidate in candidates)
    structured_by_qid = client.fetch_structured(
        match.qid for match in matches.values()
    )

    success = 0
    failed = 0

    for candidate in candidates:
        match = matches.get(candidate.imdb_id)
        if match is None:
            store.upsert(
                candidate,
                match=None,
                structured=None,
                en_plot=None,
                ru_plot=None,
                status="no_wikidata",
            )
            continue

        try:
            en_title = client.page_title_from_url(match.enwiki_url)
            ru_title = client.page_title_from_url(match.ruwiki_url)
            en_plot = client.fetch_plot("en", en_title) if en_title else None
            ru_plot = client.fetch_plot("ru", ru_title) if ru_title else None
            store.upsert(
                candidate,
                match=match,
                structured=structured_by_qid.get(match.qid, {}),
                en_plot=en_plot,
                ru_plot=ru_plot,
                status="ok",
            )
            success += 1
        except Exception as exc:
            logger.exception("Не удалось обогатить %s", candidate.imdb_id)
            store.upsert(
                candidate,
                match=match,
                structured=structured_by_qid.get(match.qid, {}),
                en_plot=None,
                ru_plot=None,
                status="error",
                error=str(exc)[:2000],
            )
            failed += 1

    return success, failed
