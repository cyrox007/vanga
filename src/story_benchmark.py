from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any, Iterable

from src.adaptation_analysis import AdaptationValidationError
from src.story_diff import StoryMap


MATCHABLE_KINDS = {
    "event",
    "motivation",
    "relationship",
    "worldbuilding",
    "theme",
    "ending",
}

BENCHMARK_SPLITS = {"train", "development", "blind"}


def _clean(value: Any, *, field_name: str, limit: int = 500) -> str:
    text = " ".join(str(value or "").strip().split())
    if not text:
        raise AdaptationValidationError(f"{field_name} не должен быть пустым")
    if len(text) > limit:
        raise AdaptationValidationError(
            f"{field_name} длиннее допустимых {limit} символов"
        )
    return text


def _normalize(value: str) -> str:
    text = value.casefold().replace("ё", "е")
    text = re.sub(r"[^0-9a-zа-я]+", " ", text, flags=re.IGNORECASE)
    return " ".join(text.split())


def _tokens(value: str) -> set[str]:
    return {token for token in _normalize(value).split() if len(token) > 1}


def _jaccard(left: set[str], right: set[str]) -> float:
    union = left | right
    if not union:
        return 0.0
    return len(left & right) / len(union)


def _prf(tp: int, fp: int, fn: int) -> dict[str, float | int]:
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = (
        2.0 * precision * recall / (precision + recall)
        if precision + recall
        else 0.0
    )
    return {
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
    }


@dataclass(frozen=True)
class GoldNodeMatch:
    adaptation_key: str
    source_keys: tuple[str, ...]
    note: str | None = None

    @classmethod
    def from_dict(
        cls,
        payload: dict[str, Any],
        *,
        source: StoryMap,
        adaptation: StoryMap,
    ) -> "GoldNodeMatch":
        adaptation_key = _clean(
            payload.get("adaptation_key"),
            field_name="gold adaptation_key",
            limit=160,
        )
        source_keys = tuple(
            dict.fromkeys(
                _clean(value, field_name="gold source_key", limit=160)
                for value in (payload.get("source_keys") or [])
            )
        )
        if not source_keys:
            raise AdaptationValidationError("gold source_keys не должен быть пустым")

        source_by_key = {node.key: node for node in source.nodes}
        adaptation_by_key = {node.key: node for node in adaptation.nodes}
        adaptation_node = adaptation_by_key.get(adaptation_key)
        if adaptation_node is None:
            raise AdaptationValidationError(
                f"Gold match: неизвестный adaptation key {adaptation_key}"
            )
        for source_key in source_keys:
            source_node = source_by_key.get(source_key)
            if source_node is None:
                raise AdaptationValidationError(
                    f"Gold match: неизвестный source key {source_key}"
                )
            if source_node.kind != adaptation_node.kind:
                raise AdaptationValidationError(
                    "Gold match нельзя задавать между разными StoryNode kind"
                )
        note = str(payload.get("note") or "").strip() or None
        return cls(
            adaptation_key=adaptation_key,
            source_keys=source_keys,
            note=note,
        )


@dataclass(frozen=True)
class AlignmentGoldCase:
    case_id: str
    split: str
    source_map_id: str
    adaptation_map_id: str
    matches: tuple[GoldNodeMatch, ...]

    @classmethod
    def from_dict(
        cls,
        payload: dict[str, Any],
        *,
        source: StoryMap,
        adaptation: StoryMap,
    ) -> "AlignmentGoldCase":
        split = str(payload.get("split") or "development").strip().casefold()
        if split not in BENCHMARK_SPLITS:
            raise AdaptationValidationError(
                "benchmark split должен быть train, development или blind"
            )
        source_map_id = _clean(
            payload.get("source_map_id") or source.map_id,
            field_name="source_map_id",
            limit=180,
        )
        adaptation_map_id = _clean(
            payload.get("adaptation_map_id") or adaptation.map_id,
            field_name="adaptation_map_id",
            limit=180,
        )
        if source_map_id != source.map_id or adaptation_map_id != adaptation.map_id:
            raise AdaptationValidationError(
                "Gold case map IDs не соответствуют переданным StoryMap"
            )
        matches = tuple(
            GoldNodeMatch.from_dict(row, source=source, adaptation=adaptation)
            for row in (payload.get("matches") or [])
        )
        seen_adaptation: set[str] = set()
        for match in matches:
            if match.adaptation_key in seen_adaptation:
                raise AdaptationValidationError(
                    f"Gold adaptation key {match.adaptation_key} указан повторно"
                )
            seen_adaptation.add(match.adaptation_key)
        return cls(
            case_id=_clean(payload.get("case_id"), field_name="case_id", limit=180),
            split=split,
            source_map_id=source_map_id,
            adaptation_map_id=adaptation_map_id,
            matches=matches,
        )


