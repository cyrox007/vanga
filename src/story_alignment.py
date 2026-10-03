from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from src.adaptation_analysis import AdaptationValidationError
from src.story_diff import StoryMap, StoryNode, StoryRelation


ALIGNMENT_METHOD = "lexical-alias-baseline"
ALIGNMENT_VERSION = "1"

_WORD_RE = re.compile(r"[A-Za-zА-Яа-яЁё0-9'-]+", re.UNICODE)
_STOPWORDS = {
    "a", "an", "and", "as", "at", "by", "for", "from", "in", "into", "of",
    "on", "or", "the", "to", "with", "after", "before", "then", "therefore",
    "а", "без", "в", "во", "для", "до", "за", "и", "из", "к", "как", "на",
    "но", "о", "от", "по", "после", "перед", "при", "с", "со", "у", "что",
    "это", "затем", "поэтому",
}


def _stable_id(prefix: str, *parts: str) -> str:
    raw = "\x1f".join(parts).encode("utf-8")
    return f"{prefix}-" + hashlib.sha1(raw).hexdigest()[:20]


def _normalize(value: str) -> str:
    return " ".join(_WORD_RE.findall(str(value or "").lower()))


def _content_tokens(value: str) -> set[str]:
    return {
        token
        for token in _WORD_RE.findall(str(value or "").lower())
        if token not in _STOPWORDS and len(token) > 1
    }


def _jaccard(left: str, right: str) -> float:
    a = _content_tokens(left)
    b = _content_tokens(right)
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _map_payload(story_map: StoryMap) -> dict[str, Any]:
    return {
        "map_id": story_map.map_id,
        "language": story_map.language,
        "nodes": [
            {
                "key": node.key,
                "kind": node.kind,
                "label": node.label,
                "importance": node.importance,
                "confidence": node.confidence,
                "maps_from": list(node.maps_from),
            }
            for node in story_map.nodes
        ],
        "relations": [
            {
                "kind": relation.kind,
                "source": relation.source,
                "target": relation.target,
                "importance": relation.importance,
                "confidence": relation.confidence,
            }
            for relation in story_map.relations
        ],
    }


class StoryAliasIndex:
    """Explicit multilingual identity aliases.

    Формат входа: ``{canonical_id: [alias1, alias2, ...]}``. Один и тот же
    normalized alias не может принадлежать двум canonical groups.
    """

    def __init__(self, groups: dict[str, list[str]] | None = None) -> None:
        self.groups = groups or {}
        self._alias_to_group: dict[str, str] = {}
        for canonical_id, aliases in self.groups.items():
            canonical = str(canonical_id or "").strip()
            if not canonical:
                raise AdaptationValidationError("alias canonical_id не должен быть пустым")
            if not isinstance(aliases, list) or not aliases:
                raise AdaptationValidationError(
                    f"alias group {canonical!r} должен содержать непустой массив aliases"
                )
            for raw in aliases:
                alias = _normalize(str(raw or ""))
                if not alias:
                    raise AdaptationValidationError(
                        f"alias group {canonical!r} содержит пустой alias"
                    )
                previous = self._alias_to_group.get(alias)
                if previous is not None and previous != canonical:
                    raise AdaptationValidationError(
                        f"alias {raw!r} одновременно принадлежит {previous!r} и {canonical!r}"
                    )
                self._alias_to_group[alias] = canonical

    def group_for(self, label: str) -> str | None:
        return self._alias_to_group.get(_normalize(label))


@dataclass(frozen=True)
class NodeMatch:
    source_key: str
    adaptation_key: str
    kind: str
    score: float
    method: str
    reason: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "source_key": self.source_key,
            "adaptation_key": self.adaptation_key,
            "kind": self.kind,
            "score": round(self.score, 4),
            "method": self.method,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class AmbiguousMatch:
    source_key: str
    kind: str
    candidate_keys: tuple[str, ...]
    top_score: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "source_key": self.source_key,
            "kind": self.kind,
            "candidate_keys": list(self.candidate_keys),
            "top_score": round(self.top_score, 4),
        }


@dataclass(frozen=True)
class AlignmentResult:
    matches: tuple[NodeMatch, ...]
    ambiguous: tuple[AmbiguousMatch, ...]
    unmatched_source_keys: tuple[str, ...]
    unmatched_adaptation_keys: tuple[str, ...]
    metadata: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "matches": [item.as_dict() for item in self.matches],
            "ambiguous": [item.as_dict() for item in self.ambiguous],
            "unmatched_source_keys": list(self.unmatched_source_keys),
            "unmatched_adaptation_keys": list(self.unmatched_adaptation_keys),
            "metadata": dict(self.metadata),
        }


