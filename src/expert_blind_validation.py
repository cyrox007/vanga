from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from src.expert_corpus import (
    EXPERT_CHANGE_TYPES,
    EXPERT_DIMENSIONS,
    ExpertCorpusStore,
    ExpertCorpusValidationError,
)


BLIND_VALIDATION_VERSION = 1
EVALUATION_SPLITS = {"development", "blind", "external_transfer"}


def _canonical_json(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _fingerprint(payload: Any) -> str:
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def _clean(value: Any, *, field_name: str, limit: int = 300, required: bool = False) -> str:
    text = " ".join(str(value or "").strip().split())
    if required and not text:
        raise ExpertCorpusValidationError(f"{field_name} не должен быть пустым")
    if len(text) > limit:
        raise ExpertCorpusValidationError(
            f"{field_name} длиннее допустимых {limit} символов"
        )
    return text


def _bounded(value: Any, *, field_name: str) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ExpertCorpusValidationError(f"{field_name} должен быть числом") from exc
    if not 0.0 <= parsed <= 1.0:
        raise ExpertCorpusValidationError(f"{field_name} должен быть в диапазоне 0..1")
    return parsed


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


def _reject_unknown_keys(payload: dict[str, Any], allowed: set[str], *, entity: str) -> None:
    unknown = sorted(set(payload) - allowed)
    if unknown:
        raise ExpertCorpusValidationError(
            f"{entity} содержит неизвестные/запрещённые поля: " + ", ".join(unknown)
        )


@dataclass(frozen=True)
class BlindFinding:
    dimension: str
    change_type: str
    reference_id: str
    confidence: float

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "BlindFinding":
        if not isinstance(payload, dict):
            raise ExpertCorpusValidationError("finding должен быть JSON-объектом")
        _reject_unknown_keys(
            payload,
            {"dimension", "change_type", "reference_id", "confidence"},
            entity="finding",
        )
        dimension = str(payload.get("dimension") or "").strip()
        change_type = str(payload.get("change_type") or "").strip()
        if dimension not in EXPERT_DIMENSIONS:
            raise ExpertCorpusValidationError(f"Неизвестный finding.dimension: {dimension!r}")
        if change_type not in EXPERT_CHANGE_TYPES:
            raise ExpertCorpusValidationError(
                f"Неизвестный finding.change_type: {change_type!r}"
            )
        return cls(
            dimension=dimension,
            change_type=change_type,
            reference_id=_clean(
                payload.get("reference_id"),
                field_name="finding.reference_id",
                limit=300,
                required=True,
            ),
            confidence=_bounded(
                payload.get("confidence", 0.5),
                field_name="finding.confidence",
            ),
        )


@dataclass(frozen=True)
class BlindCasePrediction:
    case_id: str
    findings: tuple[BlindFinding, ...]

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "BlindCasePrediction":
        if not isinstance(payload, dict):
            raise ExpertCorpusValidationError("case prediction должен быть JSON-объектом")
        _reject_unknown_keys(payload, {"case_id", "findings"}, entity="case prediction")
        raw_findings = payload.get("findings") or []
        if not isinstance(raw_findings, list):
            raise ExpertCorpusValidationError("findings должен быть массивом")
        findings = tuple(BlindFinding.from_dict(item) for item in raw_findings)
        class_keys = [(item.dimension, item.change_type) for item in findings]
        if len(class_keys) != len(set(class_keys)):
            raise ExpertCorpusValidationError(
                "В одном case нельзя дублировать одинаковый dimension/change_type"
            )
        return cls(
            case_id=_clean(
                payload.get("case_id"), field_name="case_id", limit=180, required=True
            ),
            findings=findings,
        )


class ExpertBlindValidator:
    """Post-hoc evaluator, не раскрывающий predictor-у expert gold.

    Отсутствие claim у эксперта не трактуется как отрицательная метка. Для
    expert×case оцениваются только dimensions, которые этот эксперт реально
    размечал. Метрики разных экспертов никогда не объединяются в один score.
    """

    def __init__(self, store: ExpertCorpusStore) -> None:
        self.store = store

    @staticmethod
    def _validate_split(split: str) -> str:
        split = str(split or "").strip().casefold()
        if split not in EVALUATION_SPLITS:
            raise ExpertCorpusValidationError(
                "blind validation split должен быть development, blind или external_transfer; train запрещён"
            )
        return split

    def _public_cases(self, split: str) -> list[dict[str, Any]]:
        rows = self.store.conn.execute(
            """
            SELECT case_id, imdb_id, film_title, film_year, source_work_id
            FROM expert_cases
            WHERE split = ?
            ORDER BY case_id
            """,
            [split],
        ).fetchall()
        return [
            {
                "case_id": str(row[0]),
                "imdb_id": row[1],
                "film_title": str(row[2]),
                "film_year": int(row[3]) if row[3] is not None else None,
                "source_work_id": row[4],
            }
            for row in rows
        ]

    def _sealed_gold_rows(self, split: str) -> list[tuple[str, str, str, str, str, float]]:
        rows = self.store.conn.execute(
            """
            SELECT DISTINCT
                m.expert_id, c.case_id, c.claim_id,
                c.dimension, c.change_type, c.confidence
            FROM expert_claims c
            JOIN expert_materials m ON m.material_id = c.material_id
            JOIN expert_cases ec ON ec.case_id = c.case_id
            WHERE ec.split = ?
            ORDER BY m.expert_id, c.case_id, c.claim_id, c.dimension, c.change_type
            """,
            [split],
        ).fetchall()
        return [
            (
                str(row[0]), str(row[1]), str(row[2]), str(row[3]), str(row[4]),
                round(float(row[5]), 8),
            )
            for row in rows
        ]

    def _sealed_gold_fingerprint(self, split: str) -> str:
        return _fingerprint(
            {
                "version": BLIND_VALIDATION_VERSION,
                "split": split,
                "gold_rows": self._sealed_gold_rows(split),
            }
        )

    def export_manifest(self, *, split: str = "blind") -> dict[str, Any]:
        split = self._validate_split(split)
        cases = self._public_cases(split)
        if not cases:
            raise ExpertCorpusValidationError(f"В corpus нет case-ов split={split}")
        payload = {
            "version": BLIND_VALIDATION_VERSION,
            "split": split,
            "case_count": len(cases),
            "cases": cases,
            # Hash привязывает manifest к закрытому gold, но не раскрывает labels.
            "sealed_gold_fingerprint_sha256": self._sealed_gold_fingerprint(split),
            "contains_expert_claims": False,
            "contains_expert_interpretation": False,
        }
        payload["manifest_fingerprint_sha256"] = _fingerprint(payload)
        return payload

    @staticmethod
    def parse_prediction_run(payload: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise ExpertCorpusValidationError("prediction run должен быть JSON-объектом")
        _reject_unknown_keys(
            payload,
            {"version", "run_id", "manifest_fingerprint_sha256", "cases"},
            entity="prediction run",
        )
        try:
            version = int(payload.get("version", BLIND_VALIDATION_VERSION))
        except (TypeError, ValueError) as exc:
            raise ExpertCorpusValidationError("prediction run version должен быть целым") from exc
        if version != BLIND_VALIDATION_VERSION:
            raise ExpertCorpusValidationError(
                f"Поддерживается prediction run version={BLIND_VALIDATION_VERSION}"
            )
        run_id = _clean(payload.get("run_id"), field_name="run_id", limit=180, required=True)
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
        raw_cases = payload.get("cases") or []
        if not isinstance(raw_cases, list):
            raise ExpertCorpusValidationError("prediction run.cases должен быть массивом")
        cases = tuple(BlindCasePrediction.from_dict(item) for item in raw_cases)
        case_ids = [item.case_id for item in cases]
        if len(case_ids) != len(set(case_ids)):
            raise ExpertCorpusValidationError("case_id в prediction run должны быть уникальны")

        normalized = {
            "version": version,
            "run_id": run_id,
            "manifest_fingerprint_sha256": manifest_fingerprint,
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
                                item.dimension, item.change_type,
                                item.reference_id, item.confidence,
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
            "cases": cases,
            "prediction_fingerprint_sha256": _fingerprint(normalized),
        }

    def _gold_rows(
        self,
        *,
        split: str,
        min_gold_confidence: float,
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
                ec.case_id, m.expert_id, p.display_name,
                c.dimension, c.change_type
            FROM expert_claims c
            JOIN expert_cases ec ON ec.case_id = c.case_id
            JOIN expert_materials m ON m.material_id = c.material_id
            JOIN expert_profiles p ON p.expert_id = m.expert_id
            WHERE ec.split = ?
              AND c.confidence >= ?
              {evidence_clause}
            ORDER BY m.expert_id, ec.case_id, c.dimension, c.change_type
            """,
            [split, min_gold_confidence],
        ).fetchall()

    def evaluate(
        self,
        prediction_payload: dict[str, Any],
        *,
        split: str = "blind",
        min_prediction_confidence: float = 0.5,
        min_gold_confidence: float = 0.5,
        require_supporting_evidence: bool = True,
    ) -> dict[str, Any]:
        split = self._validate_split(split)
        min_prediction_confidence = _bounded(
            min_prediction_confidence, field_name="min_prediction_confidence"
        )
        min_gold_confidence = _bounded(
            min_gold_confidence, field_name="min_gold_confidence"
        )
        manifest = self.export_manifest(split=split)
        run = self.parse_prediction_run(prediction_payload)
        if run["manifest_fingerprint_sha256"] != manifest["manifest_fingerprint_sha256"]:
            raise ExpertCorpusValidationError(
                "Prediction run создан не для текущего sealed manifest: fingerprint mismatch"
            )

        expected_case_ids = {item["case_id"] for item in manifest["cases"]}
        predicted_case_ids = {case.case_id for case in run["cases"]}
        unexpected = sorted(predicted_case_ids - expected_case_ids)
        missing = sorted(expected_case_ids - predicted_case_ids)
        if unexpected:
            raise ExpertCorpusValidationError(
                "Prediction run содержит case вне выбранного split: " + ", ".join(unexpected)
            )
        if missing:
            raise ExpertCorpusValidationError(
                "Prediction run должен содержать каждый case manifest, включая findings=[]: "
                + ", ".join(missing)
            )

        predicted_by_case: dict[str, set[tuple[str, str]]] = {}
        for case in run["cases"]:
            predicted_by_case[case.case_id] = {
                (finding.dimension, finding.change_type)
                for finding in case.findings
                if finding.confidence >= min_prediction_confidence
            }

        gold_rows = self._gold_rows(
            split=split,
            min_gold_confidence=min_gold_confidence,
            require_supporting_evidence=require_supporting_evidence,
        )
        gold_by_expert: dict[str, set[tuple[str, str, str]]] = {}
        expert_names: dict[str, str] = {}
        dimensions_by_expert_case: dict[tuple[str, str], set[str]] = {}
        for case_id, expert_id, display_name, dimension, change_type in gold_rows:
            expert_id = str(expert_id)
            case_id = str(case_id)
            dimension = str(dimension)
            expert_names[expert_id] = str(display_name)
            gold_by_expert.setdefault(expert_id, set()).add(
                (case_id, dimension, str(change_type))
            )
            dimensions_by_expert_case.setdefault((expert_id, case_id), set()).add(dimension)

        per_expert: dict[str, Any] = {}
        for expert_id in sorted(gold_by_expert):
            gold = gold_by_expert[expert_id]
            case_scope = {case_id for case_id, _dimension, _change_type in gold}
            predicted: set[tuple[str, str, str]] = set()
            unscored = 0
            dimension_counts: dict[str, dict[str, int]] = {}

            for case_id in case_scope:
                scorable_dimensions = dimensions_by_expert_case.get((expert_id, case_id), set())
                for dimension, change_type in predicted_by_case.get(case_id, set()):
                    if dimension in scorable_dimensions:
                        predicted.add((case_id, dimension, change_type))
                    else:
                        unscored += 1

            tp_set = gold & predicted
            fp_set = predicted - gold
            fn_set = gold - predicted
            for dimension in sorted({item[1] for item in gold | predicted}):
                gold_dim = {item for item in gold if item[1] == dimension}
                pred_dim = {item for item in predicted if item[1] == dimension}
                dimension_counts[dimension] = {
                    "tp": len(gold_dim & pred_dim),
                    "fp": len(pred_dim - gold_dim),
                    "fn": len(gold_dim - pred_dim),
                }

            per_expert[expert_id] = {
                "display_name": expert_names[expert_id],
                "case_count": len(case_scope),
                "gold_class_count": len(gold),
                "predicted_scored_class_count": len(predicted),
                "unscored_prediction_count": unscored,
                "metrics": _metric(len(tp_set), len(fp_set), len(fn_set)),
                "by_dimension": {
                    dimension: _metric(values["tp"], values["fp"], values["fn"])
                    for dimension, values in dimension_counts.items()
                },
            }

        return {
            "version": BLIND_VALIDATION_VERSION,
            "evaluated_at": datetime.now(timezone.utc).isoformat(),
            "split": split,
            "run_id": run["run_id"],
            "manifest_fingerprint_sha256": manifest["manifest_fingerprint_sha256"],
            "sealed_gold_fingerprint_sha256": manifest["sealed_gold_fingerprint_sha256"],
            "prediction_fingerprint_sha256": run["prediction_fingerprint_sha256"],
            "case_count": len(expected_case_ids),
            "gold_policy": {
                "min_gold_confidence": min_gold_confidence,
                "require_supporting_evidence": require_supporting_evidence,
            },
            "prediction_policy": {
                "min_prediction_confidence": min_prediction_confidence,
            },
            "scoring_policy": {
                "silence_is_negative": False,
                "score_only_dimensions_annotated_by_expert_for_case": True,
                "expert_profiles_are_never_merged": True,
            },
            "per_expert": per_expert,
            "combined_expert_score": None,
            "cross_expert_dimension_score": None,
            "expert_interpretation_exposed_to_predictor": False,
            "gold_labels_exposed_to_predictor": False,
            "preference_score": None,
            "research_only": True,
        }
