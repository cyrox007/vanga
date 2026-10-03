from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

import duckdb

from settings import config
from src.adaptation_analysis import CHANGE_TYPES, DIMENSIONS, AdaptationValidationError


EXPERT_SPLITS = {"train", "development", "blind", "external_transfer"}
MEDIA_TYPES = {"video", "article", "podcast", "post", "other"}
EVIDENCE_POLARITIES = {"supporting", "contradicting"}
EVIDENCE_KINDS = {
    "film_summary",
    "source_summary",
    "storymap",
    "storydiff",
    "production_context",
    "material_reference",
    "other",
}
EXPERT_DIMENSIONS = set(DIMENSIONS) | {
    "continuity",
    "script_logic",
    "setup_payoff",
    "production_context",
}
EXPERT_CHANGE_TYPES = set(CHANGE_TYPES) | {
    "contradiction",
    "setup_without_payoff",
    "payoff_without_setup",
}

_FORBIDDEN_FULL_TEXT_FIELDS = {
    "text",
    "full_text",
    "transcript",
    "full_transcript",
    "review_text",
    "review_transcript",
}


class ExpertCorpusValidationError(AdaptationValidationError):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _clean(
    value: Any,
    *,
    field_name: str,
    limit: int,
    required: bool = False,
) -> str:
    text = " ".join(str(value or "").strip().split())
    if required and not text:
        raise ExpertCorpusValidationError(f"{field_name} не должен быть пустым")
    if len(text) > limit:
        raise ExpertCorpusValidationError(
            f"{field_name} длиннее допустимых {limit} символов"
        )
    return text


def _confidence(value: Any, *, field_name: str = "confidence") -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ExpertCorpusValidationError(f"{field_name} должен быть числом") from exc
    if not 0.0 <= parsed <= 1.0:
        raise ExpertCorpusValidationError(f"{field_name} должен быть в диапазоне 0..1")
    return parsed


def _parse_datetime(value: Any, *, field_name: str, required: bool = False):
    if value in {None, ""}:
        if required:
            raise ExpertCorpusValidationError(f"{field_name} обязателен")
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value).strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError as exc:
            raise ExpertCorpusValidationError(
                f"{field_name} должен быть ISO datetime"
            ) from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _validate_url(value: Any) -> str:
    url = _clean(value, field_name="source_url", limit=2000, required=True)
    if not re.match(r"^https?://", url, flags=re.IGNORECASE):
        raise ExpertCorpusValidationError("source_url должен начинаться с http:// или https://")
    return url


def _reject_full_text(payload: dict[str, Any], *, entity: str) -> None:
    forbidden = sorted(
        key
        for key in _FORBIDDEN_FULL_TEXT_FIELDS
        if key in payload and payload.get(key) not in {None, ""}
    )
    if forbidden:
        raise ExpertCorpusValidationError(
            f"{entity}: полные тексты/транскрипты не хранятся в corpus registry; "
            "запрещённые поля: " + ", ".join(forbidden)
        )


def _json_list(value: Any, *, field_name: str, item_limit: int = 120) -> str:
    if value in {None, ""}:
        rows: list[str] = []
    else:
        if not isinstance(value, (list, tuple, set)):
            raise ExpertCorpusValidationError(f"{field_name} должен быть массивом")
        rows = sorted(
            {
                _clean(item, field_name=field_name, limit=item_limit, required=True)
                for item in value
            }
        )
    return json.dumps(rows, ensure_ascii=False)


