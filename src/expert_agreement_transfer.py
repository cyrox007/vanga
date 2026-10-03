from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from datetime import datetime, timezone
from itertools import combinations
from typing import Any

from src.expert_blind_validation import BlindCasePrediction
from src.expert_corpus import ExpertCorpusStore, ExpertCorpusValidationError


AGREEMENT_TRANSFER_VERSION = 1
EVALUATION_SPLITS = {"development", "blind", "external_transfer"}


def _canonical_json(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _fingerprint(payload: Any) -> str:
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def _bounded(value: Any, *, field_name: str) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ExpertCorpusValidationError(f"{field_name} должен быть числом") from exc
    if not 0.0 <= parsed <= 1.0:
        raise ExpertCorpusValidationError(f"{field_name} должен быть в диапазоне 0..1")
    return parsed


def _clean(value: Any, *, field_name: str, limit: int = 300, required: bool = False) -> str:
    text = " ".join(str(value or "").strip().split())
    if required and not text:
        raise ExpertCorpusValidationError(f"{field_name} не должен быть пустым")
    if len(text) > limit:
        raise ExpertCorpusValidationError(
            f"{field_name} длиннее допустимых {limit} символов"
        )
    return text


def _metric(tp: int, fp: int, fn: int) -> dict[str, float | int]:
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
    }


def _mean(values: list[float]) -> float:
    return round(sum(values) / len(values), 4) if values else 0.0


