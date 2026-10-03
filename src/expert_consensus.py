from __future__ import annotations

import math
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from src.expert_agreement_transfer import ExpertAgreementAnalyzer
from src.expert_corpus import ExpertCorpusStore


CONSENSUS_VERSION = 1


def _jaccard(left: set[str], right: set[str]) -> float:
    union = left | right
    if not union:
        return 1.0
    return len(left & right) / len(union)


def _rounded(value: float) -> float:
    return round(float(value), 4)


class ExpertConsensusAnalyzer:
    """Агрегирует только structural labels, не создавая общий вкусовой score.

    Источник gold и его silence-policy совпадают с `ExpertAgreementAnalyzer`:
    если эксперт не размечал `case_id + dimension`, он не участвует в этом scope.
    """

    def __init__(self, store: ExpertCorpusStore) -> None:
        self.store = store
        self.agreement = ExpertAgreementAnalyzer(store)

    def analyze(
        self,
        *,
        split: str = "blind",
        min_gold_confidence: float = 0.5,
        require_supporting_evidence: bool = True,
    ) -> dict[str, Any]:
        split = self.agreement._validate_split(split)
        rows = self.agreement._gold_rows(
            split=split,
            min_gold_confidence=min_gold_confidence,
            require_supporting_evidence=require_supporting_evidence,
        )

        labels: dict[tuple[str, str, str], set[str]] = defaultdict(set)
        expert_names: dict[str, str] = {}
        experts_by_scope: dict[tuple[str, str], set[str]] = defaultdict(set)
        for case_id, expert_id, display_name, dimension, change_type in rows:
            case_id = str(case_id)
            expert_id = str(expert_id)
            dimension = str(dimension)
            expert_names[expert_id] = str(display_name)
            labels[(expert_id, case_id, dimension)].add(str(change_type))
            experts_by_scope[(case_id, dimension)].add(expert_id)

        consensus: list[dict[str, Any]] = []
        for (case_id, dimension), experts in sorted(experts_by_scope.items()):
            ordered = sorted(experts)
            if len(ordered) < 2:
                continue
            vote_counts: dict[str, int] = defaultdict(int)
            label_sets: dict[str, set[str]] = {}
            for expert_id in ordered:
                current = labels[(expert_id, case_id, dimension)]
                label_sets[expert_id] = current
                for change_type in current:
                    vote_counts[change_type] += 1

            majority_threshold = math.floor(len(ordered) / 2) + 1
            majority_change_types = sorted(
                change_type
                for change_type, count in vote_counts.items()
                if count >= majority_threshold
            )
            unanimous = len(
                {tuple(sorted(current)) for current in label_sets.values()}
            ) == 1
            consensus.append(
                {
                    "case_id": case_id,
                    "dimension": dimension,
                    "expert_count": len(ordered),
                    "expert_ids": ordered,
                    "unanimous": unanimous,
                    "majority_threshold": majority_threshold,
                    "majority_change_types": majority_change_types,
                    "change_type_votes": {
                        change_type: vote_counts[change_type]
                        for change_type in sorted(vote_counts)
                    },
                }
            )

        leave_one_out: list[dict[str, Any]] = []
        for expert_id in sorted(expert_names):
            scope_rows: list[dict[str, Any]] = []
            for (case_id, dimension), experts in sorted(experts_by_scope.items()):
                if expert_id not in experts or len(experts) < 2:
                    continue
                peer_ids = sorted(experts - {expert_id})
                peer_votes: dict[str, int] = defaultdict(int)
                for peer_id in peer_ids:
                    for change_type in labels[(peer_id, case_id, dimension)]:
                        peer_votes[change_type] += 1

                majority_threshold = math.floor(len(peer_ids) / 2) + 1
                peer_majority = {
                    change_type
                    for change_type, count in peer_votes.items()
                    if count >= majority_threshold
                }
                own = labels[(expert_id, case_id, dimension)]
                scope_rows.append(
                    {
                        "case_id": case_id,
                        "dimension": dimension,
                        "peer_count": len(peer_ids),
                        "peer_majority_change_types": sorted(peer_majority),
                        "expert_change_types": sorted(own),
                        "exact_match": own == peer_majority,
                        "jaccard": _rounded(_jaccard(own, peer_majority)),
                    }
                )

            exact_count = sum(1 for row in scope_rows if row["exact_match"])
            jaccards = [float(row["jaccard"]) for row in scope_rows]
            leave_one_out.append(
                {
                    "expert_id": expert_id,
                    "display_name": expert_names[expert_id],
                    "shared_scope_count": len(scope_rows),
                    "exact_match_count": exact_count,
                    "exact_match_rate": (
                        _rounded(exact_count / len(scope_rows))
                        if scope_rows
                        else None
                    ),
                    "mean_jaccard": (
                        _rounded(sum(jaccards) / len(jaccards))
                        if jaccards
                        else None
                    ),
                    "scopes": scope_rows,
                }
            )

        return {
            "version": CONSENSUS_VERSION,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "split": split,
            "gold_policy": {
                "min_gold_confidence": min_gold_confidence,
                "require_supporting_evidence": require_supporting_evidence,
            },
            "scoring_policy": {
                "silence_is_disagreement": False,
                "compare_only_annotated_case_dimensions": True,
                "consensus_is_ground_truth": False,
                "preference_score": None,
                "winner": None,
            },
            "expert_count": len(expert_names),
            "consensus_scope_count": len(consensus),
            "consensus": consensus,
            "leave_one_expert_out": leave_one_out,
            "contains_claim_text": False,
            "contains_expert_interpretation": False,
            "research_only": True,
        }