class ExpertCorpusStore:
    """Структурированный retrospective corpus без хранения полных обзоров.

    Corpus физически отделён от prediction model. Он хранит ссылки на публичные
    материалы и наши структурированные аннотации, а не транскрипты. Любой
    перенос найденной закономерности в pre-release Vanga требует отдельного
    proxy + temporal ablation.
    """

    def __init__(self, path: Path | str | None = None) -> None:
        default_path = getattr(
            config,
            "EXPERT_CORPUS_DB_PATH",
            str(Path(config.ABSPATH) / "expert_corpus.duckdb"),
        )
        self.path = Path(path or default_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = duckdb.connect(str(self.path))
        self._ensure_schema()

    def close(self) -> None:
        self.conn.close()

    def _ensure_schema(self) -> None:
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS expert_profiles (
                expert_id VARCHAR PRIMARY KEY,
                display_name VARCHAR NOT NULL,
                focus_json VARCHAR NOT NULL,
                note VARCHAR,
                created_at TIMESTAMP WITH TIME ZONE NOT NULL,
                updated_at TIMESTAMP WITH TIME ZONE NOT NULL
            )
            """
        )
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS expert_cases (
                case_id VARCHAR PRIMARY KEY,
                imdb_id VARCHAR,
                film_title VARCHAR NOT NULL,
                film_year INTEGER,
                source_work_id VARCHAR,
                split VARCHAR NOT NULL,
                note VARCHAR,
                created_at TIMESTAMP WITH TIME ZONE NOT NULL,
                updated_at TIMESTAMP WITH TIME ZONE NOT NULL
            )
            """
        )
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS expert_materials (
                material_id VARCHAR PRIMARY KEY,
                expert_id VARCHAR NOT NULL,
                title VARCHAR NOT NULL,
                source_url VARCHAR NOT NULL,
                media_type VARCHAR NOT NULL,
                published_at TIMESTAMP WITH TIME ZONE,
                retrieved_at TIMESTAMP WITH TIME ZONE NOT NULL,
                note VARCHAR,
                created_at TIMESTAMP WITH TIME ZONE NOT NULL,
                updated_at TIMESTAMP WITH TIME ZONE NOT NULL
            )
            """
        )
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS expert_case_materials (
                link_id VARCHAR PRIMARY KEY,
                case_id VARCHAR NOT NULL,
                material_id VARCHAR NOT NULL,
                created_at TIMESTAMP WITH TIME ZONE NOT NULL
            )
            """
        )
        self.conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_expert_case_material_unique
            ON expert_case_materials(case_id, material_id)
            """
        )
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS expert_claims (
                claim_id VARCHAR PRIMARY KEY,
                case_id VARCHAR NOT NULL,
                material_id VARCHAR NOT NULL,
                dimension VARCHAR NOT NULL,
                change_type VARCHAR NOT NULL,
                timecode_or_section VARCHAR NOT NULL,
                claim_summary VARCHAR NOT NULL,
                observation VARCHAR NOT NULL,
                structural_consequence VARCHAR NOT NULL,
                expert_interpretation VARCHAR NOT NULL,
                confidence DOUBLE NOT NULL,
                story_map_id VARCHAR,
                story_diff_annotation_id VARCHAR,
                tags_json VARCHAR NOT NULL,
                created_at TIMESTAMP WITH TIME ZONE NOT NULL,
                updated_at TIMESTAMP WITH TIME ZONE NOT NULL
            )
            """
        )
        self.conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_expert_claims_case
            ON expert_claims(case_id)
            """
        )
        self.conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_expert_claims_material
            ON expert_claims(material_id)
            """
        )
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS expert_evidence (
                evidence_id VARCHAR PRIMARY KEY,
                claim_id VARCHAR NOT NULL,
                polarity VARCHAR NOT NULL,
                evidence_kind VARCHAR NOT NULL,
                description VARCHAR NOT NULL,
                locator VARCHAR,
                reference_id VARCHAR,
                confidence DOUBLE NOT NULL,
                created_at TIMESTAMP WITH TIME ZONE NOT NULL,
                updated_at TIMESTAMP WITH TIME ZONE NOT NULL
            )
            """
        )
        self.conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_expert_evidence_claim
            ON expert_evidence(claim_id)
            """
        )

    def _exists(self, table: str, column: str, value: str) -> bool:
        row = self.conn.execute(
            f"SELECT 1 FROM {table} WHERE {column} = ? LIMIT 1",
            [value],
        ).fetchone()
        return row is not None

    def _require(self, table: str, column: str, value: str, *, entity: str) -> None:
        if not self._exists(table, column, value):
            raise ExpertCorpusValidationError(f"Неизвестный {entity}: {value}")

    def upsert_profile(self, payload: dict[str, Any]) -> str:
        _reject_full_text(payload, entity="expert profile")
        expert_id = _clean(
            payload.get("expert_id"), field_name="expert_id", limit=160, required=True
        )
        display_name = _clean(
            payload.get("display_name"),
            field_name="display_name",
            limit=300,
            required=True,
        )
        focus_json = _json_list(payload.get("focus") or [], field_name="focus", item_limit=100)
        note = _clean(payload.get("note"), field_name="note", limit=2000) or None
        now = _now()
        self.conn.execute(
            """
            INSERT INTO expert_profiles(
                expert_id, display_name, focus_json, note, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(expert_id) DO UPDATE SET
                display_name=excluded.display_name,
                focus_json=excluded.focus_json,
                note=excluded.note,
                updated_at=excluded.updated_at
            """,
            [expert_id, display_name, focus_json, note, now, now],
        )
        return expert_id

    def upsert_case(self, payload: dict[str, Any]) -> str:
        _reject_full_text(payload, entity="expert case")
        case_id = _clean(
            payload.get("case_id"), field_name="case_id", limit=180, required=True
        )
        film_title = _clean(
            payload.get("film_title"),
            field_name="film_title",
            limit=500,
            required=True,
        )
        imdb_id = _clean(payload.get("imdb_id"), field_name="imdb_id", limit=16) or None
        if imdb_id and (not imdb_id.startswith("tt") or not imdb_id[2:].isdigit()):
            raise ExpertCorpusValidationError("imdb_id должен иметь вид tt1234567")
        raw_year = payload.get("film_year")
        film_year = None
        if raw_year not in {None, ""}:
            try:
                film_year = int(raw_year)
            except (TypeError, ValueError) as exc:
                raise ExpertCorpusValidationError("film_year должен быть целым числом") from exc
            if not 1888 <= film_year <= 2100:
                raise ExpertCorpusValidationError("film_year вне диапазона 1888..2100")
        split = str(payload.get("split") or "development").strip().casefold()
        if split not in EXPERT_SPLITS:
            raise ExpertCorpusValidationError(
                "split должен быть train, development, blind или external_transfer"
            )
        source_work_id = _clean(
            payload.get("source_work_id"),
            field_name="source_work_id",
            limit=180,
        ) or None
        note = _clean(payload.get("note"), field_name="note", limit=2000) or None
        now = _now()
        self.conn.execute(
            """
            INSERT INTO expert_cases(
                case_id, imdb_id, film_title, film_year, source_work_id,
                split, note, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(case_id) DO UPDATE SET
                imdb_id=excluded.imdb_id,
                film_title=excluded.film_title,
                film_year=excluded.film_year,
                source_work_id=excluded.source_work_id,
                split=excluded.split,
                note=excluded.note,
                updated_at=excluded.updated_at
            """,
            [
                case_id,
                imdb_id,
                film_title,
                film_year,
                source_work_id,
                split,
                note,
                now,
                now,
            ],
        )
        return case_id

    def upsert_material(self, payload: dict[str, Any]) -> str:
        _reject_full_text(payload, entity="expert material")
        material_id = _clean(
            payload.get("material_id"),
            field_name="material_id",
            limit=180,
            required=True,
        )
        expert_id = _clean(
            payload.get("expert_id"), field_name="expert_id", limit=160, required=True
        )
        self._require(
            "expert_profiles", "expert_id", expert_id, entity="expert profile"
        )
        media_type = str(payload.get("media_type") or "other").strip().casefold()
        if media_type not in MEDIA_TYPES:
            raise ExpertCorpusValidationError(
                "media_type должен быть video, article, podcast, post или other"
            )
        title = _clean(
            payload.get("title"), field_name="material.title", limit=700, required=True
        )
        source_url = _validate_url(payload.get("source_url"))
        published_at = _parse_datetime(
            payload.get("published_at"), field_name="published_at"
        )
        retrieved_at = _parse_datetime(
            payload.get("retrieved_at") or _now(),
            field_name="retrieved_at",
            required=True,
        )
        note = _clean(payload.get("note"), field_name="note", limit=2000) or None
        now = _now()
        self.conn.execute(
            """
            INSERT INTO expert_materials(
                material_id, expert_id, title, source_url, media_type,
                published_at, retrieved_at, note, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(material_id) DO UPDATE SET
                expert_id=excluded.expert_id,
                title=excluded.title,
                source_url=excluded.source_url,
                media_type=excluded.media_type,
                published_at=excluded.published_at,
                retrieved_at=excluded.retrieved_at,
                note=excluded.note,
                updated_at=excluded.updated_at
            """,
            [
                material_id,
                expert_id,
                title,
                source_url,
                media_type,
                published_at,
                retrieved_at,
                note,
                now,
                now,
            ],
        )
        return material_id

    def link_case_material(self, payload: dict[str, Any]) -> str:
        case_id = _clean(
            payload.get("case_id"), field_name="case_id", limit=180, required=True
        )
        material_id = _clean(
            payload.get("material_id"),
            field_name="material_id",
            limit=180,
            required=True,
        )
        self._require("expert_cases", "case_id", case_id, entity="expert case")
        self._require(
            "expert_materials", "material_id", material_id, entity="expert material"
        )
        existing = self.conn.execute(
            """
            SELECT link_id FROM expert_case_materials
            WHERE case_id = ? AND material_id = ?
            """,
            [case_id, material_id],
        ).fetchone()
        if existing:
            return str(existing[0])
        link_id = _clean(
            payload.get("link_id") or uuid4(),
            field_name="link_id",
            limit=180,
            required=True,
        )
        self.conn.execute(
            """
            INSERT INTO expert_case_materials(link_id, case_id, material_id, created_at)
            VALUES (?, ?, ?, ?)
            """,
            [link_id, case_id, material_id, _now()],
        )
        return link_id

    def _require_case_material_link(self, case_id: str, material_id: str) -> None:
        row = self.conn.execute(
            """
            SELECT 1 FROM expert_case_materials
            WHERE case_id = ? AND material_id = ? LIMIT 1
            """,
            [case_id, material_id],
        ).fetchone()
        if row is None:
            raise ExpertCorpusValidationError(
                "Материал должен быть явно связан с case до добавления claim"
            )

    def upsert_claim(self, payload: dict[str, Any]) -> str:
        _reject_full_text(payload, entity="expert claim")
        claim_id = _clean(
            payload.get("claim_id"), field_name="claim_id", limit=180, required=True
        )
        case_id = _clean(
            payload.get("case_id"), field_name="case_id", limit=180, required=True
        )
        material_id = _clean(
            payload.get("material_id"),
            field_name="material_id",
            limit=180,
            required=True,
        )
        self._require("expert_cases", "case_id", case_id, entity="expert case")
        self._require(
            "expert_materials", "material_id", material_id, entity="expert material"
        )
        self._require_case_material_link(case_id, material_id)

        dimension = str(payload.get("dimension") or "").strip()
        if dimension not in EXPERT_DIMENSIONS:
            raise ExpertCorpusValidationError(f"Неизвестный dimension: {dimension!r}")
        change_type = str(payload.get("change_type") or "unknown").strip()
        if change_type not in EXPERT_CHANGE_TYPES:
            raise ExpertCorpusValidationError(f"Неизвестный change_type: {change_type!r}")

        timecode_or_section = _clean(
            payload.get("timecode_or_section"),
            field_name="timecode_or_section",
            limit=500,
            required=True,
        )
        claim_summary = _clean(
            payload.get("claim_summary"),
            field_name="claim_summary",
            limit=2000,
            required=True,
        )
        observation = _clean(
            payload.get("observation"),
            field_name="observation",
            limit=3000,
            required=True,
        )
        structural_consequence = _clean(
            payload.get("structural_consequence"),
            field_name="structural_consequence",
            limit=3000,
            required=True,
        )
        expert_interpretation = _clean(
            payload.get("expert_interpretation"),
            field_name="expert_interpretation",
            limit=3000,
            required=True,
        )
        confidence = _confidence(payload.get("confidence", 0.5))
        story_map_id = _clean(
            payload.get("story_map_id"), field_name="story_map_id", limit=180
        ) or None
        story_diff_annotation_id = _clean(
            payload.get("story_diff_annotation_id"),
            field_name="story_diff_annotation_id",
            limit=180,
        ) or None
        tags_json = _json_list(payload.get("tags") or [], field_name="tags", item_limit=100)
        now = _now()
        self.conn.execute(
            """
            INSERT INTO expert_claims(
                claim_id, case_id, material_id, dimension, change_type,
                timecode_or_section, claim_summary, observation,
                structural_consequence, expert_interpretation, confidence,
                story_map_id, story_diff_annotation_id, tags_json,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(claim_id) DO UPDATE SET
                case_id=excluded.case_id,
                material_id=excluded.material_id,
                dimension=excluded.dimension,
                change_type=excluded.change_type,
                timecode_or_section=excluded.timecode_or_section,
                claim_summary=excluded.claim_summary,
                observation=excluded.observation,
                structural_consequence=excluded.structural_consequence,
                expert_interpretation=excluded.expert_interpretation,
                confidence=excluded.confidence,
                story_map_id=excluded.story_map_id,
                story_diff_annotation_id=excluded.story_diff_annotation_id,
                tags_json=excluded.tags_json,
                updated_at=excluded.updated_at
            """,
            [
                claim_id,
                case_id,
                material_id,
                dimension,
                change_type,
                timecode_or_section,
                claim_summary,
                observation,
                structural_consequence,
                expert_interpretation,
                confidence,
                story_map_id,
                story_diff_annotation_id,
                tags_json,
                now,
                now,
            ],
        )
        return claim_id

    def upsert_evidence(self, payload: dict[str, Any]) -> str:
        _reject_full_text(payload, entity="expert evidence")
        evidence_id = _clean(
            payload.get("evidence_id") or uuid4(),
            field_name="evidence_id",
            limit=180,
            required=True,
        )
        claim_id = _clean(
            payload.get("claim_id"), field_name="claim_id", limit=180, required=True
        )
        self._require("expert_claims", "claim_id", claim_id, entity="expert claim")
        polarity = str(payload.get("polarity") or "supporting").strip().casefold()
        if polarity not in EVIDENCE_POLARITIES:
            raise ExpertCorpusValidationError(
                "evidence polarity должен быть supporting или contradicting"
            )
        evidence_kind = str(payload.get("evidence_kind") or "other").strip().casefold()
        if evidence_kind not in EVIDENCE_KINDS:
            raise ExpertCorpusValidationError(
                "Неизвестный evidence_kind: " + evidence_kind
            )
        description = _clean(
            payload.get("description"),
            field_name="evidence.description",
            limit=2000,
            required=True,
        )
        locator = _clean(
            payload.get("locator"), field_name="evidence.locator", limit=700
        ) or None
        reference_id = _clean(
            payload.get("reference_id"),
            field_name="evidence.reference_id",
            limit=300,
        ) or None
        confidence = _confidence(payload.get("confidence", 0.7))
        if evidence_kind in {"storymap", "storydiff", "production_context"} and not reference_id:
            raise ExpertCorpusValidationError(
                f"evidence_kind={evidence_kind} требует reference_id"
            )
        now = _now()
        self.conn.execute(
            """
            INSERT INTO expert_evidence(
                evidence_id, claim_id, polarity, evidence_kind, description,
                locator, reference_id, confidence, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(evidence_id) DO UPDATE SET
                claim_id=excluded.claim_id,
                polarity=excluded.polarity,
                evidence_kind=excluded.evidence_kind,
                description=excluded.description,
                locator=excluded.locator,
                reference_id=excluded.reference_id,
                confidence=excluded.confidence,
                updated_at=excluded.updated_at
            """,
            [
                evidence_id,
                claim_id,
                polarity,
                evidence_kind,
                description,
                locator,
                reference_id,
                confidence,
                now,
                now,
            ],
        )
        return evidence_id

    def claim_chain(self, claim_id: str) -> dict[str, Any]:
        claim_id = _clean(
            claim_id, field_name="claim_id", limit=180, required=True
        )
        row = self.conn.execute(
            """
            SELECT
                c.claim_id, c.case_id, c.material_id,
                m.expert_id, p.display_name, m.source_url,
                c.dimension, c.change_type, c.timecode_or_section,
                c.claim_summary, c.observation, c.structural_consequence,
                c.expert_interpretation, c.confidence,
                c.story_map_id, c.story_diff_annotation_id,
                c.tags_json
            FROM expert_claims c
            JOIN expert_materials m ON m.material_id = c.material_id
            JOIN expert_profiles p ON p.expert_id = m.expert_id
            WHERE c.claim_id = ?
            """,
            [claim_id],
        ).fetchone()
        if row is None:
            raise ExpertCorpusValidationError(f"Неизвестный expert claim: {claim_id}")

        evidence_rows = self.conn.execute(
            """
            SELECT evidence_id, polarity, evidence_kind, description,
                   locator, reference_id, confidence
            FROM expert_evidence
            WHERE claim_id = ?
            ORDER BY polarity, evidence_id
            """,
            [claim_id],
        ).fetchall()
        evidence = [
            {
                "evidence_id": str(item[0]),
                "polarity": str(item[1]),
                "evidence_kind": str(item[2]),
                "description": str(item[3]),
                "locator": item[4],
                "reference_id": item[5],
                "confidence": float(item[6]),
            }
            for item in evidence_rows
        ]
        return {
            "claim_id": str(row[0]),
            "case_id": str(row[1]),
            "material_id": str(row[2]),
            "expert_id": str(row[3]),
            "expert_display_name": str(row[4]),
            "source_url": str(row[5]),
            "dimension": str(row[6]),
            "change_type": str(row[7]),
            "timecode_or_section": str(row[8]),
            "claim": str(row[9]),
            "evidence": [item for item in evidence if item["polarity"] == "supporting"],
            "contradicting_evidence": [
                item for item in evidence if item["polarity"] == "contradicting"
            ],
            "observation": str(row[10]),
            "structural_consequence": str(row[11]),
            "expert_interpretation": str(row[12]),
            "confidence": float(row[13]),
            "story_map_id": row[14],
            "story_diff_annotation_id": row[15],
            "tags": json.loads(row[16] or "[]"),
        }

    def case_summary(self, case_id: str) -> dict[str, Any]:
        case_id = _clean(case_id, field_name="case_id", limit=180, required=True)
        case = self.conn.execute(
            """
            SELECT case_id, imdb_id, film_title, film_year, source_work_id, split
            FROM expert_cases WHERE case_id = ?
            """,
            [case_id],
        ).fetchone()
        if case is None:
            raise ExpertCorpusValidationError(f"Неизвестный expert case: {case_id}")

        rows = self.conn.execute(
            """
            SELECT
                p.expert_id,
                c.dimension,
                c.change_type,
                COUNT(*) AS claim_count,
                SUM(CASE WHEN e.polarity = 'supporting' THEN 1 ELSE 0 END) AS support_count,
                SUM(CASE WHEN e.polarity = 'contradicting' THEN 1 ELSE 0 END) AS contradict_count
            FROM expert_claims c
            JOIN expert_materials m ON m.material_id = c.material_id
            JOIN expert_profiles p ON p.expert_id = m.expert_id
            LEFT JOIN expert_evidence e ON e.claim_id = c.claim_id
            WHERE c.case_id = ?
            GROUP BY p.expert_id, c.dimension, c.change_type
            ORDER BY p.expert_id, c.dimension, c.change_type
            """,
            [case_id],
        ).fetchall()

        profiles = sorted({str(row[0]) for row in rows})
        return {
            "case_id": str(case[0]),
            "imdb_id": case[1],
            "film_title": str(case[2]),
            "film_year": case[3],
            "source_work_id": case[4],
            "split": str(case[5]),
            "expert_profiles": profiles,
            "rows": [
                {
                    "expert_id": str(row[0]),
                    "dimension": str(row[1]),
                    "change_type": str(row[2]),
                    "claim_count": int(row[3]),
                    "supporting_evidence_count": int(row[4] or 0),
                    "contradicting_evidence_count": int(row[5] or 0),
                }
                for row in rows
            ],
        }

    def corpus_stats(self) -> dict[str, Any]:
        counts = {}
        for key, table in (
            ("profiles", "expert_profiles"),
            ("cases", "expert_cases"),
            ("materials", "expert_materials"),
            ("claims", "expert_claims"),
            ("evidence", "expert_evidence"),
        ):
            counts[key] = int(self.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])

        split_rows = self.conn.execute(
            """
            SELECT split, COUNT(*)
            FROM expert_cases
            GROUP BY split
            ORDER BY split
            """
        ).fetchall()
        expert_rows = self.conn.execute(
            """
            SELECT p.expert_id, COUNT(DISTINCT c.claim_id)
            FROM expert_profiles p
            LEFT JOIN expert_materials m ON m.expert_id = p.expert_id
            LEFT JOIN expert_claims c ON c.material_id = m.material_id
            GROUP BY p.expert_id
            ORDER BY p.expert_id
            """
        ).fetchall()
        return {
            **counts,
            "cases_by_split": {str(row[0]): int(row[1]) for row in split_rows},
            "claims_by_expert": {str(row[0]): int(row[1]) for row in expert_rows},
        }