class StoryMatchCandidateGenerator:
    """Ранжирует возможные source↔adaptation matches, но не принимает их.

    Candidate score — исследовательский приоритет проверки, а не confidence
    истинности. Метод никогда не меняет ``maps_from``.
    """

    VERSION = "1"

    @staticmethod
    def _character_participants(story_map: StoryMap) -> dict[str, set[str]]:
        character_keys = {
            node.key for node in story_map.nodes if node.kind == "character"
        }
        participants: dict[str, set[str]] = {}
        for relation in story_map.relations:
            if relation.kind != "relates_to":
                continue
            if relation.source in character_keys:
                participants.setdefault(relation.target, set()).add(relation.source)
            if relation.target in character_keys:
                participants.setdefault(relation.source, set()).add(relation.target)
        return participants

    @staticmethod
    def _lexical_score(source_label: str, adaptation_label: str) -> dict[str, float]:
        source_normalized = _normalize(source_label)
        adaptation_normalized = _normalize(adaptation_label)
        token_jaccard = _jaccard(_tokens(source_label), _tokens(adaptation_label))
        sequence_ratio = SequenceMatcher(
            None,
            source_normalized,
            adaptation_normalized,
        ).ratio()
        return {
            "token_jaccard": round(token_jaccard, 4),
            "sequence_ratio": round(sequence_ratio, 4),
            "lexical": round(max(token_jaccard, sequence_ratio), 4),
        }

    @classmethod
    def generate(
        cls,
        source: StoryMap,
        adaptation: StoryMap,
        *,
        known_character_matches: dict[str, str] | None = None,
        top_k: int = 5,
        min_score: float = 0.15,
    ) -> dict[str, Any]:
        if top_k < 1:
            raise ValueError("top_k должен быть положительным")
        if not 0.0 <= min_score <= 1.0:
            raise ValueError("min_score должен быть в диапазоне 0..1")

        source_by_key = {node.key: node for node in source.nodes}
        adaptation_by_key = {node.key: node for node in adaptation.nodes}
        character_matches = dict(known_character_matches or {})
        for adaptation_key, source_key in character_matches.items():
            adaptation_node = adaptation_by_key.get(adaptation_key)
            source_node = source_by_key.get(source_key)
            if adaptation_node is None or source_node is None:
                raise AdaptationValidationError(
                    "known_character_matches содержит неизвестный node key"
                )
            if adaptation_node.kind != "character" or source_node.kind != "character":
                raise AdaptationValidationError(
                    "known_character_matches допускает только character↔character"
                )

        source_participants = cls._character_participants(source)
        adaptation_participants = cls._character_participants(adaptation)
        candidate_groups: list[dict[str, Any]] = []

        for adaptation_node in adaptation.nodes:
            if adaptation_node.kind not in MATCHABLE_KINDS:
                continue
            rows: list[dict[str, Any]] = []
            adaptation_chars = adaptation_participants.get(adaptation_node.key, set())
            mapped_adaptation_chars = {
                character_matches[key]
                for key in adaptation_chars
                if key in character_matches
            }
            for source_node in source.nodes:
                if source_node.kind != adaptation_node.kind:
                    continue
                lexical = cls._lexical_score(source_node.label, adaptation_node.label)
                source_chars = source_participants.get(source_node.key, set())
                participant_overlap = _jaccard(mapped_adaptation_chars, source_chars)
                participant_coverage = (
                    len(mapped_adaptation_chars) / len(adaptation_chars)
                    if adaptation_chars
                    else 0.0
                )
                if mapped_adaptation_chars:
                    score = 0.50 * lexical["lexical"] + 0.50 * participant_overlap
                else:
                    score = 0.75 * lexical["lexical"]
                score = round(min(1.0, score), 4)
                if score < min_score:
                    continue
                rows.append(
                    {
                        "source_key": source_node.key,
                        "source_label": source_node.label,
                        "adaptation_key": adaptation_node.key,
                        "adaptation_label": adaptation_node.label,
                        "kind": adaptation_node.kind,
                        "score": score,
                        "components": {
                            **lexical,
                            "participant_overlap": round(participant_overlap, 4),
                            "known_participant_coverage": round(participant_coverage, 4),
                        },
                    }
                )
            rows.sort(key=lambda row: (-row["score"], row["source_key"]))
            rows = rows[:top_k]
            ambiguous = (
                len(rows) > 1
                and rows[0]["score"] - rows[1]["score"] < 0.08
            )
            candidate_groups.append(
                {
                    "adaptation_key": adaptation_node.key,
                    "adaptation_label": adaptation_node.label,
                    "kind": adaptation_node.kind,
                    "ambiguous": ambiguous,
                    "candidates": rows,
                }
            )

        return {
            "method": "story-match-candidates",
            "method_version": cls.VERSION,
            "source_map_id": source.map_id,
            "adaptation_map_id": adaptation.map_id,
            "auto_accept": False,
            "candidate_groups": candidate_groups,
        }


