from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any

from src.adaptation_analysis import AdaptationValidationError
from src.story_diff import StoryMap


EXTRACTOR_METHOD = "sentence-graph-baseline"
EXTRACTOR_VERSION = "1"
MAX_TEXT_LENGTH = 100_000
DEFAULT_MAX_SENTENCES = 120

_SENTENCE_RE = re.compile(r"[^.!?…]+(?:[.!?…]+|$)", re.UNICODE)
_WORD_RE = re.compile(r"[A-Za-zА-Яа-яЁё0-9'-]+", re.UNICODE)
_NAME_RE = re.compile(
    r"(?<![\w'-])(?:[A-ZА-ЯЁ][a-zа-яё'-]{2,})(?:\s+[A-ZА-ЯЁ][a-zа-яё'-]{2,}){0,2}(?![\w'-])",
    re.UNICODE,
)

_SENTENCE_START_NOISE = {
    "a",
    "an",
    "as",
    "after",
    "although",
    "because",
    "before",
    "but",
    "during",
    "finally",
    "he",
    "her",
    "his",
    "however",
    "in",
    "it",
    "later",
    "meanwhile",
    "she",
    "so",
    "the",
    "then",
    "they",
    "this",
    "therefore",
    "when",
    "while",
    "автор",
    "в",
    "вскоре",
    "где",
    "герой",
    "затем",
    "из-за",
    "когда",
    "между",
    "однако",
    "он",
    "она",
    "они",
    "позже",
    "после",
    "поскольку",
    "поэтому",
    "при",
    "тем",
    "тогда",
    "фильм",
    "эта",
    "это",
}

_CAUSAL_RESULT_PREFIXES = (
    "as a result",
    "consequently",
    "therefore",
    "thus",
    "so ",
    "в результате",
    "вследствие этого",
    "поэтому",
    "таким образом",
)

_MOTIVATION_MARKERS = (
    " wants to ",
    " wants ",
    " tries to ",
    " tries ",
    " decides to ",
    " decides ",
    " must ",
    " needs to ",
    " needs ",
    " seeks to ",
    " seeks ",
    " aims to ",
    " aims ",
    " хочет ",
    " пытается ",
    " решает ",
    " должен ",
    " должна ",
    " должны ",
    " стремится ",
    " намерен ",
    " намерена ",
    " вынужден ",
    " вынуждена ",
)


def _hash_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _stable_key(prefix: str, *parts: str) -> str:
    raw = "\x1f".join(parts).encode("utf-8")
    return f"{prefix}-" + hashlib.sha1(raw).hexdigest()[:18]


def _clean_text(value: Any, *, field_name: str, limit: int) -> str:
    text = str(value or "").strip()
    if not text:
        raise AdaptationValidationError(f"{field_name} не должен быть пустым")
    if len(text) > limit:
        raise AdaptationValidationError(
            f"{field_name} длиннее допустимых {limit} символов"
        )
    return text


def _compact(value: str) -> str:
    return " ".join(value.strip().split())


def _tokens(value: str) -> list[str]:
    return _WORD_RE.findall(value)


def _normal_name(value: str) -> str:
    return " ".join(_tokens(value.lower()))


def _is_motivation(sentence: str) -> bool:
    normalized = " " + _compact(sentence).lower() + " "
    return any(marker in normalized for marker in _MOTIVATION_MARKERS)


def _has_causal_result_prefix(sentence: str) -> bool:
    normalized = _compact(sentence).lower()
    return any(normalized.startswith(prefix) for prefix in _CAUSAL_RESULT_PREFIXES)


@dataclass(frozen=True)
class SentenceSpan:
    index: int
    start: int
    end: int
    text: str


@dataclass(frozen=True)
class StoryEvidence:
    node_key: str
    source_id: str
    sentence_index: int
    char_start: int
    char_end: int
    locator: str
    text_sha256: str
    extractor_method: str
    extractor_version: str
    confidence: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "node_key": self.node_key,
            "source_id": self.source_id,
            "sentence_index": self.sentence_index,
            "char_start": self.char_start,
            "char_end": self.char_end,
            "locator": self.locator,
            "text_sha256": self.text_sha256,
            "extractor_method": self.extractor_method,
            "extractor_version": self.extractor_version,
            "confidence": self.confidence,
        }


@dataclass(frozen=True)
class StoryExtractionResult:
    story_map: StoryMap
    evidence: tuple[StoryEvidence, ...]
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
            "evidence": [item.as_dict() for item in self.evidence],
            "metadata": dict(self.metadata),
        }


