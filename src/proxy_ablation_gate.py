from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from src.proxy_hypotheses import ProxyHypothesisStore, ProxyHypothesisValidationError


PERSISTENCE_CONTRACT_VERSION = 1


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


def _parse_timestamp(value: Any, *, field_name: str) -> datetime:
    text = _clean(value, field_name=field_name, limit=80, required=True)
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ProxyHypothesisValidationError(f"{field_name} должен быть ISO datetime") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ProxyHypothesisValidationError(f"{field_name} должен содержать timezone")
    return parsed.astimezone(timezone.utc)


def verify_generic_gate_report(report: dict[str, Any]) -> str:
    """Проверяет неизменность отчёта единственного gate из `src.proxy_ablation`.

    Здесь намеренно нет второй реализации MAE/holdout/feature logic. Все эти
    проверки уже выполнены `GenericProxyAblationGate`; persistence-слой лишь
    проверяет fingerprint его отчёта перед записью.
    """
    if not isinstance(report, dict):
        raise ProxyHypothesisValidationError("generic gate report должен быть JSON-объектом")
    supplied = _sha256_hex(
        report.get("gate_fingerprint_sha256"), field_name="gate_fingerprint_sha256"
    )
    canonical = dict(report)
    canonical.pop("gate_fingerprint_sha256", None)
    actual = _fingerprint(canonical)
    if supplied != actual:
        raise ProxyHypothesisValidationError(
            "Generic gate report fingerprint mismatch: отчёт изменён после gate"
        )
    if report.get("published") is not False:
        raise ProxyHypothesisValidationError(
            "P6 research gate report обязан иметь published=false"
        )
    if report.get("research_only") is not True:
        raise ProxyHypothesisValidationError(
            "P6 research gate report обязан иметь research_only=true"
        )
    comparison = report.get("comparison")
    if not isinstance(comparison, dict) or not isinstance(comparison.get("passed"), bool):
        raise ProxyHypothesisValidationError(
            "generic gate report.comparison.passed должен быть boolean"
        )
    return supplied