class ExpertAgreementAnalyzer:
    """Сравнивает только явно размеченные structural classes разных экспертов.

    Отсутствие claim не является ни согласием, ни расхождением. Сравнение
    существует только для одной и той же пары ``case_id + dimension``, которую
    независимо разметили минимум два expert profile.
    """

    def __init__(self, store: ExpertCorpusStore) -> None:
        self.store = store

    @staticmethod
    def _validate_split(split: str) -> str:
        split = str(split or "").strip().casefold()
        if split not in EVALUATION_SPLITS:
            raise ExpertCorpusValidationError(
                "agreement split должен быть development, blind или external_transfer; train запрещён"
            )
        return split

    def _gold_rows(
        self,
        *,
        split: str,
        min_gold_confidence: float,
        require_supporting_evidence: bool,
        expert_id: str | None = None,
    ) -> list[tuple]:
        evidence_clause = """
            AND EXISTS (
                SELECT 1 FROM expert_evidence e
                WHERE e.claim_id = c.claim_id AND e.polarity = 'supporting'
            )
        """ if require_supporting_evidence else ""
        expert_clause = "AND m.expert_id = ?" if expert_id else ""
        params: list[Any] = [split, min_gold_confidence]
        if expert_id:
            params.append(expert_id)
        return self.store.conn.execute(
            f"""
            SELECT DISTINCT
                ec.case_id, m.expert_id, p.display_name,
                c.dimension, c.change_type
            FROM expert_claims c
            JOIN expert_cases ec ON ec.case_id = c.case_id
            JOIN expert_materials m ON m.material_id = c.material_id
            JOIN expert_profiles p ON p.expert_id = m.expert_id
            WHERE ec.split = ?
              AND c.confidence >= ?
              {evidence_clause}
              {expert_clause}
            ORDER BY ec.case_id, c.dimension, m.expert_id, c.change_type
            """,
            params,
        ).fetchall()

    def analyze(
        self,
        *,
        split: str = "blind",
        min_gold_confidence: float = 0.5,
        require_supporting_evidence: bool = True,
    ) -> dict[str, Any]:
        split = self._validate_split(split)
        min_gold_confidence = _bounded(
            min_gold_confidence,
            field_name="min_gold_confidence",
        )
        rows = self._gold_rows(
            split=split,
            min_gold_confidence=min_gold_confidence,
            require_supporting_evidence=require_supporting_evidence,
        )

        labels: dict[tuple[str, str, str], set[str]] = defaultdict(set)
        expert_names: dict[str, str] = {}
        for case_id, expert_id, display_name, dimension, change_type in rows:
            case_id = str(case_id)
            expert_id = str(expert_id)
            dimension = str(dimension)
            expert_names[expert_id] = str(display_name)
            labels[(expert_id, case_id, dimension)].add(str(change_type))

        by_case_dimension: dict[tuple[str, str], list[str]] = defaultdict(list)
        for expert_id, case_id, dimension in labels:
            by_case_dimension[(case_id, dimension)].append(expert_id)

        pair_details: list[dict[str, Any]] = []
        single_expert_dimensions: list[dict[str, Any]] = []
        pair_aggregate: dict[tuple[str, str], dict[str, Any]] = {}

        for (case_id, dimension), experts in sorted(by_case_dimension.items()):
            unique_experts = sorted(set(experts))
            if len(unique_experts) < 2:
                expert_id = unique_experts[0]
                single_expert_dimensions.append(
                    {
                        "case_id": case_id,
                        "dimension": dimension,
                        "expert_id": expert_id,
                        "change_types": sorted(labels[(expert_id, case_id, dimension)]),
                        "state": "not_comparable",
                    }
                )
                continue

            for left_id, right_id in combinations(unique_experts, 2):
                left = labels[(left_id, case_id, dimension)]
                right = labels[(right_id, case_id, dimension)]
                shared = left & right
                union = left | right
                jaccard = len(shared) / len(union) if union else 1.0
                if left == right:
                    state = "exact_agreement"
                elif shared:
                    state = "partial_overlap"
                else:
                    state = "explicit_disagreement"

                detail = {
                    "case_id": case_id,
                    "dimension": dimension,
                    "left_expert_id": left_id,
                    "right_expert_id": right_id,
                    "left_change_types": sorted(left),
                    "right_change_types": sorted(right),
                    "shared_change_types": sorted(shared),
                    "left_only_change_types": sorted(left - right),
                    "right_only_change_types": sorted(right - left),
                    "jaccard": round(jaccard, 4),
                    "state": state,
                }
                pair_details.append(detail)

                pair_key = (left_id, right_id)
                aggregate = pair_aggregate.setdefault(
                    pair_key,
                    {
                        "jaccard_values": [],
                        "exact_agreement_count": 0,
                        "partial_overlap_count": 0,
                        "explicit_disagreement_count": 0,
                    },
                )
                aggregate["jaccard_values"].append(jaccard)
                aggregate[f"{state}_count"] += 1

        per_pair: dict[str, Any] = {}
        for (left_id, right_id), values in sorted(pair_aggregate.items()):
            comparable = len(values["jaccard_values"])
            per_pair[f"{left_id}::{right_id}"] = {
                "left_expert_id": left_id,
                "left_display_name": expert_names.get(left_id),
                "right_expert_id": right_id,
                "right_display_name": expert_names.get(right_id),
                "comparable_dimension_count": comparable,
                "exact_agreement_count": values["exact_agreement_count"],
                "partial_overlap_count": values["partial_overlap_count"],
                "explicit_disagreement_count": values["explicit_disagreement_count"],
                "exact_agreement_rate": round(
                    values["exact_agreement_count"] / comparable if comparable else 0.0,
                    4,
                ),
                "mean_jaccard": _mean(values["jaccard_values"]),
            }

        all_jaccard = [item["jaccard"] for item in pair_details]
        exact_count = sum(item["state"] == "exact_agreement" for item in pair_details)
        partial_count = sum(item["state"] == "partial_overlap" for item in pair_details)
        disagreement_count = sum(
            item["state"] == "explicit_disagreement" for item in pair_details
        )
        comparable_count = len(pair_details)

        return {
            "version": AGREEMENT_TRANSFER_VERSION,
            "split": split,
            "gold_policy": {
                "min_gold_confidence": min_gold_confidence,
                "require_supporting_evidence": require_supporting_evidence,
            },
            "scoring_policy": {
                "silence_is_disagreement": False,
                "compare_only_shared_case_dimensions": True,
                "expert_profiles_kept_separate": True,
            },
            "expert_count": len(expert_names),
            "comparable_dimension_pair_count": comparable_count,
            "single_expert_dimension_count": len(single_expert_dimensions),
            "exact_agreement_count": exact_count,
            "partial_overlap_count": partial_count,
            "explicit_disagreement_count": disagreement_count,
            "exact_agreement_rate": round(
                exact_count / comparable_count if comparable_count else 0.0,
                4,
            ),
            "mean_jaccard": _mean(all_jaccard),
            "per_expert_pair": per_pair,
            "comparisons": pair_details,
            "not_comparable": single_expert_dimensions,
            "winner": None,
            "preference_score": None,
            "research_only": True,
        }


