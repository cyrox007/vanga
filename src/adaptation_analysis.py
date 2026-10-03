from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable
from uuid import uuid4

import duckdb

from settings import config


LAYERS = {
    "observation",
    "structural_consequence",
    "expert_interpretation",
}

DIMENSIONS = {
    "plot",
    "characters",
    "motivation",
    "relationships",
    "worldbuilding",
    "themes",
    "tone",
    "causal_coherence",
    "ending",
    "format",
}

CHANGE_TYPES = {
    "preserved",
    "removed",
    "added",
    "merged",
    "rewritten",
    "reordered",
    "compressed",
    "expanded",
    "unknown",
}

SOURCE_KINDS = {
    "film_summary",
    "source_summary",
    "expert_review",
    "other",
}


class AdaptationValidationError(ValueError):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _bounded_float(value, *, name: str, low: float, high: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise AdaptationValidationError(f"{name} должен быть числом") from exc
    if not low <= parsed <= high:
        raise AdaptationValidationError(
            f"{name} должен быть в диапазоне {low}..{high}"
        )
    return parsed


def _clean_text(value, *, limit: int, field_name: str, required: bool = False) -> str:
    text = " ".join(str(value or "").strip().split())
    if required and not text:
        raise AdaptationValidationError(f"{field_name} не должен быть пустым")
    if len(text) > limit:
        raise AdaptationValidationError(
            f"{field_name} длиннее допустимых {limit} символов"
        )
    return text


def _text_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class AdaptationSource:
    """Ссылка на материал без обязательного хранения полного авторского текста."""

    source_id: str
    kind: str
    language: str | None = None
    title: str | None = None
    creator: str | None = None
    url: str | None = None
    locator: str | None = None
    text: str | None = None
    copyright_mode: str = "reference"

    @classmethod
    def from_dict(cls, payload: dict) -> "AdaptationSource":
        kind = str(payload.get("kind") or "").strip()
        if kind not in SOURCE_KINDS:
            raise AdaptationValidationError(
                f"Неизвестный kind источника: {kind!r}"
            )

        source_id = _clean_text(
            payload.get("source_id") or uuid4(),
            limit=160,
            field_name="source_id",
            required=True,
        )
        language = _clean_text(
            payload.get("language"),
            limit=16,
            field_name="language",
        ) or None
        title = _clean_text(
            payload.get("title"),
            limit=500,
            field_name="title",
        ) or None
        creator = _clean_text(
            payload.get("creator"),
            limit=300,
            field_name="creator",
        ) or None
        url = _clean_text(
            payload.get("url"),
            limit=2000,
            field_name="url",
        ) or None
        locator = _clean_text(
            payload.get("locator"),
            limit=500,
            field_name="locator",
        ) or None

        copyright_mode = str(
            payload.get("copyright_mode") or "reference"
        ).strip()
        if copyright_mode not in {"reference", "summary", "licensed_text"}:
            raise AdaptationValidationError(
                "copyright_mode должен быть reference, summary или licensed_text"
            )

        text = payload.get("text")
        if text is not None:
            text = str(text).strip()
            if len(text) > 100_000:
                raise AdaptationValidationError(
                    "Текст источника слишком большой для prototype ingestion"
                )
            if kind == "expert_review" and copyright_mode == "reference":
                raise AdaptationValidationError(
                    "Для expert_review в режиме reference полный text хранить нельзя; "
                    "сохраняйте URL/таймкод и собственную аннотацию"
                )

        return cls(
            source_id=source_id,
            kind=kind,
            language=language,
            title=title,
            creator=creator,
            url=url,
            locator=locator,
            text=text,
            copyright_mode=copyright_mode,
        )


@dataclass(frozen=True)
class AdaptationAnnotation:
    """Одна единица разметки: факт, структурное следствие или мнение эксперта."""

    annotation_id: str
    layer: str
    dimension: str
    change_type: str
    claim: str
    severity: float
    confidence: float
    polarity: float
    source_id: str | None = None
    evidence_locator: str | None = None
    parent_id: str | None = None
    tags: tuple[str, ...] = field(default_factory=tuple)

    @classmethod
    def from_dict(cls, payload: dict) -> "AdaptationAnnotation":
        layer = str(payload.get("layer") or "").strip()
        dimension = str(payload.get("dimension") or "").strip()
        change_type = str(payload.get("change_type") or "unknown").strip()
        if layer not in LAYERS:
            raise AdaptationValidationError(f"Неизвестный layer: {layer!r}")
        if dimension not in DIMENSIONS:
            raise AdaptationValidationError(
                f"Неизвестный dimension: {dimension!r}"
            )
        if change_type not in CHANGE_TYPES:
            raise AdaptationValidationError(
                f"Неизвестный change_type: {change_type!r}"
            )

        claim = _clean_text(
            payload.get("claim"),
            limit=4000,
            field_name="claim",
            required=True,
        )
        severity = _bounded_float(
            payload.get("severity", 0.5),
            name="severity",
            low=0.0,
            high=1.0,
        )
        confidence = _bounded_float(
            payload.get("confidence", 0.5),
            name="confidence",
            low=0.0,
            high=1.0,
        )
        polarity = _bounded_float(
            payload.get("polarity", 0.0),
            name="polarity",
            low=-1.0,
            high=1.0,
        )

        tags = tuple(
            sorted(
                {
                    _clean_text(
                        value,
                        limit=80,
                        field_name="tag",
                        required=True,
                    )
                    for value in payload.get("tags") or []
                }
            )
        )

        return cls(
            annotation_id=_clean_text(
                payload.get("annotation_id") or uuid4(),
                limit=160,
                field_name="annotation_id",
                required=True,
            ),
            layer=layer,
            dimension=dimension,
            change_type=change_type,
            claim=claim,
            severity=severity,
            confidence=confidence,
            polarity=polarity,
            source_id=(
                _clean_text(
                    payload.get("source_id"),
                    limit=160,
                    field_name="source_id",
                )
                or None
            ),
            evidence_locator=(
                _clean_text(
                    payload.get("evidence_locator"),
                    limit=500,
                    field_name="evidence_locator",
                )
                or None
            ),
            parent_id=(
                _clean_text(
                    payload.get("parent_id"),
                    limit=160,
                    field_name="parent_id",
                )
                or None
            ),
            tags=tags,
        )


@dataclass(frozen=True)
class AdaptationCase:
    case_id: str
    imdb_id: str
    film_title: str
    film_year: int | None
    source_work_id: str | None
    source_title: str | None
    source_type: str | None
    adaptation_format: str
    sources: tuple[AdaptationSource, ...]
    annotations: tuple[AdaptationAnnotation, ...]

    @classmethod
    def from_dict(cls, payload: dict) -> "AdaptationCase":
        imdb_id = _clean_text(
            payload.get("imdb_id"),
            limit=16,
            field_name="imdb_id",
            required=True,
        )
        if not imdb_id.startswith("tt") or not imdb_id[2:].isdigit():
            raise AdaptationValidationError("imdb_id должен иметь вид tt1234567")

        raw_year = payload.get("film_year")
        film_year = None
        if raw_year not in {None, ""}:
            try:
                film_year = int(raw_year)
            except (TypeError, ValueError) as exc:
                raise AdaptationValidationError("film_year должен быть числом") from exc
            if not 1888 <= film_year <= 2100:
                raise AdaptationValidationError("film_year вне допустимого диапазона")

        sources = tuple(
            AdaptationSource.from_dict(item)
            for item in (payload.get("sources") or [])
        )
        annotations = tuple(
            AdaptationAnnotation.from_dict(item)
            for item in (payload.get("annotations") or [])
        )

        source_ids = {item.source_id for item in sources}
        annotation_ids = {item.annotation_id for item in annotations}
        if len(source_ids) != len(sources):
            raise AdaptationValidationError("source_id должны быть уникальны")
        if len(annotation_ids) != len(annotations):
            raise AdaptationValidationError("annotation_id должны быть уникальны")

        for annotation in annotations:
            if annotation.source_id and annotation.source_id not in source_ids:
                raise AdaptationValidationError(
                    f"annotation {annotation.annotation_id}: неизвестный source_id"
                )
            if annotation.parent_id and annotation.parent_id not in annotation_ids:
                raise AdaptationValidationError(
                    f"annotation {annotation.annotation_id}: неизвестный parent_id"
                )
            if annotation.parent_id == annotation.annotation_id:
                raise AdaptationValidationError(
                    f"annotation {annotation.annotation_id} ссылается сама на себя"
                )

        return cls(
            case_id=_clean_text(
                payload.get("case_id") or f"{imdb_id}:default",
                limit=180,
                field_name="case_id",
                required=True,
            ),
            imdb_id=imdb_id,
            film_title=_clean_text(
                payload.get("film_title"),
                limit=500,
                field_name="film_title",
                required=True,
            ),
            film_year=film_year,
            source_work_id=(
                _clean_text(
                    payload.get("source_work_id"),
                    limit=160,
                    field_name="source_work_id",
                )
                or None
            ),
            source_title=(
                _clean_text(
                    payload.get("source_title"),
                    limit=500,
                    field_name="source_title",
                )
                or None
            ),
            source_type=(
                _clean_text(
                    payload.get("source_type"),
                    limit=80,
                    field_name="source_type",
                )
                or None
            ),
            adaptation_format=_clean_text(
                payload.get("adaptation_format") or "film",
                limit=80,
                field_name="adaptation_format",
                required=True,
            ),
            sources=sources,
            annotations=annotations,
        )


class AdaptationAnalyzer:
    """Строит только ретроспективные признаки.

    Все ключи намеренно начинаются с ``retro_adapt_``. Такой namespace не
    должен попадать в pre-release training Vanga: признаки описывают уже
    готовую экранизацию и экспертные наблюдения после релиза.
    """

    @staticmethod
    def _weighted_average(
        items: Iterable[AdaptationAnnotation],
        value_getter,
    ) -> float:
        numerator = 0.0
        denominator = 0.0
        for item in items:
            weight = max(0.05, item.confidence)
            numerator += float(value_getter(item)) * weight
            denominator += weight
        return numerator / denominator if denominator else 0.0

    @classmethod
    def features(cls, case: AdaptationCase) -> dict[str, float | int]:
        annotations = list(case.annotations)
        structural = [
            item
            for item in annotations
            if item.layer in {"observation", "structural_consequence"}
        ]
        expert = [
            item for item in annotations if item.layer == "expert_interpretation"
        ]

        result: dict[str, float | int] = {
            "retro_adapt_annotation_count": len(annotations),
            "retro_adapt_structural_count": len(structural),
            "retro_adapt_expert_count": len(expert),
        }

        for dimension in sorted(DIMENSIONS):
            dimension_structural = [
                item for item in structural if item.dimension == dimension
            ]
            dimension_expert = [
                item for item in expert if item.dimension == dimension
            ]
            result[f"retro_adapt_{dimension}_severity"] = round(
                cls._weighted_average(
                    dimension_structural,
                    lambda item: item.severity,
                ),
                4,
            )
            result[f"retro_adapt_{dimension}_expert_polarity"] = round(
                cls._weighted_average(
                    dimension_expert,
                    lambda item: item.polarity,
                ),
                4,
            )

        for change_type in sorted(CHANGE_TYPES - {"unknown"}):
            result[f"retro_adapt_change_{change_type}_count"] = sum(
                1 for item in structural if item.change_type == change_type
            )

        structural_dimensions = {
            item.dimension for item in structural if item.confidence >= 0.5
        }
        result["retro_adapt_evidence_coverage"] = round(
            len(structural_dimensions) / len(DIMENSIONS),
            4,
        )
        result["retro_adapt_mean_structural_severity"] = round(
            cls._weighted_average(structural, lambda item: item.severity),
            4,
        )
        result["retro_adapt_mean_expert_polarity"] = round(
            cls._weighted_average(expert, lambda item: item.polarity),
            4,
        )

        annotation_by_id = {item.annotation_id: item for item in annotations}
        supported = 0
        for item in expert:
            parent = annotation_by_id.get(item.parent_id or "")
            if parent and parent.layer != "expert_interpretation":
                supported += 1
        result["retro_adapt_expert_structural_support_ratio"] = round(
            supported / len(expert) if expert else 0.0,
            4,
        )

        max_depth = 0
        for item in annotations:
            seen: set[str] = set()
            depth = 0
            current = item
            while current.parent_id and current.parent_id not in seen:
                seen.add(current.annotation_id)
                parent = annotation_by_id.get(current.parent_id)
                if parent is None:
                    break
                depth += 1
                current = parent
            max_depth = max(max_depth, depth)
        result["retro_adapt_causal_chain_depth"] = max_depth

        return result


class AdaptationAnalysisStore:
    """Отдельное retrospective-хранилище, физически отделённое от imdb.duckdb."""

    def __init__(self, path: str | Path | None = None) -> None:
        default_path = getattr(
            config,
            "ADAPTATION_DB_PATH",
            str(Path(config.ABSPATH) / "adaptation.duckdb"),
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
            CREATE TABLE IF NOT EXISTS adaptation_cases (
                case_id VARCHAR PRIMARY KEY,
                imdb_id VARCHAR NOT NULL,
                film_title VARCHAR NOT NULL,
                film_year INTEGER,
                source_work_id VARCHAR,
                source_title VARCHAR,
                source_type VARCHAR,
                adaptation_format VARCHAR NOT NULL,
                updated_at TIMESTAMP WITH TIME ZONE NOT NULL
            )
            """
        )
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS adaptation_sources (
                case_id VARCHAR NOT NULL,
                source_id VARCHAR NOT NULL,
                kind VARCHAR NOT NULL,
                language VARCHAR,
                title VARCHAR,
                creator VARCHAR,
                url VARCHAR,
                locator VARCHAR,
                text_hash VARCHAR,
                text_value VARCHAR,
                copyright_mode VARCHAR NOT NULL,
                PRIMARY KEY(case_id, source_id)
            )
            """
        )
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS adaptation_annotations (
                case_id VARCHAR NOT NULL,
                annotation_id VARCHAR NOT NULL,
                layer VARCHAR NOT NULL,
                dimension VARCHAR NOT NULL,
                change_type VARCHAR NOT NULL,
                claim VARCHAR NOT NULL,
                severity DOUBLE NOT NULL,
                confidence DOUBLE NOT NULL,
                polarity DOUBLE NOT NULL,
                source_id VARCHAR,
                evidence_locator VARCHAR,
                parent_id VARCHAR,
                tags_json VARCHAR NOT NULL,
                PRIMARY KEY(case_id, annotation_id)
            )
            """
        )
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS adaptation_feature_snapshots (
                case_id VARCHAR NOT NULL,
                feature_key VARCHAR NOT NULL,
                feature_value DOUBLE NOT NULL,
                calculated_at TIMESTAMP WITH TIME ZONE NOT NULL,
                PRIMARY KEY(case_id, feature_key)
            )
            """
        )

    def replace_case(self, case: AdaptationCase) -> dict[str, float | int]:
        now = _now()
        self.conn.execute("BEGIN TRANSACTION")
        try:
            self.conn.execute(
                """
                INSERT INTO adaptation_cases(
                    case_id, imdb_id, film_title, film_year,
                    source_work_id, source_title, source_type,
                    adaptation_format, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(case_id) DO UPDATE SET
                    imdb_id = excluded.imdb_id,
                    film_title = excluded.film_title,
                    film_year = excluded.film_year,
                    source_work_id = excluded.source_work_id,
                    source_title = excluded.source_title,
                    source_type = excluded.source_type,
                    adaptation_format = excluded.adaptation_format,
                    updated_at = excluded.updated_at
                """,
                [
                    case.case_id,
                    case.imdb_id,
                    case.film_title,
                    case.film_year,
                    case.source_work_id,
                    case.source_title,
                    case.source_type,
                    case.adaptation_format,
                    now,
                ],
            )
            self.conn.execute(
                "DELETE FROM adaptation_sources WHERE case_id = ?",
                [case.case_id],
            )
            self.conn.execute(
                "DELETE FROM adaptation_annotations WHERE case_id = ?",
                [case.case_id],
            )
            self.conn.execute(
                "DELETE FROM adaptation_feature_snapshots WHERE case_id = ?",
                [case.case_id],
            )

            for source in case.sources:
                self.conn.execute(
                    """
                    INSERT INTO adaptation_sources(
                        case_id, source_id, kind, language, title,
                        creator, url, locator, text_hash, text_value,
                        copyright_mode
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        case.case_id,
                        source.source_id,
                        source.kind,
                        source.language,
                        source.title,
                        source.creator,
                        source.url,
                        source.locator,
                        _text_hash(source.text) if source.text else None,
                        source.text,
                        source.copyright_mode,
                    ],
                )

            for annotation in case.annotations:
                self.conn.execute(
                    """
                    INSERT INTO adaptation_annotations(
                        case_id, annotation_id, layer, dimension,
                        change_type, claim, severity, confidence,
                        polarity, source_id, evidence_locator,
                        parent_id, tags_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        case.case_id,
                        annotation.annotation_id,
                        annotation.layer,
                        annotation.dimension,
                        annotation.change_type,
                        annotation.claim,
                        annotation.severity,
                        annotation.confidence,
                        annotation.polarity,
                        annotation.source_id,
                        annotation.evidence_locator,
                        annotation.parent_id,
                        json.dumps(annotation.tags, ensure_ascii=False),
                    ],
                )

            features = AdaptationAnalyzer.features(case)
            for key, value in features.items():
                self.conn.execute(
                    """
                    INSERT INTO adaptation_feature_snapshots(
                        case_id, feature_key, feature_value, calculated_at
                    ) VALUES (?, ?, ?, ?)
                    """,
                    [case.case_id, key, float(value), now],
                )

            self.conn.execute("COMMIT")
            return features
        except Exception:
            self.conn.execute("ROLLBACK")
            raise

    def load_features(self, case_id: str) -> dict[str, float]:
        rows = self.conn.execute(
            """
            SELECT feature_key, feature_value
            FROM adaptation_feature_snapshots
            WHERE case_id = ?
            ORDER BY feature_key
            """,
            [case_id],
        ).fetchall()
        return {str(key): float(value) for key, value in rows}