class ProxyAblationResultRegistry:
    """Immutable history результатов единственного P6 GenericProxyAblationGate.

    Этот класс НЕ сравнивает baseline/candidate повторно и НЕ меняет статус
    hypothesis. Один успешный temporal window ещё не означает production-ready
    proxy: после него могут потребоваться дополнительные окна/external transfer.
    """

    def __init__(self, store: ProxyHypothesisStore) -> None:
        self.store = store
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        self.store.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS proxy_ablation_run_results (
                result_id VARCHAR PRIMARY KEY,
                hypothesis_id VARCHAR NOT NULL,
                plan_fingerprint_sha256 VARCHAR NOT NULL,
                gate_fingerprint_sha256 VARCHAR NOT NULL,
                result_fingerprint_sha256 VARCHAR NOT NULL,
                proxy_audit_fingerprint_sha256 VARCHAR NOT NULL,
                dataset_fingerprint_sha256 VARCHAR NOT NULL,
                baseline_mae DOUBLE NOT NULL,
                candidate_mae DOUBLE NOT NULL,
                delta_mae DOUBLE NOT NULL,
                passed BOOLEAN NOT NULL,
                reason VARCHAR NOT NULL,
                runner_id VARCHAR NOT NULL,
                runner_version VARCHAR NOT NULL,
                executed_at TIMESTAMPTZ NOT NULL,
                recorded_at TIMESTAMPTZ NOT NULL,
                UNIQUE(hypothesis_id, gate_fingerprint_sha256)
            )
            """
        )

    def prepare(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise ProxyHypothesisValidationError("result envelope должен быть JSON-объектом")
        allowed = {
            "version",
            "result_id",
            "hypothesis_id",
            "plan_fingerprint_sha256",
            "gate_report",
            "runner_id",
            "runner_version",
            "executed_at",
        }
        unknown = set(payload) - allowed
        if unknown:
            raise ProxyHypothesisValidationError(
                "result envelope содержит неизвестные поля: " + ", ".join(sorted(unknown))
            )
        try:
            version = int(payload.get("version", PERSISTENCE_CONTRACT_VERSION))
        except (TypeError, ValueError) as exc:
            raise ProxyHypothesisValidationError("version должен быть целым числом") from exc
        if version != PERSISTENCE_CONTRACT_VERSION:
            raise ProxyHypothesisValidationError(
                f"Поддерживается result envelope version={PERSISTENCE_CONTRACT_VERSION}"
            )

        hypothesis_id = _clean(
            payload.get("hypothesis_id"), field_name="hypothesis_id", limit=180, required=True
        )
        plan = self.store.export_ablation_plan(hypothesis_id)
        if plan.get("status") != "ablation_ready":
            raise ProxyHypothesisValidationError(
                f"Для записи нового gate run hypothesis должна быть ablation_ready, сейчас {plan.get('status')}"
            )
        plan_fingerprint = _sha256_hex(
            payload.get("plan_fingerprint_sha256"), field_name="plan_fingerprint_sha256"
        )
        if plan_fingerprint != plan.get("plan_fingerprint_sha256"):
            raise ProxyHypothesisValidationError(
                "result envelope относится к другому preregistered plan"
            )

        gate_report = payload.get("gate_report")
        gate_fingerprint = verify_generic_gate_report(gate_report)
        if gate_report.get("hypothesis_id") != hypothesis_id:
            raise ProxyHypothesisValidationError(
                "generic gate report относится к другой hypothesis"
            )
        if gate_report.get("plan_fingerprint_sha256") != plan_fingerprint:
            raise ProxyHypothesisValidationError(
                "generic gate report относится к другому plan fingerprint"
            )

        audit_fingerprint = _sha256_hex(
            gate_report.get("proxy_audit_fingerprint_sha256"),
            field_name="proxy_audit_fingerprint_sha256",
        )
        dataset_fingerprint = _sha256_hex(
            gate_report.get("dataset_fingerprint_sha256"),
            field_name="dataset_fingerprint_sha256",
        )
        baseline = gate_report.get("baseline")
        candidate = gate_report.get("candidate")
        comparison = gate_report.get("comparison")
        if not isinstance(baseline, dict) or not isinstance(candidate, dict) or not isinstance(comparison, dict):
            raise ProxyHypothesisValidationError(
                "generic gate report должен содержать baseline/candidate/comparison"
            )
        try:
            baseline_mae = float(baseline["test_mae"])
            candidate_mae = float(candidate["test_mae"])
            delta_mae = float(comparison["delta_mae"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ProxyHypothesisValidationError(
                "generic gate report содержит некорректные MAE metrics"
            ) from exc
        passed = comparison.get("passed")
        if not isinstance(passed, bool):
            raise ProxyHypothesisValidationError("comparison.passed должен быть boolean")
        reason = _clean(
            comparison.get("reason"), field_name="comparison.reason", limit=180, required=True
        )

        result_id = _clean(
            payload.get("result_id"), field_name="result_id", limit=180, required=True
        )
        runner_id = _clean(
            payload.get("runner_id"), field_name="runner_id", limit=180, required=True
        )
        runner_version = _clean(
            payload.get("runner_version"), field_name="runner_version", limit=100, required=True
        )
        executed_at = _parse_timestamp(payload.get("executed_at"), field_name="executed_at")

        normalized = {
            "version": version,
            "result_id": result_id,
            "hypothesis_id": hypothesis_id,
            "plan_fingerprint_sha256": plan_fingerprint,
            "gate_fingerprint_sha256": gate_fingerprint,
            "proxy_audit_fingerprint_sha256": audit_fingerprint,
            "dataset_fingerprint_sha256": dataset_fingerprint,
            "baseline_mae": baseline_mae,
            "candidate_mae": candidate_mae,
            "delta_mae": delta_mae,
            "passed": passed,
            "reason": reason,
            "runner_id": runner_id,
            "runner_version": runner_version,
            "executed_at": executed_at.isoformat(),
        }
        return {
            **normalized,
            "result_fingerprint_sha256": _fingerprint(normalized),
            "status_transition_applied": False,
            "hypothesis_status": plan.get("status"),
            "next_step": "repeat_temporal_validation_or_explicit_finalize",
            "automatic_catboost_inclusion": False,
            "production_publication_allowed": False,
            "research_only": True,
        }

    def record(self, payload: dict[str, Any]) -> dict[str, Any]:
        report = self.prepare(payload)
        existing = self.store.conn.execute(
            """
            SELECT result_fingerprint_sha256
            FROM proxy_ablation_run_results
            WHERE result_id = ?
            """,
            [report["result_id"]],
        ).fetchone()
        if existing is not None:
            if str(existing[0]) != report["result_fingerprint_sha256"]:
                raise ProxyHypothesisValidationError(
                    "result_id уже существует с другим fingerprint"
                )
            return report

        duplicate_gate = self.store.conn.execute(
            """
            SELECT result_id
            FROM proxy_ablation_run_results
            WHERE hypothesis_id = ? AND gate_fingerprint_sha256 = ?
            """,
            [report["hypothesis_id"], report["gate_fingerprint_sha256"]],
        ).fetchone()
        if duplicate_gate is not None:
            raise ProxyHypothesisValidationError(
                f"Этот generic gate report уже записан как result_id={duplicate_gate[0]}"
            )

        self.store.conn.execute(
            """
            INSERT INTO proxy_ablation_run_results(
                result_id, hypothesis_id, plan_fingerprint_sha256,
                gate_fingerprint_sha256, result_fingerprint_sha256,
                proxy_audit_fingerprint_sha256, dataset_fingerprint_sha256,
                baseline_mae, candidate_mae, delta_mae, passed, reason,
                runner_id, runner_version, executed_at, recorded_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                report["result_id"],
                report["hypothesis_id"],
                report["plan_fingerprint_sha256"],
                report["gate_fingerprint_sha256"],
                report["result_fingerprint_sha256"],
                report["proxy_audit_fingerprint_sha256"],
                report["dataset_fingerprint_sha256"],
                report["baseline_mae"],
                report["candidate_mae"],
                report["delta_mae"],
                report["passed"],
                report["reason"],
                report["runner_id"],
                report["runner_version"],
                datetime.fromisoformat(report["executed_at"]),
                datetime.now(timezone.utc),
            ],
        )
        return report

    def history(self, hypothesis_id: str) -> list[dict[str, Any]]:
        hypothesis_id = _clean(
            hypothesis_id, field_name="hypothesis_id", limit=180, required=True
        )
        rows = self.store.conn.execute(
            """
            SELECT result_id, plan_fingerprint_sha256, gate_fingerprint_sha256,
                   result_fingerprint_sha256, proxy_audit_fingerprint_sha256,
                   dataset_fingerprint_sha256, baseline_mae, candidate_mae,
                   delta_mae, passed, reason, runner_id, runner_version,
                   executed_at, recorded_at
            FROM proxy_ablation_run_results
            WHERE hypothesis_id = ?
            ORDER BY recorded_at, result_id
            """,
            [hypothesis_id],
        ).fetchall()
        return [
            {
                "result_id": str(row[0]),
                "plan_fingerprint_sha256": str(row[1]),
                "gate_fingerprint_sha256": str(row[2]),
                "result_fingerprint_sha256": str(row[3]),
                "proxy_audit_fingerprint_sha256": str(row[4]),
                "dataset_fingerprint_sha256": str(row[5]),
                "baseline_mae": float(row[6]),
                "candidate_mae": float(row[7]),
                "delta_mae": float(row[8]),
                "passed": bool(row[9]),
                "reason": str(row[10]),
                "runner_id": str(row[11]),
                "runner_version": str(row[12]),
                "executed_at": row[13].isoformat(),
                "recorded_at": row[14].isoformat(),
            }
            for row in rows
        ]


# Старое имя оставлено как alias для импорта, но semantics теперь persistence-only.
ProxyAblationResultGate = ProxyAblationResultRegistry
