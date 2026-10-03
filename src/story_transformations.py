from __future__ import annotations

import hashlib
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Any, Iterable

from src.story_diff import NODE_DIMENSIONS, StoryMap


TRANSFORMATION_METHOD = "aligned-story-transformations"
TRANSFORMATION_VERSION = "1"
TONE_METHOD = "lexical-tone-profile"
TONE_VERSION = "1"

_WORD_RE = re.compile(r"[A-Za-zА-Яа-яЁё0-9'-]+", re.UNICODE)

# Это не sentiment score. Категории описывают только наличие явных лексических
# маркеров в summary и нужны как воспроизводимый research baseline для tone.
_TONE_MARKERS: dict[str, dict[str, tuple[str, ...]]] = {
    "en": {
        "threat": ("danger", "threat", "attack", "war", "kill", "death", "enemy", "fear"),
        "loss": ("loss", "grief", "mourning", "dies", "died", "dead", "tragedy", "sacrifice"),
        "hope": ("hope", "heals", "healing", "reconcile", "reunite", "save", "rescue", "peace"),
        "humor": ("comic", "comedy", "joke", "jokes", "funny", "humorous", "laugh", "laughter"),
        "affection": ("love", "loves", "friendship", "friend", "family", "kiss", "romance", "romantic"),
    },
    "ru": {
        "threat": ("опасность", "угроза", "нападение", "война", "убить", "смерть", "враг", "страх"),
        "loss": ("потеря", "горе", "траур", "умирает", "погибает", "погиб", "трагедия", "жертва"),
        "hope": ("надежда", "исцеление", "примиряется", "воссоединяется", "спасает", "спасти", "мир"),
        "humor": ("комедия", "шутка", "шутки", "смешной", "юмор", "юмористический", "смех"),
        "affection": ("любовь", "любит", "дружба", "друг", "семья", "поцелуй", "роман", "романтический"),
    },
}


def _stable_id(*parts: str) -> str:
    raw = "\x1f".join(parts).encode("utf-8")
    return "auto-" + hashlib.sha1(raw).hexdigest()[:20]


def _clamp(value: float) -> float:
    return round(max(0.0, min(1.0, float(value))), 4)


def _annotation(
    *,
    source: StoryMap,
    adaptation: StoryMap,
    key: str,
    dimension: str,
    change_type: str,
    claim: str,
    severity: float,
    confidence: float,
    tags: Iterable[str],
) -> dict[str, Any]:
    return {
        "annotation_id": _stable_id(
            source.map_id,
            adaptation.map_id,
            TRANSFORMATION_METHOD,
            TRANSFORMATION_VERSION,
            change_type,
            key,
        ),
        "layer": "structural_consequence",
        "dimension": dimension,
        "change_type": change_type,
        "claim": claim,
        "severity": _clamp(severity),
        "confidence": _clamp(confidence),
        "polarity": 0.0,
        "tags": sorted(set(tags) | {"story_transform", TRANSFORMATION_METHOD}),
    }


def _representation(source: StoryMap, adaptation: StoryMap) -> tuple[dict[str, str], dict[str, tuple[str, ...]]]:
    """Возвращает one-to-one source->adaptation и adaptation->source keys.

    Many-to-one намеренно не попадает в one-to-one map: такие случаи анализируются
    как compression и не используются для rewrite/reorder, чтобы не смешивать
    разные виды преобразований.
    """
    source_keys = {node.key for node in source.nodes}
    source_to_adaptation: dict[str, str] = {}
    adaptation_sources: dict[str, tuple[str, ...]] = {}

    for node in adaptation.nodes:
        mapped = tuple(dict.fromkeys(key for key in node.maps_from if key in source_keys))
        if not mapped and node.key in source_keys:
            mapped = (node.key,)
        adaptation_sources[node.key] = mapped
        if len(mapped) == 1 and mapped[0] not in source_to_adaptation:
            source_to_adaptation[mapped[0]] = node.key

    return source_to_adaptation, adaptation_sources


