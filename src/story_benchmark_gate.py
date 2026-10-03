from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from src.adaptation_analysis import AdaptationValidationError

_ACCEPTANCE_SPLITS = {"development", "blind"}


def _bounded(value: Any, *, field_name: str) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise AdaptationValidationError(f"{field_name} должен быть числом") from exc
    if not 0.0 <= parsed <= 1.0:
        raise AdaptationValidationError(f"{field_name} должен быть в диапазоне 0..1")
    return parsed


def _positive_int(value: Any, *, field_name: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise AdaptationValidationError(f"{field_name} должен быть целым числом") from exc
    if parsed < 1:
        raise AdaptationValidationError(f"{field_name} должен быть >= 1")
    return parsed


def _clean(value: Any, *, field_name: str, limit: int = 180) -> str:
    text = " ".join(str(value or "").strip().split())
    if not text:
        raise AdaptationValidationError(f"{field_name} не должен быть пустым")
    if len(text) > limit:
        raise AdaptationValidationError(f"{field_name} длиннее допустимых {limit} символов")
    return text


def _canonical_json(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


@dataclass(frozen=True)
class KindRequirement:
    min_case_count: int
    min_micro_f1: float

    @classmethod
    def from_dict(cls, payload: dict[str, Any], *, field_name: str) -> "KindRequirement":
        if not isinstance(payload, dict):
            raise AdaptationValidationError(f"{field_name} должен быть объектом")
        return cls(
            min_case_count=_positive_int(
                payload.get("min_case_count", 1),
                field_name=f"{field_name}.min_case_count",
            ),
            min_micro_f1=_bounded(
                payload.get("min_micro_f1", 0.0),
                field_name=f"{field_name}.min_micro_f1",
            ),
        )


@dataclass(frozen=True)
class StoryBenchmarkQualityPolicy:
    policy_id: str
    version: int
    split: str
    min_case_count: int
    min_micro_precision: float
    min_micro_recall: float
    min_micro_f1: float
    min_macro_f1: float
    kind_requirements: dict[str, KindRequirement]
    expected_suite_fingerprint_sha256: str | None = None

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "StoryBenchmarkQualityPolicy":
        if not isinstance(payload, dict):
            raise AdaptationValidationError("quality policy должен быть JSON-объектом")
        try:
            version = int(payload.get("version", 1))
        except (TypeError, ValueError) as exc:
            raise AdaptationValidationError("quality policy version должен быть целым числом") from exc
        if version != 1:
            raise AdaptationValidationError("Поддерживается только quality policy version=1")

        split = str(payload.get("split") or "").strip().casefold()
        if split not in _ACCEPTANCE_SPLITS:
            raise AdaptationValidationError(
                "quality policy split должен быть development или blind; train нельзя использовать как acceptance gate"
            )

        raw_requirements = payload.get("kind_requirements") or {}
        if not isinstance(raw_requirements, dict):
            raise AdaptationValidationError("kind_requirements должен быть объектом")
        kind_requirements = {
            _clean(kind, field_name="kind_requirements key", limit=80): KindRequirement.from_dict(
                requirement,
                field_name=f"kind_requirements.{kind}",
            )
            for kind, requirement in raw_requirements.items()
        }

        expected_fingerprint = str(
            payload.get("expected_suite_fingerprint_sha256") or ""
        ).strip() or None
        if expected_fingerprint is not None:
            if len(expected_fingerprint) != 64 or any(
                char not in "0123456789abcdefABCDEF" for char in expected_fingerprint
            ):
                raise AdaptationValidationError(
                    "expected_suite_fingerprint_sha256 должен быть SHA-256 hex"
                )
            expected_fingerprint = expected_fingerprint.lower()

        return cls(
            policy_id=_clean(payload.get("policy_id"), field_name="policy_id"),
            version=version,
            split=split,
            min_case_count=_positive_int(
                payload.get("min_case_count", 1),
                field_name="min_case_count",
            ),
            min_micro_precision=_bounded(
                payload.get("min_micro_precision", 0.0),
                field_name="min_micro_precision",
            ),
            min_micro_recall=_bounded(
                payload.get("min_micro_recall", 0.0),
                field_name="min_micro_recall",
            ),
            min_micro_f1=_bounded(
                payload.get("min_micro_f1", 0.0),
                field_name="min_micro_f1",
            ),
            min_macro_f1=_bounded(
                payload.get("min_macro_f1", 0.0),
                field_name="min_macro_f1",
            ),
            kind_requirements=kind_requirements,
            expected_suite_fingerprint_sha256=expected_fingerprint,
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "policy_id": self.policy_id,
            "version": self.version,
            "split": self.split,
            "min_case_count": self.min_case_count,
            "min_micro_precision": self.min_micro_precision,
            "min_micro_recall": self.min_micro_recall,
            "min_micro_f1": self.min_micro_f1,
            "min_macro_f1": self.min_macro_f1,
            "kind_requirements": {
                kind: {
                    "min_case_count": requirement.min_case_count,
                    "min_micro_f1": requirement.min_micro_f1,
                }
                for kind, requirement in sorted(self.kind_requirements.items())
            },
            "expected_suite_fingerprint_sha256": self.expected_suite_fingerprint_sha256,
        }

    def fingerprint(self) -> str:
        return hashlib.sha256(_canonical_json(self.as_dict()).encode("utf-8")).hexdigest()


class StoryBenchmarkQualityGate:
    @staticmethod
    def evaluate(
        suite_report: dict[str, Any],
        policy: StoryBenchmarkQualityPolicy | dict[str, Any],
    ) -> dict[str, Any]:
        if not isinstance(suite_report, dict):
            raise AdaptationValidationError("suite report должен быть JSON-объектом")
        resolved_policy = (
            policy
            if isinstance(policy, StoryBenchmarkQualityPolicy)
            else StoryBenchmarkQualityPolicy.from_dict(policy)
        )

        by_split = suite_report.get("by_split")
        if not isinstance(by_split, dict):
            raise AdaptationValidationError("suite report.by_split должен быть объектом")
        split_report = by_split.get(resolved_policy.split)
        if not isinstance(split_report, dict):
            return StoryBenchmarkQualityGate._verdict(
                suite_report,
                resolved_policy,
                observed=None,
                checks=[],
                failures=[
                    {
                        "code": "required_split_missing",
                        "message": f"В benchmark report отсутствует split={resolved_policy.split}",
                    }
                ],
            )

        micro = split_report.get("micro") or {}
        macro = split_report.get("macro") or {}
        if not isinstance(micro, dict) or not isinstance(macro, dict):
            raise AdaptationValidationError("split report micro/macro должны быть объектами")

        checks: list[dict[str, Any]] = []
        failures: list[dict[str, Any]] = []

        def check_min(code: str, actual: float | int, minimum: float | int) -> None:
            passed = actual >= minimum
            row = {
                "code": code,
                "actual": actual,
                "minimum": minimum,
                "passed": passed,
            }
            checks.append(row)
            if not passed:
                failures.append(
                    {
                        "code": code,
                        "message": f"{code}: {actual} < {minimum}",
                        "actual": actual,
                        "minimum": minimum,
                    }
                )

        case_count = int(split_report.get("case_count", 0))
        check_min("case_count", case_count, resolved_policy.min_case_count)
        check_min(
            "micro_precision",
            float(micro.get("precision", 0.0)),
            resolved_policy.min_micro_precision,
        )
        check_min(
            "micro_recall",
            float(micro.get("recall", 0.0)),
            resolved_policy.min_micro_recall,
        )
        check_min(
            "micro_f1",
            float(micro.get("f1", 0.0)),
            resolved_policy.min_micro_f1,
        )
        check_min(
            "macro_f1",
            float(macro.get("f1", 0.0)),
            resolved_policy.min_macro_f1,
        )

        by_kind = split_report.get("by_kind") or {}
        if not isinstance(by_kind, dict):
            raise AdaptationValidationError("split report.by_kind должен быть объектом")
        for kind, requirement in sorted(resolved_policy.kind_requirements.items()):
            metrics = by_kind.get(kind)
            if not isinstance(metrics, dict):
                failures.append(
                    {
                        "code": "kind_missing",
                        "kind": kind,
                        "message": f"В benchmark split нет обязательного kind={kind}",
                    }
                )
                continue
            check_min(
                f"kind:{kind}:case_count",
                int(metrics.get("case_count", 0)),
                requirement.min_case_count,
            )
            kind_micro = metrics.get("micro") or {}
            if not isinstance(kind_micro, dict):
                raise AdaptationValidationError(f"by_kind.{kind}.micro должен быть объектом")
            check_min(
                f"kind:{kind}:micro_f1",
                float(kind_micro.get("f1", 0.0)),
                requirement.min_micro_f1,
            )

        suite_fingerprint = str(
            suite_report.get("suite_fingerprint_sha256") or ""
        ).strip().lower() or None
        expected = resolved_policy.expected_suite_fingerprint_sha256
        if expected is not None:
            passed = suite_fingerprint == expected
            checks.append(
                {
                    "code": "suite_fingerprint",
                    "actual": suite_fingerprint,
                    "expected": expected,
                    "passed": passed,
                }
            )
            if not passed:
                failures.append(
                    {
                        "code": "suite_fingerprint_mismatch",
                        "message": "Benchmark suite fingerprint не совпадает с preregistered policy",
                        "actual": suite_fingerprint,
                        "expected": expected,
                    }
                )

        observed = {
            "case_count": case_count,
            "micro": {
                "precision": float(micro.get("precision", 0.0)),
                "recall": float(micro.get("recall", 0.0)),
                "f1": float(micro.get("f1", 0.0)),
            },
            "macro": {
                "f1": float(macro.get("f1", 0.0)),
            },
        }
        return StoryBenchmarkQualityGate._verdict(
            suite_report,
            resolved_policy,
            observed=observed,
            checks=checks,
            failures=failures,
        )

    @staticmethod
    def _verdict(
        suite_report: dict[str, Any],
        policy: StoryBenchmarkQualityPolicy,
        *,
        observed: dict[str, Any] | None,
        checks: list[dict[str, Any]],
        failures: list[dict[str, Any]],
    ) -> dict[str, Any]:
        return {
            "passed": not failures,
            "policy_id": policy.policy_id,
            "policy_version": policy.version,
            "policy_fingerprint_sha256": policy.fingerprint(),
            "split": policy.split,
            "suite_id": suite_report.get("suite_id"),
            "suite_fingerprint_sha256": suite_report.get("suite_fingerprint_sha256"),
            "observed": observed,
            "checks": checks,
            "failures": failures,
            "research_only": True,
        }