class HeldOutExpertTransferEvaluator:
    """Post-hoc transfer evaluation для полностью исключённого expert profile.

    Код может проверить декларацию exclusion и fingerprints, но не может доказать,
    каким внешним процессом был построен prediction run. Поэтому отчёт явно
    маркирует exclusion как декларативный контракт, а не криптографическое
    доказательство отсутствия эксперта в обучающих данных.
    """

    def __init__(self, store: ExpertCorpusStore) -> None:
        self.store = store
        self.agreement = ExpertAgreementAnalyzer(store)

    def _require_expert(self, expert_id: str) -> str:
        expert_id = _clean(
            expert_id,
            field_name="held_out_expert_id",
            limit=160,
            required=True,
        )
        row = self.store.conn.execute(
            "SELECT 1 FROM expert_profiles WHERE expert_id = ? LIMIT 1",
            [expert_id],
        ).fetchone()
        if row is None:
            raise ExpertCorpusValidationError(f"Неизвестный expert profile: {expert_id}")
        return expert_id

    def export_manifest(
        self,
        *,
        held_out_expert_id: str,
        split: str = "external_transfer",
        min_gold_confidence: float = 0.5,
        require_supporting_evidence: bool = True,
    ) -> dict[str, Any]:
        split = self.agreement._validate_split(split)
        held_out_expert_id = self._require_expert(held_out_expert_id)
        min_gold_confidence = _bounded(
            min_gold_confidence,
            field_name="min_gold_confidence",
        )
        gold_rows = self.agreement._gold_rows(
            split=split,
            min_gold_confidence=min_gold_confidence,
            require_supporting_evidence=require_supporting_evidence,
            expert_id=held_out_expert_id,
        )
        case_ids = sorted({str(row[0]) for row in gold_rows})
        cases: list[dict[str, Any]] = []
        for case_id in case_ids:
            row = self.store.conn.execute(
                """
                SELECT case_id, imdb_id, film_title, film_year, source_work_id
                FROM expert_cases WHERE case_id = ?
                """,
                [case_id],
            ).fetchone()
            if row is None:
                continue
            cases.append(
                {
                    "case_id": str(row[0]),
                    "imdb_id": row[1],
                    "film_title": str(row[2]),
                    "film_year": row[3],
                    "source_work_id": row[4],
                }
            )

        payload = {
            "version": AGREEMENT_TRANSFER_VERSION,
            "protocol": "held_out_expert_transfer",
            "split": split,
            "held_out_expert_id": held_out_expert_id,
            "case_count": len(cases),
            "cases": cases,
            "contains_held_out_claims": False,
            "contains_held_out_interpretation": False,
            "gold_policy": {
                "min_gold_confidence": min_gold_confidence,
                "require_supporting_evidence": require_supporting_evidence,
            },
        }
        payload["manifest_fingerprint_sha256"] = _fingerprint(payload)
        return payload

    @staticmethod
    def _parse_prediction_run(payload: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise ExpertCorpusValidationError("transfer prediction run должен быть JSON-объектом")
        run_id = _clean(
            payload.get("run_id"),
            field_name="run_id",
            limit=180,
            required=True,
        )
        manifest_fingerprint = _clean(
            payload.get("manifest_fingerprint_sha256"),
            field_name="manifest_fingerprint_sha256",
            limit=64,
            required=True,
        ).lower()
        if len(manifest_fingerprint) != 64 or any(
            char not in "0123456789abcdef" for char in manifest_fingerprint
        ):
            raise ExpertCorpusValidationError(
                "manifest_fingerprint_sha256 должен быть SHA-256 hex"
            )
        raw_excluded = payload.get("excluded_expert_ids") or []
        if not isinstance(raw_excluded, list):
            raise ExpertCorpusValidationError("excluded_expert_ids должен быть массивом")
        excluded_expert_ids = sorted(
            {
                _clean(
                    value,
                    field_name="excluded_expert_ids",
                    limit=160,
                    required=True,
                )
                for value in raw_excluded
            }
        )
        cases = tuple(
            BlindCasePrediction.from_dict(item)
            for item in (payload.get("cases") or [])
        )
        case_ids = [item.case_id for item in cases]
        if len(case_ids) != len(set(case_ids)):
            raise ExpertCorpusValidationError("case_id в transfer run должны быть уникальны")

        normalized = {
            "version": AGREEMENT_TRANSFER_VERSION,
            "run_id": run_id,
            "manifest_fingerprint_sha256": manifest_fingerprint,
            "excluded_expert_ids": excluded_expert_ids,
            "cases": [
                {
                    "case_id": case.case_id,
                    "findings": [
                        {
                            "dimension": finding.dimension,
                            "change_type": finding.change_type,
                            "reference_id": finding.reference_id,
                            "confidence": finding.confidence,
                        }
                        for finding in sorted(
                            case.findings,
                            key=lambda item: (
                                item.dimension,
                                item.change_type,
                                item.reference_id,
                            ),
                        )
                    ],
                }
                for case in sorted(cases, key=lambda item: item.case_id)
            ],
        }
        return {
            "run_id": run_id,
            "manifest_fingerprint_sha256": manifest_fingerprint,
            "excluded_expert_ids": excluded_expert_ids,
            "cases": cases,
            "prediction_fingerprint_sha256": _fingerprint(normalized),
        }

    def evaluate(
        self,
        prediction_payload: dict[str, Any],
        *,
        held_out_expert_id: str,
        split: str = "external_transfer",
        min_prediction_confidence: float = 0.5,
        min_gold_confidence: float = 0.5,
        require_supporting_evidence: bool = True,
    ) -> dict[str, Any]:
        held_out_expert_id = self._require_expert(held_out_expert_id)
        min_prediction_confidence = _bounded(
            min_prediction_confidence,
            field_name="min_prediction_confidence",
        )
        manifest = self.export_manifest(
            held_out_expert_id=held_out_expert_id,
            split=split,
            min_gold_confidence=min_gold_confidence,
            require_supporting_evidence=require_supporting_evidence,
        )
        run = self._parse_prediction_run(prediction_payload)
        if run["manifest_fingerprint_sha256"] != manifest["manifest_fingerprint_sha256"]:
            raise ExpertCorpusValidationError(
                "Transfer run создан не для текущего manifest: fingerprint mismatch"
            )
        if held_out_expert_id not in run["excluded_expert_ids"]:
            raise ExpertCorpusValidationError(
                "held-out expert должен быть явно указан в excluded_expert_ids"
            )

        expected_case_ids = {item["case_id"] for item in manifest["cases"]}
        predicted_case_ids = {case.case_id for case in run["cases"]}
        unexpected = sorted(predicted_case_ids - expected_case_ids)
        missing = sorted(expected_case_ids - predicted_case_ids)
        if unexpected:
            raise ExpertCorpusValidationError(
                "Transfer run содержит case вне manifest: " + ", ".join(unexpected)
            )
        if missing:
            raise ExpertCorpusValidationError(
                "Transfer run должен содержать каждый case manifest, включая findings=[]: "
                + ", ".join(missing)
            )

        gold_rows = self.agreement._gold_rows(
            split=manifest["split"],
            min_gold_confidence=min_gold_confidence,
            require_supporting_evidence=require_supporting_evidence,
            expert_id=held_out_expert_id,
        )
        gold: set[tuple[str, str, str]] = set()
        scorable_dimensions: dict[str, set[str]] = defaultdict(set)
        for case_id, _expert_id, _display_name, dimension, change_type in gold_rows:
            case_id = str(case_id)
            dimension = str(dimension)
            gold.add((case_id, dimension, str(change_type)))
            scorable_dimensions[case_id].add(dimension)

        predicted: set[tuple[str, str, str]] = set()
        unscored = 0
        for case in run["cases"]:
            allowed_dimensions = scorable_dimensions.get(case.case_id, set())
            for finding in case.findings:
                if finding.confidence < min_prediction_confidence:
                    continue
                if finding.dimension not in allowed_dimensions:
                    unscored += 1
                    continue
                predicted.add((case.case_id, finding.dimension, finding.change_type))

        tp = gold & predicted
        fp = predicted - gold
        fn = gold - predicted
        dimensions = sorted({item[1] for item in gold | predicted})
        by_dimension: dict[str, Any] = {}
        for dimension in dimensions:
            gold_dimension = {item for item in gold if item[1] == dimension}
            predicted_dimension = {item for item in predicted if item[1] == dimension}
            by_dimension[dimension] = _metric(
                len(gold_dimension & predicted_dimension),
                len(predicted_dimension - gold_dimension),
                len(gold_dimension - predicted_dimension),
            )

        case_count = len(expected_case_ids)
        return {
            "version": AGREEMENT_TRANSFER_VERSION,
            "evaluated_at": datetime.now(timezone.utc).isoformat(),
            "protocol": "held_out_expert_transfer",
            "split": manifest["split"],
            "held_out_expert_id": held_out_expert_id,
            "run_id": run["run_id"],
            "manifest_fingerprint_sha256": manifest["manifest_fingerprint_sha256"],
            "prediction_fingerprint_sha256": run["prediction_fingerprint_sha256"],
            "case_count": case_count,
            "gold_class_count": len(gold),
            "predicted_scored_class_count": len(predicted),
            "unscored_prediction_count": unscored,
            "metrics": _metric(len(tp), len(fp), len(fn)),
            "by_dimension": by_dimension,
            "policy": {
                "held_out_expert_exclusion_declared": True,
                "exclusion_is_declarative_not_audit_proof": True,
                "silence_is_negative": False,
                "score_only_held_out_annotated_dimensions": True,
                "min_prediction_confidence": min_prediction_confidence,
                "min_gold_confidence": min_gold_confidence,
                "require_supporting_evidence": require_supporting_evidence,
            },
            "coverage_warning": case_count < 5,
            "expert_interpretation_exposed_to_predictor": False,
            "preference_score": None,
            "research_only": True,
        }
