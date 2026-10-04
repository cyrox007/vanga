from __future__ import annotations

import hashlib
import json
from typing import Any

from src.rating_history import RatingHistoryError, RatingHistoryStore, _imdb_id, _parse_datetime
from src.rating_milestones import DEFAULT_MILESTONES, RatingMilestoneExtractor


RATING_DATASET_VERSION = 1


def _canonical_json(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _fingerprint(payload: Any) -> str:
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


class RatingMilestoneDatasetBuilder:
    """Аудит покрытия P7 milestones на наборе фильмов.

    Слой ничего не обучает. Он формирует воспроизводимый report на явный ``as_of``
    и экспортирует только milestone rows с ``ready=true``.
    """

    def __init__(self, store: RatingHistoryStore) -> None:
        self.store = store
        self.extractor = RatingMilestoneExtractor(store)

    @staticmethod
    def _normalize_cases(cases: Any) -> list[dict[str, str]]:
        if not isinstance(cases, list) or not cases:
            raise RatingHistoryError("cases должен быть непустым массивом")
        result: list[dict[str, str]] = []
        seen_ids: set[str] = set()
        seen_cases: set[str] = set()
        for raw in cases:
            if not isinstance(raw, dict):
                raise RatingHistoryError("cases[] должен содержать JSON-объекты")
            imdb_id = _imdb_id(raw.get("imdb_id"))
            release = _parse_datetime(raw.get("release_at"), field_name=f"{imdb_id}.release_at")
            case_id = " ".join(str(raw.get("case_id") or imdb_id).strip().split())
            if not case_id:
                raise RatingHistoryError("case_id не должен быть пустым")
            if case_id in seen_cases:
                raise RatingHistoryError(f"Дублирующийся case_id: {case_id}")
            if imdb_id in seen_ids:
                raise RatingHistoryError(
                    f"Дублирующийся imdb_id в dataset: {imdb_id}; release contract должен быть однозначным"
                )
            seen_cases.add(case_id)
            seen_ids.add(imdb_id)
            result.append(
                {
                    "case_id": case_id,
                    "imdb_id": imdb_id,
                    "release_at": release.isoformat(),
                }
            )
        result.sort(key=lambda item: (item["imdb_id"], item["case_id"]))
        return result

    @staticmethod
    def _validate_thresholds(
        min_ready_rows: int | None,
        min_due_coverage: float | None,
    ) -> tuple[int | None, float | None]:
        if min_ready_rows is not None:
            try:
                min_ready_rows = int(min_ready_rows)
            except (TypeError, ValueError) as exc:
                raise RatingHistoryError("min_ready_rows должен быть целым") from exc
            if min_ready_rows < 1:
                raise RatingHistoryError("min_ready_rows должен быть >= 1")
        if min_due_coverage is not None:
            try:
                min_due_coverage = float(min_due_coverage)
            except (TypeError, ValueError) as exc:
                raise RatingHistoryError("min_due_coverage должен быть числом") from exc
            if not 0.0 <= min_due_coverage <= 1.0:
                raise RatingHistoryError("min_due_coverage должен быть в диапазоне 0..1")
        return min_ready_rows, min_due_coverage

    def build(
        self,
        cases: list[dict[str, Any]],
        *,
        as_of: str,
        policy: dict[str, Any] | None = None,
        min_ready_rows: int | None = None,
        min_due_coverage: float | None = None,
    ) -> dict[str, Any]:
        normalized_cases = self._normalize_cases(cases)
        cutoff = _parse_datetime(as_of, field_name="as_of")
        normalized_policy = RatingMilestoneExtractor._normalize_policy(
            policy or DEFAULT_MILESTONES
        )
        min_ready_rows, min_due_coverage = self._validate_thresholds(
            min_ready_rows,
            min_due_coverage,
        )
        thresholds_enabled = (
            min_ready_rows is not None or min_due_coverage is not None
        )

        case_results: list[dict[str, Any]] = []
        counters = {
            name: {
                "case_count": len(normalized_cases),
                "due_count": 0,
                "ready_count": 0,
                "not_due_count": 0,
                "no_observation_count": 0,
                "too_late_count": 0,
            }
            for name in normalized_policy
        }

        for case in normalized_cases:
            extracted = self.extractor.extract(
                case["imdb_id"],
                case["release_at"],
                as_of=cutoff,
                policy=normalized_policy,
            )
            case_milestones: dict[str, Any] = {}
            for name in normalized_policy:
                item = dict(extracted["milestones"][name])
                case_milestones[name] = item
                bucket = counters[name]
                if item["due"]:
                    bucket["due_count"] += 1
                else:
                    bucket["not_due_count"] += 1
                if item["ready"]:
                    bucket["ready_count"] += 1
                elif item["missing_reason"] == "no_observation_after_target":
                    bucket["no_observation_count"] += 1
                elif item["missing_reason"] == "observation_too_late":
                    bucket["too_late_count"] += 1

            case_results.append(
                {
                    **case,
                    "milestones": case_milestones,
                    "rating_deltas": extracted["rating_deltas"],
                }
            )

        milestone_summary: dict[str, Any] = {}
        for name, bucket in counters.items():
            due = int(bucket["due_count"])
            ready = int(bucket["ready_count"])
            due_coverage = (ready / due) if due else None
            total_coverage = ready / len(normalized_cases)
            readiness: bool | None = None
            if thresholds_enabled:
                enough_rows = (
                    ready >= min_ready_rows if min_ready_rows is not None else True
                )
                enough_coverage = (
                    due_coverage is not None and due_coverage >= min_due_coverage
                    if min_due_coverage is not None
                    else due > 0
                )
                readiness = bool(due > 0 and enough_rows and enough_coverage)
            milestone_summary[name] = {
                **bucket,
                "due_ready_ratio": round(due_coverage, 6) if due_coverage is not None else None,
                "total_ready_ratio": round(total_coverage, 6),
                "readiness_evaluated": thresholds_enabled,
                "ready_for_research_target": readiness,
            }

        input_contract = {
            "version": RATING_DATASET_VERSION,
            "as_of": cutoff.isoformat(),
            "cases": normalized_cases,
            "policy": normalized_policy,
        }
        dataset_fingerprint = _fingerprint(input_contract)
        report_body = {
            "version": RATING_DATASET_VERSION,
            "as_of": cutoff.isoformat(),
            "case_count": len(normalized_cases),
            "policy": normalized_policy,
            "thresholds": {
                "min_ready_rows": min_ready_rows,
                "min_due_coverage": min_due_coverage,
                "evaluated": thresholds_enabled,
            },
            "dataset_fingerprint_sha256": dataset_fingerprint,
            "milestone_summary": milestone_summary,
            "case_results": case_results,
            "research_only": True,
            "model_training_started": False,
        }
        report_body["materialization_fingerprint_sha256"] = _fingerprint(report_body)
        return report_body

    @staticmethod
    def export_target_rows(report: dict[str, Any], milestone: str) -> dict[str, Any]:
        if not isinstance(report, dict):
            raise RatingHistoryError("report должен быть JSON-объектом")
        summaries = report.get("milestone_summary") or {}
        if milestone not in summaries:
            raise RatingHistoryError(f"Неизвестный milestone в report: {milestone}")
        rows: list[dict[str, Any]] = []
        for case in report.get("case_results") or []:
            item = (case.get("milestones") or {}).get(milestone) or {}
            if not item.get("ready") or not item.get("usable_as_target"):
                continue
            rows.append(
                {
                    "case_id": case["case_id"],
                    "imdb_id": case["imdb_id"],
                    "release_at": case["release_at"],
                    "milestone": milestone,
                    "target_at": item["target_at"],
                    "snapshot_id": item["snapshot_id"],
                    "observed_at": item["observed_at"],
                    "lag_days": item["lag_days"],
                    "average_rating": item["average_rating"],
                    "num_votes": item["num_votes"],
                    "source_fingerprint_sha256": item.get(
                        "source_fingerprint_sha256"
                    ),
                }
            )
        rows.sort(key=lambda item: (item["imdb_id"], item["case_id"]))
        body = {
            "version": RATING_DATASET_VERSION,
            "milestone": milestone,
            "as_of": report.get("as_of"),
            "source_dataset_fingerprint_sha256": report.get(
                "dataset_fingerprint_sha256"
            ),
            "source_materialization_fingerprint_sha256": report.get(
                "materialization_fingerprint_sha256"
            ),
            "row_count": len(rows),
            "rows": rows,
            "research_only": True,
            "model_training_started": False,
        }
        body["target_dataset_fingerprint_sha256"] = _fingerprint(body)
        return body
