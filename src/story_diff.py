from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

from src.adaptation_analysis import AdaptationValidationError


NODE_KINDS = {
    "event",
    "character",
    "motivation",
    "relationship",
    "worldbuilding",
    "theme",
    "ending",
}

RELATION_KINDS = {
    "causes",
    "motivates",
    "explains",
    "depends_on",
    "relates_to",
}

NODE_DIMENSIONS = {
    "event": "plot",
    "character": "characters",
    "motivation": "motivation",
    "relationship": "relationships",
    "worldbuilding": "worldbuilding",
    "theme": "themes",
    "ending": "ending",
}

RELATION_DIMENSIONS = {
    "causes": "causal_coherence",
    "motivates": "motivation",
    "explains": "causal_coherence",
    "depends_on": "causal_coherence",
    "relates_to": "relationships",
}


def _bounded(value, *, field_name: str) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise AdaptationValidationError(f"{field_name} должен быть числом") from exc
    if not 0.0 <= parsed <= 1.0:
        raise AdaptationValidationError(
            f"{field_name} должен быть в диапазоне 0..1"
        )
    return parsed


def _clean(value, *, field_name: str, limit: int = 500) -> str:
    text = " ".join(str(value or "").strip().split())
    if not text:
        raise AdaptationValidationError(f"{field_name} не должен быть пустым")
    if len(text) > limit:
        raise AdaptationValidationError(
            f"{field_name} длиннее допустимых {limit} символов"
        )
    return text


def _stable_id(*parts: str) -> str:
    raw = "\x1f".join(parts).encode("utf-8")
    return "auto-" + hashlib.sha1(raw).hexdigest()[:20]


@dataclass(frozen=True)
class StoryNode:
    key: str
    kind: str
    label: str
    importance: float = 0.5
    confidence: float = 0.7
    maps_from: tuple[str, ...] = field(default_factory=tuple)

    @classmethod
    def from_dict(cls, payload: dict) -> "StoryNode":
        kind = str(payload.get("kind") or "").strip()
        if kind not in NODE_KINDS:
            raise AdaptationValidationError(f"Неизвестный kind StoryNode: {kind!r}")
        maps_from = tuple(
            dict.fromkeys(
                _clean(value, field_name="maps_from", limit=160)
                for value in payload.get("maps_from") or []
            )
        )
        return cls(
            key=_clean(payload.get("key"), field_name="StoryNode.key", limit=160),
            kind=kind,
            label=_clean(payload.get("label"), field_name="StoryNode.label"),
            importance=_bounded(
                payload.get("importance", 0.5),
                field_name="StoryNode.importance",
            ),
            confidence=_bounded(
                payload.get("confidence", 0.7),
                field_name="StoryNode.confidence",
            ),
            maps_from=maps_from,
        )


@dataclass(frozen=True)
class StoryRelation:
    kind: str
    source: str
    target: str
    importance: float = 0.5
    confidence: float = 0.7

    @classmethod
    def from_dict(cls, payload: dict) -> "StoryRelation":
        kind = str(payload.get("kind") or "").strip()
        if kind not in RELATION_KINDS:
            raise AdaptationValidationError(
                f"Неизвестный kind StoryRelation: {kind!r}"
            )
        return cls(
            kind=kind,
            source=_clean(
                payload.get("source"),
                field_name="StoryRelation.source",
                limit=160,
            ),
            target=_clean(
                payload.get("target"),
                field_name="StoryRelation.target",
                limit=160,
            ),
            importance=_bounded(
                payload.get("importance", 0.5),
                field_name="StoryRelation.importance",
            ),
            confidence=_bounded(
                payload.get("confidence", 0.7),
                field_name="StoryRelation.confidence",
            ),
        )

    @property
    def signature(self) -> tuple[str, str, str]:
        return (self.kind, self.source, self.target)


@dataclass(frozen=True)
class StoryMap:
    map_id: str
    language: str | None
    nodes: tuple[StoryNode, ...]
    relations: tuple[StoryRelation, ...]

    @classmethod
    def from_dict(cls, payload: dict) -> "StoryMap":
        nodes = tuple(
            StoryNode.from_dict(item)
            for item in payload.get("nodes") or []
        )
        relations = tuple(
            StoryRelation.from_dict(item)
            for item in payload.get("relations") or []
        )
        keys = {node.key for node in nodes}
        if len(keys) != len(nodes):
            raise AdaptationValidationError("StoryMap.node keys должны быть уникальны")
        for relation in relations:
            if relation.source not in keys or relation.target not in keys:
                raise AdaptationValidationError(
                    "StoryRelation ссылается на отсутствующий StoryNode"
                )
        return cls(
            map_id=_clean(payload.get("map_id"), field_name="StoryMap.map_id", limit=180),
            language=(
                " ".join(str(payload.get("language") or "").strip().split())
                or None
            ),
            nodes=nodes,
            relations=relations,
        )


