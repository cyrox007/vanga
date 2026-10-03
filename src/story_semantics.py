from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any, Iterable

from src.adaptation_analysis import AdaptationValidationError
from src.story_diff import StoryMap
from src.story_extractor import RuleBasedStoryExtractor, StoryExtractionResult


SEMANTIC_METHOD = "rule-based-story-semantics"
SEMANTIC_VERSION = "1"

WORLD_MARKERS = {
    "ru": (
        "магия", "магический", "закон мира", "правило", "пророчество",
        "королевство", "империя", "орден", "ритуал", "проклятие",
        "технология", "система", "мир устроен",
    ),
    "en": (
        "magic", "magical", "law of the world", "rule", "prophecy",
        "kingdom", "empire", "order", "ritual", "curse", "technology",
        "system", "the world works",
    ),
}

THEME_MARKERS = {
    "ru": (
        "тема ", "темой ", "исследует тему", "посвящен теме", "посвящена теме",
        "рассказывает о теме",
    ),
    "en": (
        "theme of ", "the theme ", "explores the theme", "focuses on the theme",
        "is about the theme",
    ),
}

ENDING_MARKERS = {
    "ru": ("в финале", "в конце", "финал", "заканчивается", "под конец"),
    "en": ("in the end", "at the end", "finale", "ends with", "the ending"),
}

RELATIONSHIP_MARKERS = {
    "ru": (
        " брат", " сестр", " отец", " мать", " друг", " подруг", " враг",
        " соперник", " возлюб", " муж", " жена", " сын", " дочь", " наставник",
    ),
    "en": (
        " brother", " sister", " father", " mother", " friend", " enemy",
        " rival", " lover", " husband", " wife", " son", " daughter", " mentor",
    ),
}

COREFERENCE_PRONOUNS = {
    "ru": {"он", "она", "его", "ее", "её", "ей", "ему", "него", "нее", "неё"},
    "en": {"he", "she", "him", "her", "his", "hers"},
}

MOTIVATION_MARKERS = {
    "ru": (" чтобы ",),
    "en": (" in order to ",),
}

DEPENDENCY_MARKERS = {
    "ru": (" только если ", " зависит от ", " при условии что "),
    "en": (" only if ", " depends on ", " provided that "),
}


def _stable_key(prefix: str, *parts: str) -> str:
    raw = "\x1f".join(parts).encode("utf-8")
    return f"{prefix}-" + hashlib.sha1(raw).hexdigest()[:20]


def _hash_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _normalize_language(language: str | None) -> str:
    value = str(language or "").strip().casefold()
    if value.startswith("ru"):
        return "ru"
    if value.startswith("en"):
        return "en"
    raise AdaptationValidationError(
        "Semantic enrichment v1 поддерживает только явно заданные ru/en language"
    )


def _tokens(value: str) -> list[str]:
    return re.findall(r"[A-Za-zА-Яа-яЁё'-]+", value.casefold(), flags=re.UNICODE)


def _contains_marker(value: str, markers: Iterable[str]) -> bool:
    normalized = " " + " ".join(value.casefold().split()) + " "
    return any(marker in normalized for marker in markers)


def _relation_key(kind: str, source: str, target: str) -> str:
    return _stable_key("relation", kind, source, target)


@dataclass(frozen=True)
class SemanticEvidence:
    target_type: str
    target_key: str
    source_id: str
    sentence_index: int
    char_start: int
    char_end: int
    locator: str
    text_sha256: str
    rule: str
    confidence: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "target_type": self.target_type,
            "target_key": self.target_key,
            "source_id": self.source_id,
            "sentence_index": self.sentence_index,
            "char_start": self.char_start,
            "char_end": self.char_end,
            "locator": self.locator,
            "text_sha256": self.text_sha256,
            "method": SEMANTIC_METHOD,
            "method_version": SEMANTIC_VERSION,
            "rule": self.rule,
            "confidence": round(self.confidence, 4),
        }


@dataclass(frozen=True)
class SemanticEnrichmentResult:
    story_map: StoryMap
    baseline: StoryExtractionResult
    evidence: tuple[SemanticEvidence, ...]
    metadata: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "story_map": {
                "map_id": self.story_map.map_id,
                "language": self.story_map.language,
                "nodes": [
                    {
                        "key": node.key,
                        "kind": node.kind,
                        "label": node.label,
                        "importance": node.importance,
                        "confidence": node.confidence,
                        "maps_from": list(node.maps_from),
                    }
                    for node in self.story_map.nodes
                ],
                "relations": [
                    {
                        "kind": relation.kind,
                        "source": relation.source,
                        "target": relation.target,
                        "importance": relation.importance,
                        "confidence": relation.confidence,
                    }
                    for relation in self.story_map.relations
                ],
            },
            "baseline_map_id": self.baseline.story_map.map_id,
            "baseline_evidence": [item.as_dict() for item in self.baseline.evidence],
            "semantic_evidence": [item.as_dict() for item in self.evidence],
            "metadata": dict(self.metadata),
        }


