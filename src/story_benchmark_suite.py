from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Iterable

from src.adaptation_analysis import AdaptationValidationError
from src.story_benchmark import BENCHMARK_SPLITS


def _clean(value: Any, *, field_name: str, limit: int = 300) -> str:
    text = " ".join(str(value or "").strip().split())
    if not text:
        raise AdaptationValidationError(f"{field_name} не должен быть пустым")
    if len(text) > limit:
        raise AdaptationValidationError(
            f"{field_name} длиннее допустимых {limit} символов"
        )
    return text


def _prf(tp: int, fp: int, fn: int) -> dict[str, float | int]:
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = (
        2.0 * precision * recall / (precision + recall)
        if precision + recall
        else 0.0
    )
    return {
        "tp": int(tp),
        "fp": int(fp),
        "fn": int(fn),
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
    }


def _mean(values: Iterable[float]) -> float:
    rows = list(values)
    return round(sum(rows) / len(rows), 4) if rows else 0.0


def _canonical_json(payload: Any) -> str:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


@dataclass(frozen=True)
class BenchmarkSuiteManifestCase:
    case_id: str
    split: str
    source: str
    adaptation: str
    gold: str
    predicted: str

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "BenchmarkSuiteManifestCase":
        if not isinstance(payload, dict):
            raise AdaptationValidationError("benchmark suite case должен быть JSON-объектом")
        split = str(payload.get("split") or "").strip().casefold()
        if split not in BENCHMARK_SPLITS:
            raise AdaptationValidationError(
                "suite split должен быть train, development или blind"
            )
        return cls(
            case_id=_clean(payload.get("case_id"), field_name="case_id", limit=180),
            split=split,
            source=_clean(payload.get("source"), field_name="source", limit=500),
            adaptation=_clean(
                payload.get("adaptation"), field_name="adaptation", limit=500
            ),
            gold=_clean(payload.get("gold"), field_name="gold", limit=500),
            predicted=_clean(
                payload.get("predicted"), field_name="predicted", limit=500
            ),
        )


@dataclass(frozen=True)
class BenchmarkSuiteManifest:
    suite_id: str
    version: int
    cases: tuple[BenchmarkSuiteManifestCase, ...]

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "BenchmarkSuiteManifest":
        if not isinstance(payload, dict):
            raise AdaptationValidationError("benchmark suite manifest должен быть JSON-объектом")
        try:
            version = int(payload.get("version", 1))
        except (TypeError, ValueError) as exc:
            raise AdaptationValidationError("suite version должен быть целым числом") from exc
        if version != 1:
            raise AdaptationValidationError("Поддерживается только benchmark suite version=1")
        raw_cases = payload.get("cases") or []
        if not isinstance(raw_cases, list) or not raw_cases:
            raise AdaptationValidationError("suite cases должен быть непустым массивом")
        cases = tuple(BenchmarkSuiteManifestCase.from_dict(row) for row in raw_cases)
        ids = [row.case_id for row in cases]
        if len(ids) != len(set(ids)):
            raise AdaptationValidationError("case_id в suite manifest должны быть уникальны")
        return cls(
            suite_id=_clean(payload.get("suite_id"), field_name="suite_id", limit=180),
            version=version,
            cases=cases,
        )

    def fingerprint(self) -> str:
        payload = {
            "suite_id": self.suite_id,
            "version": self.version,
            "cases": [
                {
                    "case_id": row.case_id,
                    "split": row.split,
                    "source": row.source,
                    "adaptation": row.adaptation,
                    "gold": row.gold,
                    "predicted": row.predicted,
                }
                for row in self.cases
            ],
        }
        return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