class RuleBasedStoryExtractor:
    """Воспроизводимый P4 baseline: summary text -> raw StoryMap.

    Это не semantic/LLM extractor. Он создаёт проверяемый baseline, где каждый
    structural node привязан к конкретному sentence span. Поздний bilingual и
    source↔adaptation alignment выполняется отдельным слоем и заполняет
    ``maps_from``; здесь карты двух сторон намеренно не сопоставляются.
    """

    method = EXTRACTOR_METHOD
    version = EXTRACTOR_VERSION

    def __init__(self, *, max_sentences: int = DEFAULT_MAX_SENTENCES) -> None:
        if max_sentences < 1:
            raise ValueError("max_sentences должен быть положительным")
        self.max_sentences = int(max_sentences)

    @staticmethod
    def split_sentences(text: str) -> list[SentenceSpan]:
        result: list[SentenceSpan] = []
        for match in _SENTENCE_RE.finditer(text):
            raw = match.group(0)
            if not raw.strip():
                continue
            leading = len(raw) - len(raw.lstrip())
            trailing = len(raw.rstrip())
            start = match.start() + leading
            end = match.start() + trailing
            sentence = _compact(text[start:end])
            if len(_tokens(sentence)) < 3:
                continue
            result.append(
                SentenceSpan(
                    index=len(result),
                    start=start,
                    end=end,
                    text=sentence,
                )
            )
        return result

    @staticmethod
    def _candidate_names(sentences: list[SentenceSpan]) -> dict[str, dict[str, Any]]:
        candidates: dict[str, dict[str, Any]] = {}
        for sentence in sentences:
            for match in _NAME_RE.finditer(sentence.text):
                label = _compact(match.group(0))
                normalized = _normal_name(label)
                if not normalized:
                    continue
                words = normalized.split()

                # Capitalized discourse marker в начале предложения не является
                # частью имени: "Поэтому Анна" -> "Анна",
                # "Therefore Alice Carter" -> "Alice Carter".
                if match.start() == 0 and words and words[0] in _SENTENCE_START_NOISE:
                    label_parts = label.split()
                    if len(label_parts) <= 1:
                        continue
                    label = " ".join(label_parts[1:])
                    normalized = _normal_name(label)
                    words = normalized.split()
                    if not normalized:
                        continue

                if len(words) == 1 and words[0] in _SENTENCE_START_NOISE:
                    continue
                item = candidates.setdefault(
                    normalized,
                    {
                        "label": label,
                        "count": 0,
                        "sentences": [],
                        "first_sentence": sentence.index,
                    },
                )
                item["count"] += 1
                if sentence.index not in item["sentences"]:
                    item["sentences"].append(sentence.index)
                # Предпочитаем наиболее информативный вариант написания.
                if len(label) > len(item["label"]):
                    item["label"] = label
        return {
            key: value
            for key, value in candidates.items()
            if value["count"] >= 2 or len(key.split()) >= 2
        }

    @staticmethod
    def _node_evidence(
        node_key: str,
        source_id: str,
        sentence: SentenceSpan,
        *,
        confidence: float,
    ) -> StoryEvidence:
        return StoryEvidence(
            node_key=node_key,
            source_id=source_id,
            sentence_index=sentence.index,
            char_start=sentence.start,
            char_end=sentence.end,
            locator=f"sentence:{sentence.index + 1}",
            text_sha256=_hash_text(sentence.text),
            extractor_method=EXTRACTOR_METHOD,
            extractor_version=EXTRACTOR_VERSION,
            confidence=confidence,
        )

    def extract(
        self,
        text: str,
        *,
        source_id: str,
        language: str | None = None,
        map_id: str | None = None,
    ) -> StoryExtractionResult:
        source_id = _clean_text(source_id, field_name="source_id", limit=180)
        raw_text = _clean_text(text, field_name="text", limit=MAX_TEXT_LENGTH)
        language = _compact(str(language or "")) or None

        all_sentences = self.split_sentences(raw_text)
        if not all_sentences:
            raise AdaptationValidationError(
                "Из текста не удалось получить ни одного содержательного предложения"
            )
        sentences = all_sentences[: self.max_sentences]
        truncated = len(all_sentences) > len(sentences)
        text_hash = _hash_text(raw_text)
        resolved_map_id = map_id or _stable_key(
            "map",
            source_id,
            language or "und",
            self.method,
            self.version,
            text_hash,
        )

        names = self._candidate_names(sentences)
        character_keys = {
            normalized: _stable_key("character", normalized)
            for normalized in sorted(names)
        }

        nodes: list[dict[str, Any]] = []
        relations: list[dict[str, Any]] = []
        evidence: list[StoryEvidence] = []

        for normalized, item in sorted(
            names.items(), key=lambda pair: pair[1]["first_sentence"]
        ):
            key = character_keys[normalized]
            first = sentences[item["first_sentence"]]
            confidence = min(0.85, 0.58 + 0.05 * min(int(item["count"]), 5))
            nodes.append(
                {
                    "key": key,
                    "kind": "character",
                    "label": item["label"],
                    "importance": 0.5,
                    "confidence": confidence,
                    "maps_from": [],
                }
            )
            for sentence_index in item["sentences"]:
                evidence.append(
                    self._node_evidence(
                        key,
                        source_id,
                        sentences[sentence_index],
                        confidence=confidence,
                    )
                )

        event_keys: list[str] = []
        motivation_keys: list[str | None] = []
        for sentence in sentences:
            event_key = _stable_key(
                "event",
                source_id,
                self.version,
                str(sentence.index),
                _hash_text(sentence.text),
            )
            event_keys.append(event_key)
            event_confidence = 0.58
            nodes.append(
                {
                    "key": event_key,
                    "kind": "event",
                    "label": sentence.text[:500],
                    "importance": 0.5,
                    "confidence": event_confidence,
                    "maps_from": [],
                }
            )
            evidence.append(
                self._node_evidence(
                    event_key,
                    source_id,
                    sentence,
                    confidence=event_confidence,
                )
            )

            sentence_lower = sentence.text.lower()
            mentioned_character_keys: list[str] = []
            for normalized, character_key in character_keys.items():
                if re.search(
                    r"(?<![\w'-])" + re.escape(normalized) + r"(?![\w'-])",
                    sentence_lower,
                    flags=re.UNICODE,
                ):
                    mentioned_character_keys.append(character_key)
                    relations.append(
                        {
                            "kind": "relates_to",
                            "source": character_key,
                            "target": event_key,
                            "importance": 0.4,
                            "confidence": 0.62,
                        }
                    )

            motivation_key: str | None = None
            if _is_motivation(sentence.text):
                motivation_key = _stable_key(
                    "motivation",
                    source_id,
                    self.version,
                    str(sentence.index),
                    _hash_text(sentence.text),
                )
                nodes.append(
                    {
                        "key": motivation_key,
                        "kind": "motivation",
                        "label": sentence.text[:500],
                        "importance": 0.5,
                        "confidence": 0.62,
                        "maps_from": [],
                    }
                )
                evidence.append(
                    self._node_evidence(
                        motivation_key,
                        source_id,
                        sentence,
                        confidence=0.62,
                    )
                )
                relations.append(
                    {
                        "kind": "motivates",
                        "source": motivation_key,
                        "target": event_key,
                        "importance": 0.5,
                        "confidence": 0.60,
                    }
                )
                for character_key in mentioned_character_keys:
                    relations.append(
                        {
                            "kind": "relates_to",
                            "source": character_key,
                            "target": motivation_key,
                            "importance": 0.45,
                            "confidence": 0.60,
                        }
                    )
            motivation_keys.append(motivation_key)

            if sentence.index > 0 and _has_causal_result_prefix(sentence.text):
                relations.append(
                    {
                        "kind": "causes",
                        "source": event_keys[sentence.index - 1],
                        "target": event_key,
                        "importance": 0.5,
                        "confidence": 0.55,
                    }
                )

        story_map = StoryMap.from_dict(
            {
                "map_id": resolved_map_id,
                "language": language,
                "nodes": nodes,
                "relations": relations,
            }
        )
        metadata = {
            "extractor_method": self.method,
            "extractor_version": self.version,
            "source_id": source_id,
            "language": language,
            "source_text_sha256": text_hash,
            "sentence_count_total": len(all_sentences),
            "sentence_count_used": len(sentences),
            "truncated": truncated,
            "character_count": len(character_keys),
            "event_count": len(event_keys),
            "motivation_count": sum(1 for key in motivation_keys if key),
            "alignment_status": "not_aligned",
            "research_only": True,
        }
        return StoryExtractionResult(
            story_map=story_map,
            evidence=tuple(evidence),
            metadata=metadata,
        )
