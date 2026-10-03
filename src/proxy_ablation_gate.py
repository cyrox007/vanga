from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.proxy_hypotheses import ProxyHypothesisStore, ProxyHypothesisValidationError


RESULT_CONTRACT_VERSION = 1
_ALLOWED_METRICS = {"mae", "rmse", "r2"}


def _canonical_json(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _fingerprint(payload: Any) -> str:
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def _clean(value: Any, *, field_name: str, limit: int = 500, required: bool = False) -> str:
    text = " ".join(str(value or "").strip().split())
    if required and not text:
        raise ProxyHypothesisValidationError(f"{field_name} не должен быть пустым")
    if len(text) > limit:
        raise ProxyHypothesisValidationError(
            f"{field_name} длиннее допустимых {limit} символов"
        )
    return text


def _sha256_hex(value: Any, *, field_name: str) -> str:
    text = _clean(value, field_name=field_name, limit=64, required=True).lower()
    if len(text) != 64 or any(char not in "0123456789abcdef" for char in text):
        raise ProxyHypothesisValidationError(f"{field_name} должен быть SHA-256 hex")
    return text


def _float(value: Any, *, field_name: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ProxyHypothesisValidationError(f"{field_name} должен быть числом") from exc
    if not result == result or result in (float("inf"), float("-inf")):
        raise ProxyHypothesisValidationError(f"{field_name} должен быть конечным числом")
    return result


def _positive_int(value: Any, *, field_name: str) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise ProxyHypothesisValidationError(f"{field_name} должен быть целым числом") from exc
    if result < 1:
        raise ProxyHypothesisValidationError(f"{field_name} должен быть >= 1")
    return result


class ProxyAblationResultGate:
    """Проверяет результат P6 ablation против preregistered plan.

    Gate ничего не обучает и не добавляет признаки в production-модель. Он лишь
    фиксирует, что эксперимент действительно относится к конкретному plan,
    выполнен на одном dataset/holdout и удовлетворяет заранее заданному MAE
    contract. Принятая гипотеза всё равно требует отдельного ML-инкремента.
    """

    def __init__(self, store: ProxyHypothesisStore) -> None:
        self.store = store
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        self.store.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS proxy_ablation_results (
                result_id VARCHAR PRIMARY KEY,
                hypothesis_id VARCHAR NOT NULL,
                plan_fingerprint_sha256 VARCHAR NOT NULL,
                result_fingerprint_sha256 VARCHAR NOT NULL,
                dataset_fingerprint_sha256 VARCHAR NOT NULL,
                holdout_policy VARCHAR NOT NULL,
                holdout_start_year INTEGER NOT NULL,
                holdout_end_year INTEGER NOT NULL,
                holdout_row_count INTEGER NOT NULL,
                baseline_mae DOUBLE NOT NULL,
                candidate_mae DOUBLE NOT NULL,
                mae_delta DOUBLE NOT NULL,
                passed BOOLEAN NOT NULL,
                verdict VARCHAR NOT NULL,
                runner_id VARCHAR NOT NULL,
                runner_version VARCHAR NOT NULL,
                executed_at TIMESTAMPTZ NOT NULL,
                recorded_at TIMESTAMPTZ NOT NULL,
                UNIQUE(hypothesis_id, result_fingerprint_sha256)
            )
            """
        )

    @staticmethod
    def _parse_metrics(payload: Any, *, field_name: str) -> dict[str, float]:
        if not isinstance(payload, dict):
            raise ProxyHypothesisValidationError(f"{field_name} должен быть объектом")
        unknown = set(payload) - _ALLOWED_METRICS
        if unknown:
            raise ProxyHypothesisValidationError(
                f"{field_name} содержит неизвестные метрики: " + ", ".join(sorted(unknown))
            )
        if "mae" not in payload:
            raise ProxyHypothesisValidationError(f"{field_name}.mae обязателен")
        result = {key: _float(value, field_name=f"{field_name}.{key}") for key, value in payload.items()}
        if result["mae"] < 0:
            raise ProxyHypothesisValidationError(f"{field_name}.mae не может быть отрицательным")
        if "rmse" in result and result["rmse"] < 0:
            raise ProxyHypothesisValidationError(f"{field_name}.rmse не может быть отрицательным")
        return result

    @staticmethod
    def _parse_executed_at(value: Any) -> datetime:
        text = _clean(value, field_name="executed_at", limit=80, required=True)
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            result = datetime.fromisoformat(text)
        except ValueError as exc:
            raise ProxyHypothesisValidationError("executed_at должен быть ISO datetime") from exc
        if result.tzinfo is None:
            raise ProxyHypothesisValidationError("executed_at должен содержать timezone")
        return result.astimezone(timezone.utc)

    def evaluate(self, payload: dict[str, Any], *, record: bool = False) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise ProxyHypothesisValidationError("ablation result должен быть JSON-объектом")
        allowed = {
            "version",
            "result_id",
            "hypothesis_id",
            "plan_fingerprint_sha256",
            "dataset_fingerprint_sha256",
            "holdout",
            "candidate_features",
            "baseline_metrics",
            "candidate_metrics",
            "runner_id",
            "runner_version",
            "executed_at",
        }
        unknown = set(payload) - allowed
        if unknown:
            raise ProxyHypothesisValidationError(
                "ablation result содержит неизвестные поля: " + ", ".join(sorted(unknown))
            )
        try:
            version = int(payload.get("version", RESULT_CONTRACT_VERSION))
        except (TypeError, ValueError) as exc:
            raise ProxyHypothesisValidationError("version должен быть целым числом") from exc
        if version != RESULT_CONTRACT_VERSION:
            raise ProxyHypothesisValidationError(
                f"Поддерживается только ablation result version={RESULT_CONTRACT_VERSION}"
            )

        hypothesis_id = _clean(
            payload.get("hypothesis_id"), field_name="hypothesis_id", limit=180, required=True
        )
        plan = self.store.export_ablation_plan(hypothesis_id)
        if plan["status"] != "ablation_ready":
            raise ProxyHypothesisValidationError(
                f"Hypothesis должна иметь status=ablation_ready, сейчас {plan['status']}"
            )
        plan_fingerprint = _sha256_hex(
            payload.get("plan_fingerprint_sha256"), field_name="plan_fingerprint_sha256"
        )
        if plan_fingerprint != plan["plan_fingerprint_sha256"]:
            raise ProxyHypothesisValidationError(
                "Ablation result относится к другому preregistered plan"
            )
        dataset_fingerprint = _sha256_hex(
            payload.get("dataset_fingerprint_sha256"), field_name="dataset_fingerprint_sha256"
        )

        holdout = payload.get("holdout")
        if not isinstance(holdout, dict):
            raise ProxyHypothesisValidationError("holdout должен быть объектом")
        holdout_allowed = {"policy", "start_year", "end_year", "row_count", "baseline_dataset_fingerprint_sha256", "candidate_dataset_fingerprint_sha256"}
        holdout_unknown = set(holdout) - holdout_allowed
        if holdout_unknown:
            raise ProxyHypothesisValidationError(
                "holdout содержит неизвестные поля: " + ", ".join(sorted(holdout_unknown))
            )
        holdout_policy = _clean(
            holdout.get("policy"), field_name="holdout.policy", limit=180, required=True
        )
        expected_holdout_policy = str(plan["ablation"]["holdout_policy"])
        if holdout_policy != expected_holdout_policy:
            raise ProxyHypothesisValidationError(
                f"holdout.policy={holdout_policy!r} не совпадает с preregistered {expected_holdout_policy!r}"
            )
        start_year = _positive_int(holdout.get("start_year"), field_name="holdout.start_year")
        end_year = _positive_int(holdout.get("end_year"), field_name="holdout.end_year")
        if end_year < start_year:
            raise ProxyHypothesisValidationError("holdout.end_year не может быть раньше start_year")
        row_count = _positive_int(holdout.get("row_count"), field_name="holdout.row_count")
        baseline_dataset = _sha256_hex(
            holdout.get("baseline_dataset_fingerprint_sha256"),
            field_name="holdout.baseline_dataset_fingerprint_sha256",
        )
        candidate_dataset = _sha256_hex(
            holdout.get("candidate_dataset_fingerprint_sha256"),
            field_name="holdout.candidate_dataset_fingerprint_sha256",
        )
        if baseline_dataset != candidate_dataset or baseline_dataset != dataset_fingerprint:
            raise ProxyHypothesisValidationError(
                "Baseline и candidate обязаны использовать один и тот же dataset fingerprint"
            )

        candidate_features = payload.get("candidate_features")
        if not isinstance(candidate_features, list):
            raise ProxyHypothesisValidationError("candidate_features должен быть массивом")
        normalized_features = sorted(
            {
                _clean(item, field_name="candidate_features[]", limit=180, required=True)
                for item in candidate_features
            }
        )
        if len(normalized_features) != len(candidate_features):
            raise ProxyHypothesisValidationError(
                "candidate_features не должен содержать дубликаты"
            )
        expected_features = sorted(
            item["feature_name"] for item in plan["candidate_pre_release_features"]
        )
        if normalized_features != expected_features:
            raise ProxyHypothesisValidationError(
                "Набор candidate_features не совпадает с preregistered plan"
            )

        baseline = self._parse_metrics(payload.get("baseline_metrics"), field_name="baseline_metrics")
        candidate = self._parse_metrics(payload.get("candidate_metrics"), field_name="candidate_metrics")
        baseline_mae = baseline["mae"]
        candidate_mae = candidate["mae"]
        delta = candidate_mae - baseline_mae
        max_regression = float(plan["ablation"]["max_mae_regression"])
        require_improvement = bool(plan["ablation"]["require_improvement"])

        within_regression_budget = delta <= max_regression
        improved = candidate_mae < baseline_mae
        passed = within_regression_budget and (improved if require_improvement else True)
        if not within_regression_budget:
            verdict = "rejected_mae_regression"
        elif require_improvement and not improved:
            verdict = "rejected_no_improvement"
        else:
            verdict = "accepted_ablation"

        result_id = _clean(
            payload.get("result_id"), field_name="result_id", limit=180, required=True
        )
        runner_id = _clean(
            payload.get("runner_id"), field_name="runner_id", limit=180, required=True
        )
        runner_version = _clean(
            payload.get("runner_version"), field_name="runner_version", limit=100, required=True
        )
        executed_at = self._parse_executed_at(payload.get("executed_at"))

        fingerprint_payload = {
            "version": version,
            "result_id": result_id,
            "hypothesis_id": hypothesis_id,
            "plan_fingerprint_sha256": plan_fingerprint,
            "dataset_fingerprint_sha256": dataset_fingerprint,
            "holdout": {
                "policy": holdout_policy,
                "start_year": start_year,
                "end_year": end_year,
                "row_count": row_count,
            },
            "candidate_features": normalized_features,
            "baseline_metrics": baseline,
            "candidate_metrics": candidate,
            "runner_id": runner_id,
            "runner_version": runner_version,
            "executed_at": executed_at.isoformat(),
        }
        result_fingerprint = _fingerprint(fingerprint_payload)
        report = {
            **fingerprint_payload,
            "result_fingerprint_sha256": result_fingerprint,
            "mae_delta": round(delta, 8),
            "mae_improvement": round(baseline_mae - candidate_mae, 8),
            "max_mae_regression": max_regression,
            "require_improvement": require_improvement,
            "within_regression_budget": within_regression_budget,
            "improved": improved,
            "passed": passed,
            "verdict": verdict,
            "automatic_catboost_inclusion": False,
            "production_publication_allowed": False,
            "research_only": True,
        }
        if record:
            self._record(report)
        return report

    def _record(self, report: dict[str, Any]) -> None:
        existing = self.store.conn.execute(
            "SELECT result_fingerprint_sha256 FROM proxy_ablation_results WHERE result_id = ?",
            [report["result_id"]],
        ).fetchone()
        if existing is not None:
            if str(existing[0]) != report["result_fingerprint_sha256"]:
                raise ProxyHypothesisValidationError(
                    "result_id уже существует с другим fingerprint"
                )
            return

        self.store.conn.execute(
            """
            INSERT INTO proxy_ablation_results
            (result_id, hypothesis_id, plan_fingerprint_sha256,
             result_fingerprint_sha256, dataset_fingerprint_sha256,
             holdout_policy, holdout_start_year, holdout_end_year,
             holdout_row_count, baseline_mae, candidate_mae, mae_delta,
             passed, verdict, runner_id, runner_version, executed_at, recorded_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                report["result_id"],
                report["hypothesis_id"],
                report["plan_fingerprint_sha256"],
                report["result_fingerprint_sha256"],
                report["dataset_fingerprint_sha256"],
                report["holdout"]["policy"],
                report["holdout"]["start_year"],
                report["holdout"]["end_year"],
                report["holdout"]["row_count"],
                report["baseline_metrics"]["mae"],
                report["candidate_metrics"]["mae"],
                report["mae_delta"],
                report["passed"],
                report["verdict"],
                report["runner_id"],
                report["runner_version"],
                datetime.fromisoformat(report["executed_at"]),
                datetime.now(timezone.utc),
            ],
        )
        if report["passed"]:
            self.store.conn.execute(
                """
                UPDATE proxy_hypotheses
                SET status = 'accepted', rejection_reason = NULL, updated_at = ?
                WHERE hypothesis_id = ?
                """,
                [datetime.now(timezone.utc), report["hypothesis_id"]],
            )
        else:
            self.store.conn.execute(
                """
                UPDATE proxy_hypotheses
                SET status = 'rejected', rejection_reason = ?, updated_at = ?
                WHERE hypothesis_id = ?
                """,
                [
                    f"Ablation gate: {report['verdict']}; MAE delta={report['mae_delta']}",
                    datetime.now(timezone.utc),
                    report["hypothesis_id"],
                ],
            )

    def result_history(self, hypothesis_id: str) -> list[dict[str, Any]]:
        hypothesis_id = _clean(
            hypothesis_id, field_name="hypothesis_id", limit=180, required=True
        )
        rows = self.store.conn.execute(
            """
            SELECT result_id, plan_fingerprint_sha256, result_fingerprint_sha256,
                   dataset_fingerprint_sha256, holdout_policy,
                   holdout_start_year, holdout_end_year, holdout_row_count,
                   baseline_mae, candidate_mae, mae_delta, passed, verdict,
                   runner_id, runner_version, executed_at, recorded_at
            FROM proxy_ablation_results
            WHERE hypothesis_id = ?
            ORDER BY recorded_at, result_id
            """,
            [hypothesis_id],
        ).fetchall()
        return [
            {
                "result_id": row[0],
                "plan_fingerprint_sha256": row[1],
                "result_fingerprint_sha256": row[2],
                "dataset_fingerprint_sha256": row[3],
                "holdout": {
                    "policy": row[4],
                    "start_year": int(row[5]),
                    "end_year": int(row[6]),
                    "row_count": int(row[7]),
                },
                "baseline_mae": float(row[8]),
                "candidate_mae": float(row[9]),
                "mae_delta": float(row[10]),
                "passed": bool(row[11]),
                "verdict": row[12],
                "runner_id": row[13],
                "runner_version": row[14],
                "executed_at": row[15].isoformat(),
                "recorded_at": row[16].isoformat(),
            }
            for row in rows
        ]