class StoryNodeMatcher:
    """Детерминированный alias/lexical baseline без скрытого semantic inference."""

    def __init__(
        self,
        *,
        alias_groups: dict[str, list[str]] | None = None,
        threshold: float = 0.62,
        ambiguity_margin: float = 0.10,
    ) -> None:
        if not 0.0 < threshold <= 1.0:
            raise ValueError("threshold должен быть в диапазоне (0, 1]")
        if not 0.0 <= ambiguity_margin < 1.0:
            raise ValueError("ambiguity_margin должен быть в диапазоне [0, 1)")
        self.aliases = StoryAliasIndex(alias_groups)
        self.threshold = float(threshold)
        self.ambiguity_margin = float(ambiguity_margin)

    def _score(
        self,
        source: StoryNode,
        adaptation: StoryNode,
        *,
        source_language: str | None,
        adaptation_language: str | None,
    ) -> tuple[float, str]:
        if source.kind != adaptation.kind:
            return 0.0, "kind_mismatch"

        left = _normalize(source.label)
        right = _normalize(adaptation.label)
        if left and left == right:
            return 1.0, "normalized_exact"

        left_group = self.aliases.group_for(source.label)
        right_group = self.aliases.group_for(adaptation.label)
        if left_group is not None and left_group == right_group:
            return 0.98, f"explicit_alias:{left_group}"

        different_language = (
            source_language is not None
            and adaptation_language is not None
            and source_language != adaptation_language
        )
        if different_language:
            return 0.0, "cross_language_requires_alias_or_semantic_scorer"

        lexical = _jaccard(source.label, adaptation.label)
        if source.kind == "character":
            # Для персон lexical matching должен быть существенно строже, чем
            # для предложений-событий: одно общее имя не доказывает identity.
            if lexical >= 0.80:
                return lexical, "same_language_character_tokens"
            return 0.0, "weak_character_overlap"
        return lexical, "same_language_token_jaccard"

    def match(self, source: StoryMap, adaptation: StoryMap) -> AlignmentResult:
        matches: list[NodeMatch] = []
        ambiguous: list[AmbiguousMatch] = []
        matched_source: set[str] = set()
        matched_adaptation: set[str] = set()

        adaptation_by_kind: dict[str, list[StoryNode]] = defaultdict(list)
        for node in adaptation.nodes:
            adaptation_by_kind[node.kind].append(node)

        for source_node in source.nodes:
            scored: list[tuple[float, StoryNode, str]] = []
            for adaptation_node in adaptation_by_kind.get(source_node.kind, []):
                score, reason = self._score(
                    source_node,
                    adaptation_node,
                    source_language=source.language,
                    adaptation_language=adaptation.language,
                )
                if score > 0.0:
                    scored.append((score, adaptation_node, reason))
            scored.sort(key=lambda item: (-item[0], item[1].key))
            if not scored or scored[0][0] < self.threshold:
                continue

            top_score, top_node, reason = scored[0]
            competitive = [
                item
                for item in scored[1:]
                if item[0] >= self.threshold
                and top_score - item[0] < self.ambiguity_margin
            ]
            if competitive:
                candidate_keys = tuple(
                    [top_node.key] + [item[1].key for item in competitive]
                )
                ambiguous.append(
                    AmbiguousMatch(
                        source_key=source_node.key,
                        kind=source_node.kind,
                        candidate_keys=candidate_keys,
                        top_score=top_score,
                    )
                )
                continue

            matches.append(
                NodeMatch(
                    source_key=source_node.key,
                    adaptation_key=top_node.key,
                    kind=source_node.kind,
                    score=top_score,
                    method=ALIGNMENT_METHOD,
                    reason=reason,
                )
            )
            matched_source.add(source_node.key)
            matched_adaptation.add(top_node.key)

        unmatched_source = tuple(
            node.key for node in source.nodes if node.key not in matched_source
        )
        unmatched_adaptation = tuple(
            node.key for node in adaptation.nodes if node.key not in matched_adaptation
        )
        return AlignmentResult(
            matches=tuple(matches),
            ambiguous=tuple(ambiguous),
            unmatched_source_keys=unmatched_source,
            unmatched_adaptation_keys=unmatched_adaptation,
            metadata={
                "alignment_method": ALIGNMENT_METHOD,
                "alignment_version": ALIGNMENT_VERSION,
                "threshold": self.threshold,
                "ambiguity_margin": self.ambiguity_margin,
                "source_language": source.language,
                "adaptation_language": adaptation.language,
                "source_node_count": len(source.nodes),
                "adaptation_node_count": len(adaptation.nodes),
                "matched_source_count": len(matched_source),
                "matched_adaptation_count": len(matched_adaptation),
                "source_match_ratio": (
                    len(matched_source) / len(source.nodes) if source.nodes else 0.0
                ),
                "adaptation_match_ratio": (
                    len(matched_adaptation) / len(adaptation.nodes)
                    if adaptation.nodes
                    else 0.0
                ),
                "ambiguous_count": len(ambiguous),
                "research_only": True,
            },
        )