class RuleBasedStorySemanticEnricher:
    """Консервативный derived semantic layer поверх sentence-graph-baseline.

    Правила намеренно узкие. Они фиксируют доказуемые lexical cues и не
    подменяют semantic NLP/LLM. Baseline StoryMap не изменяется на диске:
    создаётся новый derived map с отдельным method/version/evidence contract.
    """

    def __init__(self, *, max_sentences: int = 120) -> None:
        self.baseline_extractor = RuleBasedStoryExtractor(max_sentences=max_sentences)
        self.max_sentences = max_sentences

    @staticmethod
    def _evidence(
        source_id: str,
        sentence,
        *,
        target_type: str,
        target_key: str,
        rule: str,
        confidence: float,
    ) -> SemanticEvidence:
        return SemanticEvidence(
            target_type=target_type,
            target_key=target_key,
            source_id=source_id,
            sentence_index=sentence.index,
            char_start=sentence.start,
            char_end=sentence.end,
            locator=f"sentence:{sentence.index + 1}",
            text_sha256=_hash_text(sentence.text),
            rule=rule,
            confidence=confidence,
        )

    @staticmethod
    def _split_after_marker(text: str, markers: Iterable[str]) -> tuple[str, str] | None:
        padded = " " + " ".join(text.split()) + " "
        lower = padded.casefold()
        for marker in markers:
            position = lower.find(marker)
            if position < 0:
                continue
            before = padded[:position].strip(" ,.;:—-")
            after = padded[position + len(marker):].strip(" ,.;:—-")
            if before and after:
                return before, after
        return None

    @staticmethod
    def _because_clause(text: str, language: str) -> str | None:
        value = " ".join(text.strip().split()).rstrip(".!?…")
        if language == "en":
            prefix = re.match(r"^Because\s+(.+?),\s*(.+)$", value, flags=re.IGNORECASE)
            if prefix:
                return prefix.group(1).strip()
            middle = re.match(r"^(.+?)\s+because\s+(.+)$", value, flags=re.IGNORECASE)
            if middle:
                return middle.group(2).strip()
        else:
            prefix = re.match(r"^(?:Поскольку|Так как)\s+(.+?),\s*(.+)$", value, flags=re.IGNORECASE)
            if prefix:
                return prefix.group(1).strip()
            middle = re.match(r"^(.+?),?\s+потому что\s+(.+)$", value, flags=re.IGNORECASE)
            if middle:
                return middle.group(2).strip()
        return None

    @staticmethod
    def _event_by_sentence(baseline: StoryExtractionResult) -> dict[int, str]:
        kind_by_key = {node.key: node.kind for node in baseline.story_map.nodes}
        result: dict[int, str] = {}
        for item in baseline.evidence:
            if kind_by_key.get(item.node_key) == "event":
                result.setdefault(item.sentence_index, item.node_key)
        return result

    @staticmethod
    def _characters_by_sentence(baseline: StoryExtractionResult) -> dict[int, list[str]]:
        kind_by_key = {node.key: node.kind for node in baseline.story_map.nodes}
        result: dict[int, list[str]] = {}
        for item in baseline.evidence:
            if kind_by_key.get(item.node_key) != "character":
                continue
            result.setdefault(item.sentence_index, [])
            if item.node_key not in result[item.sentence_index]:
                result[item.sentence_index].append(item.node_key)
        return result

    def extract(
        self,
        text: str,
        *,
        source_id: str,
        language: str,
        map_id: str | None = None,
    ) -> SemanticEnrichmentResult:
        lang = _normalize_language(language)
        baseline = self.baseline_extractor.extract(
            text,
            source_id=source_id,
            language=lang,
        )
        sentences = self.baseline_extractor.split_sentences(text)[: self.max_sentences]
        event_by_sentence = self._event_by_sentence(baseline)
        characters_by_sentence = self._characters_by_sentence(baseline)

        nodes = [
            {
                "key": node.key,
                "kind": node.kind,
                "label": node.label,
                "importance": node.importance,
                "confidence": node.confidence,
                "maps_from": list(node.maps_from),
            }
            for node in baseline.story_map.nodes
        ]
        relations = [
            {
                "kind": relation.kind,
                "source": relation.source,
                "target": relation.target,
                "importance": relation.importance,
                "confidence": relation.confidence,
            }
            for relation in baseline.story_map.relations
        ]
        evidence: list[SemanticEvidence] = []

        for sentence in sentences:
            lower = " " + " ".join(sentence.text.casefold().split()) + " "
            event_key = event_by_sentence.get(sentence.index)

            if _contains_marker(sentence.text, WORLD_MARKERS[lang]):
                key = _stable_key(
                    "worldbuilding",
                    source_id,
                    SEMANTIC_VERSION,
                    str(sentence.index),
                    _hash_text(sentence.text),
                )
                nodes.append(
                    {
                        "key": key,
                        "kind": "worldbuilding",
                        "label": sentence.text[:500],
                        "importance": 0.55,
                        "confidence": 0.58,
                        "maps_from": [],
                    }
                )
                evidence.append(
                    self._evidence(
                        source_id,
                        sentence,
                        target_type="node",
                        target_key=key,
                        rule="worldbuilding_lexical_marker",
                        confidence=0.58,
                    )
                )
                if event_key:
                    relations.append(
                        {
                            "kind": "explains",
                            "source": key,
                            "target": event_key,
                            "importance": 0.45,
                            "confidence": 0.48,
                        }
                    )

            if _contains_marker(sentence.text, THEME_MARKERS[lang]):
                key = _stable_key(
                    "theme",
                    source_id,
                    SEMANTIC_VERSION,
                    str(sentence.index),
                    _hash_text(sentence.text),
                )
                nodes.append(
                    {
                        "key": key,
                        "kind": "theme",
                        "label": sentence.text[:500],
                        "importance": 0.5,
                        "confidence": 0.52,
                        "maps_from": [],
                    }
                )
                evidence.append(
                    self._evidence(
                        source_id,
                        sentence,
                        target_type="node",
                        target_key=key,
                        rule="theme_explicit_marker",
                        confidence=0.52,
                    )
                )

            character_keys = characters_by_sentence.get(sentence.index, [])
            if len(character_keys) >= 2 and _contains_marker(
                sentence.text,
                RELATIONSHIP_MARKERS[lang],
            ):
                key = _stable_key(
                    "relationship",
                    source_id,
                    SEMANTIC_VERSION,
                    str(sentence.index),
                    _hash_text(sentence.text),
                )
                nodes.append(
                    {
                        "key": key,
                        "kind": "relationship",
                        "label": sentence.text[:500],
                        "importance": 0.55,
                        "confidence": 0.62,
                        "maps_from": [],
                    }
                )
                evidence.append(
                    self._evidence(
                        source_id,
                        sentence,
                        target_type="node",
                        target_key=key,
                        rule="relationship_marker_with_two_characters",
                        confidence=0.62,
                    )
                )
                for character_key in character_keys:
                    relations.append(
                        {
                            "kind": "relates_to",
                            "source": key,
                            "target": character_key,
                            "importance": 0.5,
                            "confidence": 0.62,
                        }
                    )

            cause = self._because_clause(sentence.text, lang)
            if cause and event_key and len(_tokens(cause)) >= 2:
                cause_key = _stable_key(
                    "cause-event",
                    source_id,
                    SEMANTIC_VERSION,
                    str(sentence.index),
                    _hash_text(cause),
                )
                nodes.append(
                    {
                        "key": cause_key,
                        "kind": "event",
                        "label": cause[:500],
                        "importance": 0.5,
                        "confidence": 0.68,
                        "maps_from": [],
                    }
                )
                evidence.append(
                    self._evidence(
                        source_id,
                        sentence,
                        target_type="node",
                        target_key=cause_key,
                        rule="because_clause",
                        confidence=0.68,
                    )
                )
                relations.append(
                    {
                        "kind": "explains",
                        "source": cause_key,
                        "target": event_key,
                        "importance": 0.6,
                        "confidence": 0.66,
                    }
                )
                relation_key = _relation_key("explains", cause_key, event_key)
                evidence.append(
                    self._evidence(
                        source_id,
                        sentence,
                        target_type="relation",
                        target_key=relation_key,
                        rule="because_clause",
                        confidence=0.66,
                    )
                )

            dependency = self._split_after_marker(
                sentence.text,
                DEPENDENCY_MARKERS[lang],
            )
            if dependency and event_key and len(_tokens(dependency[1])) >= 2:
                condition = dependency[1]
                condition_key = _stable_key(
                    "condition-event",
                    source_id,
                    SEMANTIC_VERSION,
                    str(sentence.index),
                    _hash_text(condition),
                )
                nodes.append(
                    {
                        "key": condition_key,
                        "kind": "event",
                        "label": condition[:500],
                        "importance": 0.5,
                        "confidence": 0.64,
                        "maps_from": [],
                    }
                )
                relations.append(
                    {
                        "kind": "depends_on",
                        "source": event_key,
                        "target": condition_key,
                        "importance": 0.58,
                        "confidence": 0.62,
                    }
                )
                evidence.append(
                    self._evidence(
                        source_id,
                        sentence,
                        target_type="node",
                        target_key=condition_key,
                        rule="dependency_marker",
                        confidence=0.64,
                    )
                )

            motivation = self._split_after_marker(
                sentence.text,
                MOTIVATION_MARKERS[lang],
            )
            if motivation and event_key and len(_tokens(motivation[1])) >= 2:
                goal = motivation[1]
                motivation_key = _stable_key(
                    "goal-motivation",
                    source_id,
                    SEMANTIC_VERSION,
                    str(sentence.index),
                    _hash_text(goal),
                )
                nodes.append(
                    {
                        "key": motivation_key,
                        "kind": "motivation",
                        "label": goal[:500],
                        "importance": 0.55,
                        "confidence": 0.68,
                        "maps_from": [],
                    }
                )
                relations.append(
                    {
                        "kind": "motivates",
                        "source": motivation_key,
                        "target": event_key,
                        "importance": 0.58,
                        "confidence": 0.66,
                    }
                )
                evidence.append(
                    self._evidence(
                        source_id,
                        sentence,
                        target_type="node",
                        target_key=motivation_key,
                        rule="explicit_goal_marker",
                        confidence=0.68,
                    )
                )

            # Очень консервативный coreference: текущая фраза не содержит имени,
            # содержит местоимение и предыдущая фраза ссылалась ровно на одного
            # персонажа. При любой неоднозначности ничего не добавляем.
            if event_key and not character_keys and sentence.index > 0:
                pronouns = set(_tokens(sentence.text)) & COREFERENCE_PRONOUNS[lang]
                previous_characters = characters_by_sentence.get(sentence.index - 1, [])
                if pronouns and len(previous_characters) == 1:
                    character_key = previous_characters[0]
                    relations.append(
                        {
                            "kind": "relates_to",
                            "source": character_key,
                            "target": event_key,
                            "importance": 0.35,
                            "confidence": 0.52,
                        }
                    )
                    relation_key = _relation_key("relates_to", character_key, event_key)
                    evidence.append(
                        self._evidence(
                            source_id,
                            sentence,
                            target_type="relation",
                            target_key=relation_key,
                            rule="single_recent_character_coreference",
                            confidence=0.52,
                        )
                    )

        # Ending извлекается только из последних двух использованных предложений
        # и только при явном lexical marker.
        for sentence in sentences[-2:]:
            if not _contains_marker(sentence.text, ENDING_MARKERS[lang]):
                continue
            key = _stable_key(
                "ending",
                source_id,
                SEMANTIC_VERSION,
                str(sentence.index),
                _hash_text(sentence.text),
            )
            nodes.append(
                {
                    "key": key,
                    "kind": "ending",
                    "label": sentence.text[:500],
                    "importance": 0.75,
                    "confidence": 0.72,
                    "maps_from": [],
                }
            )
            evidence.append(
                self._evidence(
                    source_id,
                    sentence,
                    target_type="node",
                    target_key=key,
                    rule="ending_explicit_marker",
                    confidence=0.72,
                )
            )

        node_by_key: dict[str, dict[str, Any]] = {}
        for node in nodes:
            current = node_by_key.get(node["key"])
            if current is None or float(node["confidence"]) > float(current["confidence"]):
                node_by_key[node["key"]] = node

        relation_by_signature: dict[tuple[str, str, str], dict[str, Any]] = {}
        for relation in relations:
            signature = (relation["kind"], relation["source"], relation["target"])
            current = relation_by_signature.get(signature)
            if current is None or float(relation["confidence"]) > float(current["confidence"]):
                relation_by_signature[signature] = relation

        resolved_map_id = map_id or _stable_key(
            "semantic-map",
            baseline.story_map.map_id,
            SEMANTIC_METHOD,
            SEMANTIC_VERSION,
        )
        story_map = StoryMap.from_dict(
            {
                "map_id": resolved_map_id,
                "language": baseline.story_map.language,
                "nodes": sorted(node_by_key.values(), key=lambda row: (row["kind"], row["key"])),
                "relations": sorted(
                    relation_by_signature.values(),
                    key=lambda row: (row["kind"], row["source"], row["target"]),
                ),
            }
        )
        metadata = {
            "research_only": True,
            "method": SEMANTIC_METHOD,
            "method_version": SEMANTIC_VERSION,
            "baseline_map_id": baseline.story_map.map_id,
            "language": lang,
            "semantic_evidence_count": len(evidence),
            "node_count": len(story_map.nodes),
            "relation_count": len(story_map.relations),
            "limitations": [
                "lexical_semantics_only",
                "single_recent_character_coreference_only",
                "no_general_semantic_event_matching",
                "no_quality_judgement",
            ],
        }
        return SemanticEnrichmentResult(
            story_map=story_map,
            baseline=baseline,
            evidence=tuple(evidence),
            metadata=metadata,
        )