class StoryTransformationAnalyzer:
    """Детерминированные преобразования только поверх подтверждённого alignment."""

    method = TRANSFORMATION_METHOD
    version = TRANSFORMATION_VERSION

    @classmethod
    def compare(cls, source: StoryMap, adaptation: StoryMap) -> dict[str, Any]:
        source_nodes = {node.key: node for node in source.nodes}
        adaptation_nodes = {node.key: node for node in adaptation.nodes}
        source_to_adaptation, adaptation_sources = _representation(source, adaptation)
        annotations: list[dict[str, Any]] = []

        # 1) Compression: подтверждённое many-to-one maps_from.
        for adaptation_node in adaptation.nodes:
            source_keys = adaptation_sources.get(adaptation_node.key, ())
            compatible = [
                source_nodes[key]
                for key in source_keys
                if key in source_nodes and source_nodes[key].kind == adaptation_node.kind
            ]
            if len(compatible) < 2:
                continue
            max_importance = max(node.importance for node in compatible)
            confidence = min(
                [adaptation_node.confidence] + [node.confidence for node in compatible]
            )
            annotations.append(
                _annotation(
                    source=source,
                    adaptation=adaptation,
                    key=adaptation_node.key,
                    dimension=NODE_DIMENSIONS[adaptation_node.kind],
                    change_type="compressed",
                    claim=(
                        f"В элементе экранизации «{adaptation_node.label}» объединены "
                        f"{len(compatible)} подтверждённых элемента первоисточника."
                    ),
                    severity=max_importance * min(1.0, len(compatible) / 3.0),
                    confidence=confidence,
                    tags=["many_to_one", adaptation_node.kind],
                )
            )

        # 2) Rewrite: сравниваем relation-neighborhood только для one-to-one nodes.
        reverse = {adaptation_key: source_key for source_key, adaptation_key in source_to_adaptation.items()}
        source_relations = {
            (relation.kind, relation.source, relation.target)
            for relation in source.relations
            if relation.source in source_to_adaptation and relation.target in source_to_adaptation
        }
        adaptation_relations: set[tuple[str, str, str]] = set()
        for relation in adaptation.relations:
            canonical_source = reverse.get(relation.source)
            canonical_target = reverse.get(relation.target)
            if canonical_source and canonical_target:
                adaptation_relations.add((relation.kind, canonical_source, canonical_target))

        source_incident: dict[str, set[tuple[str, str, str]]] = defaultdict(set)
        adaptation_incident: dict[str, set[tuple[str, str, str]]] = defaultdict(set)
        for signature in source_relations:
            source_incident[signature[1]].add(signature)
            source_incident[signature[2]].add(signature)
        for signature in adaptation_relations:
            adaptation_incident[signature[1]].add(signature)
            adaptation_incident[signature[2]].add(signature)

        for source_key, adaptation_key in sorted(source_to_adaptation.items()):
            before = source_incident.get(source_key, set())
            after = adaptation_incident.get(source_key, set())
            # Не называем отсутствие всех связей rewrite: это уже покрывается relation loss.
            if not before or not after or before == after:
                continue
            union = before | after
            changed_ratio = len(before ^ after) / len(union) if union else 0.0
            if changed_ratio < 0.5:
                continue
            source_node = source_nodes[source_key]
            adaptation_node = adaptation_nodes[adaptation_key]
            annotations.append(
                _annotation(
                    source=source,
                    adaptation=adaptation,
                    key=source_key,
                    dimension=NODE_DIMENSIONS[source_node.kind],
                    change_type="rewritten",
                    claim=(
                        f"Структурные связи элемента «{source_node.label}» существенно "
                        "изменились в подтверждённом one-to-one представлении экранизации."
                    ),
                    severity=changed_ratio * max(source_node.importance, adaptation_node.importance),
                    confidence=min(source_node.confidence, adaptation_node.confidence, 0.85),
                    tags=["relation_neighborhood_changed", source_node.kind],
                )
            )

        # 3) Reorder: сравниваем относительный порядок только подтверждённых one-to-one events.
        source_event_order = [node.key for node in source.nodes if node.kind == "event"]
        adaptation_event_order = [node.key for node in adaptation.nodes if node.kind == "event"]
        source_rank = {key: index for index, key in enumerate(source_event_order)}
        adaptation_rank = {key: index for index, key in enumerate(adaptation_event_order)}
        aligned_events = [
            source_key
            for source_key in source_event_order
            if source_key in source_to_adaptation
            and source_nodes[source_key].kind == "event"
            and adaptation_nodes[source_to_adaptation[source_key]].kind == "event"
        ]
        if len(aligned_events) >= 3:
            inversion_counts: Counter[str] = Counter()
            for index, left in enumerate(aligned_events):
                for right in aligned_events[index + 1 :]:
                    source_delta = source_rank[left] - source_rank[right]
                    adaptation_delta = (
                        adaptation_rank[source_to_adaptation[left]]
                        - adaptation_rank[source_to_adaptation[right]]
                    )
                    if source_delta * adaptation_delta < 0:
                        inversion_counts[left] += 1
                        inversion_counts[right] += 1

            denominator = max(1, len(aligned_events) - 1)
            for source_key, count in sorted(inversion_counts.items()):
                source_node = source_nodes[source_key]
                adaptation_node = adaptation_nodes[source_to_adaptation[source_key]]
                ratio = count / denominator
                annotations.append(
                    _annotation(
                        source=source,
                        adaptation=adaptation,
                        key=source_key,
                        dimension="plot",
                        change_type="reordered",
                        claim=(
                            f"Относительный порядок события «{source_node.label}» изменён "
                            "среди подтверждённых сопоставленных событий."
                        ),
                        severity=ratio * max(source_node.importance, adaptation_node.importance),
                        confidence=min(source_node.confidence, adaptation_node.confidence, 0.8),
                        tags=["event_order_inversion"],
                    )
                )

        return {
            "source_map_id": source.map_id,
            "adaptation_map_id": adaptation.map_id,
            "method": cls.method,
            "method_version": cls.version,
            "research_only": True,
            "alignment_required": True,
            "annotations": annotations,
            "summary": {
                "compressed": sum(item["change_type"] == "compressed" for item in annotations),
                "rewritten": sum(item["change_type"] == "rewritten" for item in annotations),
                "reordered": sum(item["change_type"] == "reordered" for item in annotations),
            },
        }


