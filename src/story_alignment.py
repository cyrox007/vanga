from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any, Iterable

from src.adaptation_analysis import AdaptationValidationError
from src.story_diff import StoryDiffAnalyzer, StoryMap


ALIGNMENT_METHOD = "deterministic-story-alignment"
ALIGNMENT_VERSION = "1"

_RU_TRANSLIT = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e",
    "ё": "e", "ж": "zh", "з": "z", "и": "i", "й": "i", "к": "k",
    "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r",
    "с": "s", "т": "t", "у": "u", "ф": "f", "х": "kh", "ц": "ts",
    "ч": "ch", "ш": "sh", "щ": "shch", "ъ": "", "ы": "y", "ь": "",
    "э": "e", "ю": "yu", "я": "ya",
}


def _clean(value: Any, *, field_name: str, limit: int = 500) -> str:
    text = " ".join(str(value or "").strip().split())
    if not text:
        raise AdaptationValidationError(f"{field_name} не должен быть пустым")
    if len(text) > limit:
        raise AdaptationValidationError(
            f"{field_name} длиннее допустимых {limit} символов"
        )
    return text


def _stable_key(prefix: str, *parts: str) -> str:
    raw = "\x1f".join(parts).encode("utf-8")
    return f"{prefix}-" + hashlib.sha1(raw).hexdigest()[:20]


def _normalize_label(value: str) -> str:
    text = value.casefold().replace("ё", "е")
    text = re.sub(r"[^0-9a-zа-я]+", " ", text, flags=re.IGNORECASE)
    return " ".join(text.split())


def _transliterate_ru(value: str) -> str:
    normalized = _normalize_label(value)
    return "".join(_RU_TRANSLIT.get(char, char) for char in normalized)


def _confidence_union(values: Iterable[float]) -> float:
    remainder = 1.0
    seen = False
    for raw in values:
        seen = True
        value = max(0.0, min(0.98, float(raw)))
        remainder *= 1.0 - value
    return round(min(0.98, 1.0 - remainder), 4) if seen else 0.0


def _story_map_payload(story_map: StoryMap) -> dict[str, Any]:
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


def story_map_from_payload(payload: dict[str, Any]) -> StoryMap:
    """Читает как raw StoryMap, так и JSON результата storymap_extract.py."""
    if not isinstance(payload, dict):
        raise AdaptationValidationError("StoryMap payload должен быть JSON-объектом")
    nested = payload.get("story_map")
    if isinstance(nested, dict):
        payload = nested
    return StoryMap.from_dict(payload)


@dataclass(frozen=True)
class ExplicitAlias:
    alias_id: str
    kind: str
    labels: tuple[str, ...]
    canonical_label: str | None = None

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ExplicitAlias":
        labels = tuple(
            dict.fromkeys(
                _clean(value, field_name="alias label", limit=500)
                for value in (payload.get("labels") or [])
            )
        )
        if len(labels) < 2:
            raise AdaptationValidationError("ExplicitAlias.labels должен содержать минимум 2 значения")
        kind = _clean(payload.get("kind"), field_name="alias kind", limit=80)
        canonical_label = str(payload.get("canonical_label") or "").strip() or None
        if canonical_label is not None:
            canonical_label = _clean(
                canonical_label,
                field_name="canonical_label",
                limit=500,
            )
        return cls(
            alias_id=_clean(payload.get("alias_id"), field_name="alias_id", limit=160),
            kind=kind,
            labels=labels,
            canonical_label=canonical_label,
        )


@dataclass(frozen=True)
class ExplicitNodeMatch:
    adaptation_key: str
    source_keys: tuple[str, ...]
    confidence: float = 1.0
    note: str | None = None

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ExplicitNodeMatch":
        adaptation_key = _clean(
            payload.get("adaptation_key"),
            field_name="adaptation_key",
            limit=160,
        )
        source_keys = tuple(
            dict.fromkeys(
                _clean(value, field_name="source_key", limit=160)
                for value in (payload.get("source_keys") or [])
            )
        )
        if not source_keys:
            raise AdaptationValidationError("source_keys не должен быть пустым")
        try:
            confidence = float(payload.get("confidence", 1.0))
        except (TypeError, ValueError) as exc:
            raise AdaptationValidationError("confidence explicit match должен быть числом") from exc
        if not 0.0 <= confidence <= 1.0:
            raise AdaptationValidationError("confidence explicit match должен быть в диапазоне 0..1")
        note = str(payload.get("note") or "").strip() or None
        return cls(
            adaptation_key=adaptation_key,
            source_keys=source_keys,
            confidence=confidence,
            note=note,
        )