class StoryBenchmarkSuiteAggregator:
    """Агрегирует уже посчитанные case reports без доступа к сюжетным текстам."""

    @staticmethod
    def _validate_case_result(payload: dict[str, Any]) -> None:
        if not isinstance(payload, dict):
            raise AdaptationValidationError("benchmark case result должен быть JSON-объектом")
        _clean(payload.get("case_id"), field_name="result.case_id", limit=180)
        split = str(payload.get("split") or "").strip().casefold()
        if split not in BENCHMARK_SPLITS:
            raise AdaptationValidationError("result.split неизвестен")
        overall = payload.get("overall")
        if not isinstance(overall, dict):
            raise AdaptationValidationError("result.overall должен быть объектом")
        for name in ("tp", "fp", "fn"):
            try:
                value = int(overall[name])
            except (KeyError, TypeError, ValueError) as exc:
                raise AdaptationValidationError(
                    f"result.overall.{name} должен быть целым числом"
                ) from exc
            if value < 0:
                raise AdaptationValidationError(
                    f"result.overall.{name} должен быть >= 0"
                )

    @classmethod
    def aggregate(
        cls,
        case_results: Iterable[dict[str, Any]],
        *,
        suite_id: str,
        suite_fingerprint: str | None = None,
        only_split: str | None = None,
    ) -> dict[str, Any]:
        rows = list(case_results)
        if not rows:
            raise AdaptationValidationError("Для benchmark suite нужен хотя бы один case result")
        for row in rows:
            cls._validate_case_result(row)

        case_ids = [str(row["case_id"]) for row in rows]
        if len(case_ids) != len(set(case_ids)):
            raise AdaptationValidationError("case result IDs должны быть уникальны")

        if only_split is not None:
            only_split = str(only_split).strip().casefold()
            if only_split not in BENCHMARK_SPLITS:
                raise AdaptationValidationError("only_split неизвестен")
            rows = [row for row in rows if row["split"] == only_split]
            if not rows:
                raise AdaptationValidationError(
                    f"В suite нет результатов split={only_split}"
                )

        split_reports: dict[str, dict[str, Any]] = {}
        for split in sorted({str(row["split"]) for row in rows}):
            selected = [row for row in rows if row["split"] == split]
            split_reports[split] = cls._aggregate_group(selected)

        total = cls._aggregate_group(rows)
        return {
            "suite_id": _clean(suite_id, field_name="suite_id", limit=180),
            "suite_fingerprint_sha256": suite_fingerprint,
            "only_split": only_split,
            "case_count": len(rows),
            "case_ids": sorted(str(row["case_id"]) for row in rows),
            "overall": total,
            "by_split": split_reports,
            "research_only": True,
        }

    @staticmethod
    def _aggregate_group(rows: list[dict[str, Any]]) -> dict[str, Any]:
        tp = sum(int(row["overall"]["tp"]) for row in rows)
        fp = sum(int(row["overall"]["fp"]) for row in rows)
        fn = sum(int(row["overall"]["fn"]) for row in rows)
        micro = _prf(tp, fp, fn)

        macro = {
            "precision": _mean(float(row["overall"]["precision"]) for row in rows),
            "recall": _mean(float(row["overall"]["recall"]) for row in rows),
            "f1": _mean(float(row["overall"]["f1"]) for row in rows),
        }

        all_kinds = sorted(
            {
                kind
                for row in rows
                for kind in (row.get("by_kind") or {}).keys()
            }
        )
        by_kind: dict[str, dict[str, Any]] = {}
        for kind in all_kinds:
            tp_kind = 0
            fp_kind = 0
            fn_kind = 0
            per_case: list[dict[str, Any]] = []
            for row in rows:
                metrics = (row.get("by_kind") or {}).get(kind)
                if not isinstance(metrics, dict):
                    continue
                tp_kind += int(metrics.get("tp", 0))
                fp_kind += int(metrics.get("fp", 0))
                fn_kind += int(metrics.get("fn", 0))
                per_case.append(metrics)
            by_kind[kind] = {
                "micro": _prf(tp_kind, fp_kind, fn_kind),
                "macro": {
                    "precision": _mean(float(item.get("precision", 0.0)) for item in per_case),
                    "recall": _mean(float(item.get("recall", 0.0)) for item in per_case),
                    "f1": _mean(float(item.get("f1", 0.0)) for item in per_case),
                },
                "case_count": len(per_case),
            }

        return {
            "case_count": len(rows),
            "micro": micro,
            "macro": macro,
            "by_kind": by_kind,
        }