@dataclass(frozen=True)
class LexicalToneProfile:
    language: str
    text_sha256: str
    word_count: int
    marker_counts: dict[str, int]
    marker_rates_per_100_words: dict[str, float]
    matched_word_ratio: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "method": TONE_METHOD,
            "method_version": TONE_VERSION,
            "language": self.language,
            "text_sha256": self.text_sha256,
            "word_count": self.word_count,
            "marker_counts": dict(self.marker_counts),
            "marker_rates_per_100_words": dict(self.marker_rates_per_100_words),
            "matched_word_ratio": self.matched_word_ratio,
            "research_only": True,
            "quality_sign": None,
        }


class LexicalToneAnalyzer:
    """Языково-нормализованный lexical baseline без общей оценки тона/качества."""

    method = TONE_METHOD
    version = TONE_VERSION

    @classmethod
    def profile(cls, text: str, *, language: str) -> LexicalToneProfile:
        language = str(language or "").strip().lower()
        if language not in _TONE_MARKERS:
            raise ValueError("language должен быть ru или en")
        raw = str(text or "").strip()
        if not raw:
            raise ValueError("text не должен быть пустым")

        tokens = [token.lower() for token in _WORD_RE.findall(raw)]
        counts = Counter(tokens)
        word_count = len(tokens)
        marker_counts: dict[str, int] = {}
        marker_rates: dict[str, float] = {}
        matched_total = 0
        for category, markers in _TONE_MARKERS[language].items():
            count = sum(counts[marker] for marker in markers)
            marker_counts[category] = count
            marker_rates[category] = round((count / word_count * 100.0) if word_count else 0.0, 4)
            matched_total += count

        return LexicalToneProfile(
            language=language,
            text_sha256=hashlib.sha256(raw.encode("utf-8")).hexdigest(),
            word_count=word_count,
            marker_counts=marker_counts,
            marker_rates_per_100_words=marker_rates,
            matched_word_ratio=round((matched_total / word_count) if word_count else 0.0, 4),
        )

    @classmethod
    def compare(cls, source: LexicalToneProfile, adaptation: LexicalToneProfile) -> dict[str, Any]:
        categories = sorted(set(source.marker_rates_per_100_words) | set(adaptation.marker_rates_per_100_words))
        deltas = {
            category: round(
                adaptation.marker_rates_per_100_words.get(category, 0.0)
                - source.marker_rates_per_100_words.get(category, 0.0),
                4,
            )
            for category in categories
        }
        l1_distance = round(sum(abs(value) for value in deltas.values()), 4)
        return {
            "method": cls.method,
            "method_version": cls.version,
            "research_only": True,
            "quality_sign": None,
            "source": source.as_dict(),
            "adaptation": adaptation.as_dict(),
            "category_rate_delta_per_100_words": deltas,
            "l1_distance_per_100_words": l1_distance,
            "coverage_warning": (
                source.matched_word_ratio < 0.01 or adaptation.matched_word_ratio < 0.01
            ),
        }