def load_case_seed_from_enrichment(
    imdb_id: str,
    *,
    enrichment_db_path: str | Path | None = None,
) -> dict:
    """Подготавливает case JSON из уже собранных RU/EN plot фильма.

    Первоисточник пока только связывается по Wikidata ``based_on``. Его RU/EN
    пересказы добавляются отдельным шагом: это не позволяет случайно принять
    описание фильма за описание книги/игры/комикса.
    """
    path = Path(enrichment_db_path or config.ENRICHMENT_DB_PATH)
    if not path.exists():
        raise FileNotFoundError(f"Enrichment DB не найдена: {path}")

    conn = duckdb.connect(str(path), read_only=True)
    try:
        row = conn.execute(
            """
            SELECT
                imdb_title,
                imdb_year,
                wikidata_json,
                enwiki_title,
                enwiki_revision,
                enwiki_plot,
                ruwiki_title,
                ruwiki_revision,
                ruwiki_plot
            FROM film_enrichment
            WHERE imdb_id = ?
            LIMIT 1
            """,
            [imdb_id],
        ).fetchone()
    finally:
        conn.close()

    if row is None:
        raise KeyError(f"{imdb_id} отсутствует в enrichment DB")

    try:
        structured = json.loads(row[2] or "{}")
    except json.JSONDecodeError:
        structured = {}
    based_on = structured.get("based_on") or []
    source_work_id = str(based_on[0]) if based_on else None

    sources: list[dict] = []
    if row[5]:
        sources.append(
            {
                "source_id": f"{imdb_id}:film:en",
                "kind": "film_summary",
                "language": "en",
                "title": row[3],
                "locator": (
                    f"Wikipedia revision {row[4]}" if row[4] else "Wikipedia"
                ),
                "text": row[5],
                "copyright_mode": "summary",
            }
        )
    if row[8]:
        sources.append(
            {
                "source_id": f"{imdb_id}:film:ru",
                "kind": "film_summary",
                "language": "ru",
                "title": row[6],
                "locator": (
                    f"Wikipedia revision {row[7]}" if row[7] else "Wikipedia"
                ),
                "text": row[8],
                "copyright_mode": "summary",
            }
        )

    return {
        "case_id": f"{imdb_id}:default",
        "imdb_id": imdb_id,
        "film_title": str(row[0] or imdb_id),
        "film_year": int(row[1]) if row[1] is not None else None,
        "source_work_id": source_work_id,
        "source_title": None,
        "source_type": None,
        "adaptation_format": "film",
        "sources": sources,
        "annotations": [],
    }