def merge_story_variants(
    primary: StoryMap,
    secondary: StoryMap,
    *,
    matcher: StoryNodeMatcher,
) -> tuple[StoryMap, AlignmentResult, dict[str, Any]]:
    """Объединяет RU/EN raw maps одной и той же стороны в canonical map.

    `maps_from` намеренно не используется: оно зарезервировано для последующего
    source↔adaptation StoryDiff. Variant alignment хранится отдельным report.
    Many-to-one variant matches не схлопываются автоматически.
    """
    alignment = matcher.match(primary, secondary)
    matches_by_secondary: dict[str, list[NodeMatch]] = defaultdict(list)
    for match in alignment.matches:
        matches_by_secondary[match.adaptation_key].append(match)

    secondary_to_canonical: dict[str, str] = {}
    safe_match_count = 0
    for secondary_node in secondary.nodes:
        linked = matches_by_secondary.get(secondary_node.key, [])
        if len(linked) == 1:
            secondary_to_canonical[secondary_node.key] = linked[0].source_key
            safe_match_count += 1
        else:
            secondary_to_canonical[secondary_node.key] = _stable_id(
                "variant",
                primary.map_id,
                secondary.map_id,
                secondary_node.key,
            )

    nodes_payload: list[dict[str, Any]] = [
        {
            "key": node.key,
            "kind": node.kind,
            "label": node.label,
            "importance": node.importance,
            "confidence": node.confidence,
            "maps_from": list(node.maps_from),
        }
        for node in primary.nodes
    ]
    primary_keys = {node.key for node in primary.nodes}
    for node in secondary.nodes:
        canonical_key = secondary_to_canonical[node.key]
        if canonical_key in primary_keys:
            continue
        nodes_payload.append(
            {
                "key": canonical_key,
                "kind": node.kind,
                "label": node.label,
                "importance": node.importance,
                "confidence": node.confidence,
                "maps_from": [],
            }
        )

    relations_payload: list[dict[str, Any]] = []
    seen_relations: set[tuple[str, str, str]] = set()
    for relation in primary.relations:
        signature = relation.signature
        if signature in seen_relations:
            continue
        seen_relations.add(signature)
        relations_payload.append(
            {
                "kind": relation.kind,
                "source": relation.source,
                "target": relation.target,
                "importance": relation.importance,
                "confidence": relation.confidence,
            }
        )
    for relation in secondary.relations:
        source_key = secondary_to_canonical[relation.source]
        target_key = secondary_to_canonical[relation.target]
        signature = (relation.kind, source_key, target_key)
        if signature in seen_relations:
            continue
        seen_relations.add(signature)
        relations_payload.append(
            {
                "kind": relation.kind,
                "source": source_key,
                "target": target_key,
                "importance": relation.importance,
                "confidence": relation.confidence,
            }
        )

    merged = StoryMap.from_dict(
        {
            "map_id": _stable_id(
                "canonical-map",
                primary.map_id,
                secondary.map_id,
                ALIGNMENT_METHOD,
                ALIGNMENT_VERSION,
            ),
            "language": (
                primary.language
                if primary.language == secondary.language
                else "multi"
            ),
            "nodes": nodes_payload,
            "relations": relations_payload,
        }
    )
    metadata = {
        "merge_method": ALIGNMENT_METHOD,
        "merge_version": ALIGNMENT_VERSION,
        "primary_map_id": primary.map_id,
        "secondary_map_id": secondary.map_id,
        "safe_variant_matches": safe_match_count,
        "primary_node_count": len(primary.nodes),
        "secondary_node_count": len(secondary.nodes),
        "merged_node_count": len(merged.nodes),
        "many_to_one_not_collapsed": sum(
            1 for values in matches_by_secondary.values() if len(values) > 1
        ),
        "research_only": True,
    }
    return merged, alignment, metadata


def apply_source_alignment(
    source: StoryMap,
    adaptation: StoryMap,
    *,
    matcher: StoryNodeMatcher,
) -> tuple[StoryMap, AlignmentResult]:
    """Создаёт derived adaptation map с `maps_from` для StoryDiffAnalyzer."""
    alignment = matcher.match(source, adaptation)
    source_keys_by_adaptation: dict[str, list[str]] = defaultdict(list)
    for match in alignment.matches:
        source_keys_by_adaptation[match.adaptation_key].append(match.source_key)

    nodes_payload: list[dict[str, Any]] = []
    for node in adaptation.nodes:
        mapped = list(node.maps_from)
        for source_key in source_keys_by_adaptation.get(node.key, []):
            if source_key not in mapped:
                mapped.append(source_key)
        nodes_payload.append(
            {
                "key": node.key,
                "kind": node.kind,
                "label": node.label,
                "importance": node.importance,
                "confidence": node.confidence,
                "maps_from": mapped,
            }
        )

    aligned = StoryMap.from_dict(
        {
            "map_id": _stable_id(
                "aligned-map",
                source.map_id,
                adaptation.map_id,
                ALIGNMENT_METHOD,
                ALIGNMENT_VERSION,
            ),
            "language": adaptation.language,
            "nodes": nodes_payload,
            "relations": [
                {
                    "kind": relation.kind,
                    "source": relation.source,
                    "target": relation.target,
                    "importance": relation.importance,
                    "confidence": relation.confidence,
                }
                for relation in adaptation.relations
            ],
        }
    )
    return aligned, alignment


def story_map_as_dict(story_map: StoryMap) -> dict[str, Any]:
    return _map_payload(story_map)