class StoryDiffAnalyzer:
    """Строит наблюдения о структурных изменениях между двумя story maps.

    Текстовый extractor намеренно вынесен за пределы класса. Он может быть
    ручным, LLM/NLP или гибридным. После того как RU/EN summaries сведены к
    каноническим key, этот diff детерминирован и воспроизводим.
    """

    @staticmethod
    def _annotation(
        *,
        annotation_id: str,
        layer: str,
        dimension: str,
        change_type: str,
        claim: str,
        severity: float,
        confidence: float,
        parent_id: str | None = None,
        tags: list[str] | None = None,
    ) -> dict:
        return {
            "annotation_id": annotation_id,
            "layer": layer,
            "dimension": dimension,
            "change_type": change_type,
            "claim": claim,
            "severity": round(max(0.0, min(1.0, severity)), 4),
            "confidence": round(max(0.0, min(1.0, confidence)), 4),
            "polarity": 0.0,
            "parent_id": parent_id,
            "tags": tags or [],
        }

    @classmethod
    def compare(cls, source: StoryMap, adaptation: StoryMap) -> list[dict]:
        source_nodes = {node.key: node for node in source.nodes}
        adaptation_nodes = {node.key: node for node in adaptation.nodes}

        represented_by: dict[str, StoryNode] = {}
        for node in adaptation.nodes:
            if node.key in source_nodes:
                represented_by[node.key] = node
            for source_key in node.maps_from:
                if source_key in source_nodes:
                    represented_by[source_key] = node

        result: list[dict] = []
        removal_ids: dict[str, str] = {}

        for key, source_node in source_nodes.items():
            mapped = represented_by.get(key)
            if mapped is None:
                annotation_id = _stable_id(
                    source.map_id,
                    adaptation.map_id,
                    "removed",
                    key,
                )
                removal_ids[key] = annotation_id
                result.append(
                    cls._annotation(
                        annotation_id=annotation_id,
                        layer="observation",
                        dimension=NODE_DIMENSIONS[source_node.kind],
                        change_type="removed",
                        claim=(
                            f"Элемент первоисточника «{source_node.label}» "
                            "не представлен в карте экранизации."
                        ),
                        severity=source_node.importance,
                        confidence=min(source_node.confidence, 0.9),
                        tags=["story_diff", "source_element_missing"],
                    )
                )
                continue

            if len(mapped.maps_from) > 1 and key in mapped.maps_from:
                result.append(
                    cls._annotation(
                        annotation_id=_stable_id(
                            source.map_id,
                            adaptation.map_id,
                            "merged",
                            key,
                            mapped.key,
                        ),
                        layer="observation",
                        dimension=NODE_DIMENSIONS[source_node.kind],
                        change_type="merged",
                        claim=(
                            f"Элемент «{source_node.label}» объединён в экранизации "
                            f"с другими элементами в «{mapped.label}»."
                        ),
                        severity=max(source_node.importance, mapped.importance) * 0.75,
                        confidence=min(source_node.confidence, mapped.confidence),
                        tags=["story_diff", "merged_element"],
                    )
                )

        mapped_source_keys = set(represented_by)
        for key, adaptation_node in adaptation_nodes.items():
            if key in source_nodes or adaptation_node.maps_from:
                continue
            result.append(
                cls._annotation(
                    annotation_id=_stable_id(
                        source.map_id,
                        adaptation.map_id,
                        "added",
                        key,
                    ),
                    layer="observation",
                    dimension=NODE_DIMENSIONS[adaptation_node.kind],
                    change_type="added",
                    claim=(
                        f"В экранизацию добавлен новый элемент "
                        f"«{adaptation_node.label}», отсутствующий в карте первоисточника."
                    ),
                    severity=adaptation_node.importance,
                    confidence=min(adaptation_node.confidence, 0.9),
                    tags=["story_diff", "adaptation_element_added"],
                )
            )

        # Для relation diff нормализуем узлы экранизации обратно к одному
        # каноническому source key, если это однозначно возможно.
        canonical_adaptation_key: dict[str, str] = {}
        for node in adaptation.nodes:
            if node.key in source_nodes:
                canonical_adaptation_key[node.key] = node.key
            elif len(node.maps_from) == 1 and node.maps_from[0] in source_nodes:
                canonical_adaptation_key[node.key] = node.maps_from[0]

        adaptation_relations: set[tuple[str, str, str]] = set()
        for relation in adaptation.relations:
            source_key = canonical_adaptation_key.get(relation.source)
            target_key = canonical_adaptation_key.get(relation.target)
            if source_key and target_key:
                adaptation_relations.add((relation.kind, source_key, target_key))

        for relation in source.relations:
            if relation.signature in adaptation_relations:
                continue

            source_present = relation.source in mapped_source_keys
            target_present = relation.target in mapped_source_keys
            if not source_present and not target_present:
                continue

            source_label = source_nodes[relation.source].label
            target_label = source_nodes[relation.target].label
            parent_id = (
                removal_ids.get(relation.source)
                or removal_ids.get(relation.target)
            )
            result.append(
                cls._annotation(
                    annotation_id=_stable_id(
                        source.map_id,
                        adaptation.map_id,
                        "relation_removed",
                        relation.kind,
                        relation.source,
                        relation.target,
                    ),
                    layer="structural_consequence",
                    dimension=RELATION_DIMENSIONS[relation.kind],
                    change_type="removed",
                    claim=(
                        f"В карте экранизации не подтверждена связь "
                        f"{relation.kind}: «{source_label}» → «{target_label}»."
                    ),
                    severity=relation.importance,
                    confidence=min(relation.confidence, 0.85),
                    parent_id=parent_id,
                    tags=["story_diff", "relation_loss", relation.kind],
                )
            )

        return result