class BilingualStoryMapMerger:
    """Объединяет RU/EN raw maps одной и той же стороны в canonical StoryMap.

    Безопасность важнее recall:
    - exact normalized label допускается для любого kind;
    - RU→Latin identity автоматически используется только для character;
    - events/motivations между языками без exact label или explicit alias не
      объединяются, чтобы не придумывать semantic identity.
    """

    @staticmethod
    def _alias_index(aliases: Iterable[ExplicitAlias | dict[str, Any]] | None) -> dict[tuple[str, str], ExplicitAlias]:
        result: dict[tuple[str, str], ExplicitAlias] = {}
        for raw in aliases or []:
            alias = raw if isinstance(raw, ExplicitAlias) else ExplicitAlias.from_dict(raw)
            for label in alias.labels:
                key = (alias.kind, _normalize_label(label))
                current = result.get(key)
                if current is not None and current.alias_id != alias.alias_id:
                    raise AdaptationValidationError(
                        f"Label {label!r} входит в несколько explicit alias groups"
                    )
                result[key] = alias
        return result

    @staticmethod
    def _identity(kind: str, label: str, alias_index: dict[tuple[str, str], ExplicitAlias]) -> tuple[str, str | None, str]:
        normalized = _normalize_label(label)
        explicit = alias_index.get((kind, normalized))
        if explicit is not None:
            return (
                f"explicit:{kind}:{explicit.alias_id}",
                explicit.canonical_label,
                "explicit_alias",
            )
        if kind == "character":
            return (
                f"character:{_transliterate_ru(label)}",
                None,
                "character_transliteration",
            )
        return (f"{kind}:{normalized}", None, "exact_normalized_label")

    @classmethod
    def merge(
        cls,
        story_maps: Iterable[StoryMap],
        *,
        canonical_map_id: str,
        aliases: Iterable[ExplicitAlias | dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        maps = list(story_maps)
        if not maps:
            raise AdaptationValidationError("Для bilingual merge нужен хотя бы один StoryMap")
        canonical_map_id = _clean(
            canonical_map_id,
            field_name="canonical_map_id",
            limit=180,
        )
        alias_index = cls._alias_index(aliases)

        groups: dict[str, list[tuple[StoryMap, Any, str]]] = {}
        identity_by_node: dict[tuple[str, str], str] = {}
        for story_map in maps:
            for node in story_map.nodes:
                identity, canonical_label, method = cls._identity(
                    node.kind,
                    node.label,
                    alias_index,
                )
                groups.setdefault(identity, []).append((story_map, node, method))
                identity_by_node[(story_map.map_id, node.key)] = identity

        canonical_key_by_identity: dict[str, str] = {}
        canonical_nodes: list[dict[str, Any]] = []
        merge_evidence: list[dict[str, Any]] = []
        for identity in sorted(groups):
            members = groups[identity]
            kinds = {node.kind for _, node, _ in members}
            if len(kinds) != 1:
                raise AdaptationValidationError(
                    f"Bilingual merge identity {identity} смешивает разные node kind"
                )
            kind = next(iter(kinds))
            canonical_key = _stable_key(
                "canonical",
                canonical_map_id,
                identity,
            )
            canonical_key_by_identity[identity] = canonical_key

            explicit_label = None
            for story_map, node, _method in members:
                alias = alias_index.get((kind, _normalize_label(node.label)))
                if alias and alias.canonical_label:
                    explicit_label = alias.canonical_label
                    break
            representative = explicit_label or members[0][1].label
            canonical_nodes.append(
                {
                    "key": canonical_key,
                    "kind": kind,
                    "label": representative,
                    "importance": round(max(float(node.importance) for _, node, _ in members), 4),
                    "confidence": _confidence_union(node.confidence for _, node, _ in members),
                    "maps_from": [],
                }
            )
            merge_evidence.append(
                {
                    "canonical_key": canonical_key,
                    "kind": kind,
                    "members": [
                        {
                            "map_id": story_map.map_id,
                            "language": story_map.language,
                            "node_key": node.key,
                            "label": node.label,
                            "method": method,
                            "confidence": node.confidence,
                        }
                        for story_map, node, method in members
                    ],
                }
            )

        relation_groups: dict[tuple[str, str, str], list[Any]] = {}
        for story_map in maps:
            for relation in story_map.relations:
                source_identity = identity_by_node[(story_map.map_id, relation.source)]
                target_identity = identity_by_node[(story_map.map_id, relation.target)]
                source = canonical_key_by_identity[source_identity]
                target = canonical_key_by_identity[target_identity]
                if source == target:
                    continue
                signature = (relation.kind, source, target)
                relation_groups.setdefault(signature, []).append(relation)

        canonical_relations = [
            {
                "kind": kind,
                "source": source,
                "target": target,
                "importance": round(max(float(row.importance) for row in rows), 4),
                "confidence": _confidence_union(row.confidence for row in rows),
            }
            for (kind, source, target), rows in sorted(relation_groups.items())
        ]

        canonical = StoryMap.from_dict(
            {
                "map_id": canonical_map_id,
                "language": "canonical",
                "nodes": canonical_nodes,
                "relations": canonical_relations,
            }
        )
        return {
            "method": ALIGNMENT_METHOD,
            "method_version": ALIGNMENT_VERSION,
            "stage": "bilingual_merge",
            "story_map": _story_map_payload(canonical),
            "merge_evidence": merge_evidence,
            "source_maps": [story_map.map_id for story_map in maps],
            "research_only": True,
        }


class SourceAdaptationAligner:
    """Сопоставляет canonical source и adaptation maps и заполняет maps_from.

    Автоматический matching намеренно ограничен:
    - exact normalized label разрешён для любого совместимого kind;
    - transliteration/fuzzy разрешены только для character;
    - event/motivation semantic similarity без explicit match не используется.
    """

    CHARACTER_FUZZY_THRESHOLD = 0.90
    CHARACTER_AMBIGUITY_GAP = 0.06

    @staticmethod
    def _automatic_score(source_node, adaptation_node) -> tuple[float, str] | None:
        if source_node.kind != adaptation_node.kind:
            return None
        source_normalized = _normalize_label(source_node.label)
        adaptation_normalized = _normalize_label(adaptation_node.label)
        if source_normalized == adaptation_normalized:
            return (0.99, "exact_normalized_label")
        if source_node.kind != "character":
            return None

        source_translit = _transliterate_ru(source_node.label)
        adaptation_translit = _transliterate_ru(adaptation_node.label)
        if source_translit == adaptation_translit:
            return (0.96, "character_transliteration")
        if min(len(source_translit), len(adaptation_translit)) < 4:
            return None
        ratio = SequenceMatcher(None, source_translit, adaptation_translit).ratio()
        if ratio < SourceAdaptationAligner.CHARACTER_FUZZY_THRESHOLD:
            return None
        return (round(0.85 + (ratio - 0.90) * 0.8, 4), "character_fuzzy")

    @classmethod
    def align(
        cls,
        source: StoryMap,
        adaptation: StoryMap,
        *,
        explicit_matches: Iterable[ExplicitNodeMatch | dict[str, Any]] | None = None,
        derived_map_id: str | None = None,
    ) -> dict[str, Any]:
        source_by_key = {node.key: node for node in source.nodes}
        adaptation_by_key = {node.key: node for node in adaptation.nodes}
        mapped_sources: set[str] = set()
        matched_by_adaptation: dict[str, list[str]] = {}
        evidence: list[dict[str, Any]] = []
        ambiguous: list[dict[str, Any]] = []

        for raw in explicit_matches or []:
            match = raw if isinstance(raw, ExplicitNodeMatch) else ExplicitNodeMatch.from_dict(raw)
            adaptation_node = adaptation_by_key.get(match.adaptation_key)
            if adaptation_node is None:
                raise AdaptationValidationError(
                    f"Explicit match: неизвестный adaptation key {match.adaptation_key}"
                )
            resolved_sources = []
            for source_key in match.source_keys:
                source_node = source_by_key.get(source_key)
                if source_node is None:
                    raise AdaptationValidationError(
                        f"Explicit match: неизвестный source key {source_key}"
                    )
                if source_node.kind != adaptation_node.kind:
                    raise AdaptationValidationError(
                        "Explicit match нельзя создавать между разными StoryNode kind"
                    )
                if source_key in mapped_sources:
                    raise AdaptationValidationError(
                        f"Source node {source_key} уже сопоставлен с другим adaptation node"
                    )
                resolved_sources.append(source_key)
            matched_by_adaptation[adaptation_node.key] = resolved_sources
            mapped_sources.update(resolved_sources)
            evidence.append(
                {
                    "adaptation_key": adaptation_node.key,
                    "source_keys": resolved_sources,
                    "kind": adaptation_node.kind,
                    "method": "explicit",
                    "confidence": round(match.confidence, 4),
                    "note": match.note,
                }
            )

        # Автоматический matching — только для узлов, не закрытых explicit rules.
        for adaptation_node in adaptation.nodes:
            if adaptation_node.key in matched_by_adaptation:
                continue
            candidates: list[tuple[float, str, Any]] = []
            for source_node in source.nodes:
                if source_node.key in mapped_sources:
                    continue
                score = cls._automatic_score(source_node, adaptation_node)
                if score is None:
                    continue
                confidence, method = score
                candidates.append((confidence, method, source_node))
            candidates.sort(key=lambda row: (-row[0], row[2].key))
            if not candidates:
                continue
            best = candidates[0]
            if len(candidates) > 1 and best[0] - candidates[1][0] < cls.CHARACTER_AMBIGUITY_GAP:
                ambiguous.append(
                    {
                        "adaptation_key": adaptation_node.key,
                        "adaptation_label": adaptation_node.label,
                        "kind": adaptation_node.kind,
                        "candidates": [
                            {
                                "source_key": row[2].key,
                                "source_label": row[2].label,
                                "confidence": row[0],
                                "method": row[1],
                            }
                            for row in candidates[:5]
                        ],
                    }
                )
                continue
            confidence, method, source_node = best
            matched_by_adaptation[adaptation_node.key] = [source_node.key]
            mapped_sources.add(source_node.key)
            evidence.append(
                {
                    "adaptation_key": adaptation_node.key,
                    "source_keys": [source_node.key],
                    "kind": adaptation_node.kind,
                    "method": method,
                    "confidence": confidence,
                    "note": None,
                }
            )

        derived_id = derived_map_id or _stable_key(
            "aligned",
            source.map_id,
            adaptation.map_id,
            ALIGNMENT_METHOD,
            ALIGNMENT_VERSION,
        )
        derived_payload = {
            "map_id": derived_id,
            "language": adaptation.language or "canonical",
            "nodes": [
                {
                    "key": node.key,
                    "kind": node.kind,
                    "label": node.label,
                    "importance": node.importance,
                    "confidence": node.confidence,
                    "maps_from": matched_by_adaptation.get(node.key, list(node.maps_from)),
                }
                for node in adaptation.nodes
            ],
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
        derived = StoryMap.from_dict(derived_payload)
        annotations = StoryDiffAnalyzer.compare(source, derived)

        matched_adaptation = set(matched_by_adaptation)
        return {
            "method": ALIGNMENT_METHOD,
            "method_version": ALIGNMENT_VERSION,
            "stage": "source_adaptation_alignment",
            "source_map_id": source.map_id,
            "adaptation_map_id": adaptation.map_id,
            "story_map": _story_map_payload(derived),
            "alignment_evidence": evidence,
            "ambiguous_matches": ambiguous,
            "unmatched_source_keys": [
                node.key for node in source.nodes if node.key not in mapped_sources
            ],
            "unmatched_adaptation_keys": [
                node.key for node in adaptation.nodes if node.key not in matched_adaptation
            ],
            "story_diff": annotations,
            "research_only": True,
        }