class StoryAlignmentBenchmark:
    @staticmethod
    def _gold_pairs(case: AlignmentGoldCase) -> set[tuple[str, str]]:
        return {
            (source_key, match.adaptation_key)
            for match in case.matches
            for source_key in match.source_keys
        }

    @staticmethod
    def _predicted_pairs(predicted_map: StoryMap) -> set[tuple[str, str]]:
        return {
            (source_key, node.key)
            for node in predicted_map.nodes
            for source_key in node.maps_from
        }

    @classmethod
    def evaluate(
        cls,
        *,
        source: StoryMap,
        adaptation: StoryMap,
        gold: AlignmentGoldCase | dict[str, Any],
        predicted_map: StoryMap,
    ) -> dict[str, Any]:
        case = (
            gold
            if isinstance(gold, AlignmentGoldCase)
            else AlignmentGoldCase.from_dict(
                gold,
                source=source,
                adaptation=adaptation,
            )
        )
        gold_pairs = cls._gold_pairs(case)
        predicted_pairs = cls._predicted_pairs(predicted_map)
        source_by_key = {node.key: node for node in source.nodes}

        unknown_predicted = sorted(
            source_key
            for source_key, _adaptation_key in predicted_pairs
            if source_key not in source_by_key
        )
        if unknown_predicted:
            raise AdaptationValidationError(
                "Predicted map ссылается на неизвестные source keys: "
                + ", ".join(unknown_predicted)
            )

        tp_pairs = gold_pairs & predicted_pairs
        fp_pairs = predicted_pairs - gold_pairs
        fn_pairs = gold_pairs - predicted_pairs
        overall = _prf(len(tp_pairs), len(fp_pairs), len(fn_pairs))

        by_kind: dict[str, dict[str, float | int]] = {}
        kinds = sorted(
            {
                source_by_key[source_key].kind
                for source_key, _adaptation_key in gold_pairs | predicted_pairs
            }
        )
        for kind in kinds:
            gold_kind = {
                pair for pair in gold_pairs if source_by_key[pair[0]].kind == kind
            }
            predicted_kind = {
                pair for pair in predicted_pairs if source_by_key[pair[0]].kind == kind
            }
            by_kind[kind] = _prf(
                len(gold_kind & predicted_kind),
                len(predicted_kind - gold_kind),
                len(gold_kind - predicted_kind),
            )

        return {
            "case_id": case.case_id,
            "split": case.split,
            "source_map_id": case.source_map_id,
            "adaptation_map_id": case.adaptation_map_id,
            "overall": overall,
            "by_kind": by_kind,
            "gold_pair_count": len(gold_pairs),
            "predicted_pair_count": len(predicted_pairs),
            "false_positive_pairs": [list(pair) for pair in sorted(fp_pairs)],
            "false_negative_pairs": [list(pair) for pair in sorted(fn_pairs)],
        }
