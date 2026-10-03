from __future__ import annotations

import math
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from src.expert_corpus import ExpertCorpusStore, ExpertCorpusValidationError


AGREEMENT_VERSION = 1
_ALLOWED_SPLITS = {"train", "development", "blind", "external_transfer"}


def _bounded(value: Any, *, field_name: str) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ExpertCorpusValidationError(f"{field_name} должен быть числом") from exc
    if not 0.0 <= parsed <= 1.0:
        raise ExpertCorpusValidationError(f"{field_name} должен быть в диапазоне 0..1")
    return parsed


def _validate_split(value: str) -> str:
    split = str(value or "").strip().casefold()
    if split not in _ALLOWED_SPLITS:
        raise ExpertCorpusValidationError(
            "split должен быть train, development, blind или external_transfer"
        )
    return split


def _jaccard(left: set[str], right: set[str]) -> float:
    union = left | right
    if not union:
        return 1.0
    return len(left & right) / len(union)


def _rounded(value: float) -> float:
    return round(float(value), 4)


class ExpertAgreementAnalyzer:
    """Сравнивает структурную разметку экспертов без усреднения их вкуса.

    Scope сравнения — только ``case_id + dimension``, которые реально размечены
    обоими экспертами. Отсутствие claim у одного эксперта не считается ни
    согласием, ни несогласием.
    """

    def __init__(self, store: ExpertCorpusStore) -> None:
        self.store = store

    def _rows(
        self,
        *,
        split: str,
        min_confidence: float,
        require_supporting_evidence: bool,
    ) -> list[tuple]:
        evidence_clause = """
            AND EXISTS (
                SELECT 1 FROM expert_evidence e
                WHERE e.claim_id = c.claim_id AND e.polarity = 'supporting'
            )
        """ if require_supporting_evidence else ""
        return self.store.conn.execute(
            f"""
            SELECT DISTINCT
                ec.case_id,
                m.expert_id,
                p.display_name,
                c.dimension,
                c.change_type
            FROM expert_claims c
            JOIN expert_cases ec ON ec.case_id = c.case_id
            JOIN expert_materials m ON m.material_id = c.material_id
            JOIN expert_profiles p ON p.expert_id = m.expert_id
            WHERE ec.split = ?
              AND c.confidence >= ?
              {evidence_clause}
            ORDER BY ec.case_id, c.dimension, m.expert_id, c.change_type
            """,
            [split, min_confidence],
        ).fetchall()

    def analyze(
        self,
        *,
        split: str = "development",
        min_confidence: float = 0.5,
        require_supporting_evidence: bool = True,
    ) -> dict[str, Any]:
        split = _validate_split(split)
        min_confidence = _bounded(min_confidence, field_name="min_confidence")
        rows = self._rows(
            split=split,
            min_confidence=min_confidence,
            require_supporting_evidence=require_supporting_evidence,
        )

        expert_names: dict[str, str] = {}
        labels: dict[tuple[str, str, str], set[str]] = defaultdict(set)
        experts_by_scope: dict[tuple[str, str], set[str]] = defaultdict(set)
        for case_id, expert_id, display_name, dimension, change_type in rows:
            case_id = str(case_id)
            expert_id = str(expert_id)
            dimension = str(dimension)
            expert_names[expert_id] = str(display_name)
            labels[(expert_id, case_id, dimension)].add(str(change_type))
            experts_by_scope[(case_id, dimension)].add(expert_id)

        pair_scopes: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        consensus_rows: list[dict[str, Any]] = []
        for (case_id, dimension), experts in sorted(experts_by_scope.items()):
            ordered = sorted(experts)
            if len(ordered) < 2:
                continue

            vote_counts: dict[str, int] = defaultdict(int)
            expert_label_sets: dict[str, set[str]] = {}
            for expert_id in ordered:
                current = labels[(expert_id, case_id, dimension)]
                expert_label_sets[expert_id] = current
                for change_type in current:
                    vote_counts[change_type] += 1

            unanimous = len({tuple(sorted(value)) for value in expert_label_sets.values()}) == 1
            consensus_rows.append(
                {
                    "case_id": case_id,
                    "dimension": dimension,
                    "expert_count": len(ordered),
                    "unanimous": unanimous,
                    "change_type_votes": {
                        key: vote_counts[key] for key in sorted(vote_counts)
                    },
                }
            )

            for left_index, left_id in enumerate(ordered):
                for right_id in ordered[left_index + 1 :]:
                    left = expert_label_sets[left_id]
                    right = expert_label_sets[right_id]
                    pair_scopes[(left_id, right_id)].append(
                        {
                            "case_id": case_id,
                            "dimension": dimension,
                            "exact": left == right,
                            "jaccard": _jaccard(left, right),
                            "shared": sorted(left & right),
                            "only_left": sorted(left - right),
                            "only_right": sorted(right - left),
                        }
                    )

        pairwise: list[dict[str, Any]] = []
        for (left_id, right_id), scopes in sorted(pair_scopes.items()):
            exact_count = sum(1 for item in scopes if item["exact"])
            jaccards = [float(item["jaccard"]) for item in scopes]
            pairwise.append(
                {
                    "left_expert_id": left_id,
                    "left_display_name": expert_names[left_id],
                    "right_expert_id": right_id,
                    "right_display_name": expert_names[right_id],
                    "shared_scope_count": len(scopes),
                    "exact_agreement_count": exact_count,
                    "exact_agreement_rate": _rounded(exact_count / len(scopes)),
                    "mean_jaccard": _rounded(sum(jaccards) / len(jaccards)),
                    "disagreement_scope_count": len(scopes) - exact_count,
                    "scopes": [
                        {
                            **item,
                            "jaccard": _rounded(float(item["jaccard"])),
                        }
                        for item in scopes
                    ],
                }
            )

        leave_one_out: list[dict[str, Any]] = []
        for expert_id in sorted(expert_names):
            scored = 0
            exact = 0
            jaccard_total = 0.0
            for (case_id, dimension), experts in sorted(experts_by_scope.items()):
                if expert_id not in experts or len(experts) < 2:
                    continue
                peers = sorted(experts - {expert_id})
                peer_votes: dict[str, int] = defaultdict(int)
                for peer_id in peers:
                    for change_type in labels[(peer_id, case_id, dimension)]:
                        peer_votes[change_type] += 1
                majority_threshold = math.floor(len(peers) / 2) + 1
                peer_majority = {
                    change_type
                    for change_type, count in peer_votes.items()
                    if count >= majority_threshold
                }
                own = labels[(expert_id, case_id, dimension)]
                scored += 1
                if own == peer_majority:
                    exact += 1
                jaccard_total += _jaccard(own, peer_majority)

            leave_one_out.append(
                {
                    "expert_id": expert_id,
                    "display_name": expert_names[expert_id],
                    "shared_scope_count": scored,
                    "exact_match_with_peer_majority_count": exact,
                    "exact_match_with_peer_majority_rate": (
                        _rounded(exact / scored) if scored else None
                    ),
                    "mean_jaccard_with_peer_majority": (
                        _rounded(jaccard_total / scored) if scored else None
                    ),
                }
            )

        compared_scopes = sum(1 for experts in experts_by_scope.values() if len(experts) >= 2)
        return {
            "version": AGREEMENT_VERSION,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "split": split,
            "policy": {
                "min_confidence": min_confidence,
                "require_supporting_evidence": require_supporting_evidence,
                "silence_is_negative": False,
                "preference_score": None,
            },
            "expert_count": len(expert_names),
            "annotated_scope_count": len(experts_by_scope),
            "compared_scope_count": compared_scopes,
            "pairwise": pairwise,
            "consensus": consensus_rows,
            "leave_one_expert_out": leave_one_out,
            "contains_claim_text": False,
            "contains_expert_interpretation": False,
            "research_only": True,
        }
