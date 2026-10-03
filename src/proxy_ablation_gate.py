from __future__ import annotations

from typing import Any

from src.proxy_hypotheses import ProxyHypothesisStore, ProxyHypothesisValidationError


class ProxyAblationResultGate:
    """Compatibility storage для исторической schema PR #71.

    ВАЖНО: этот класс больше не принимает quality decision. Единственный
    разрешённый путь — `TemporalProxyAvailabilityAuditor` ->
    `GenericProxyAblationGate` -> `ProxyAblationPipeline`.

    Конструктор оставлен, потому что canonical pipeline переиспользует уже
    существующую таблицу `proxy_ablation_results`, а `result_history()` нужен для
    чтения старых/новых research artifacts.
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

    def evaluate(self, payload: dict[str, Any], *, record: bool = False) -> dict[str, Any]:
        raise ProxyHypothesisValidationError(
            "Legacy ProxyAblationResultGate.evaluate отключён: он дублировал MAE decision "
            "и мог обходить обязательный temporal audit. Используйте "
            "scripts/proxy_ablation_pipeline.py / ProxyAblationPipeline."
        )

    def result_history(self, hypothesis_id: str) -> list[dict[str, Any]]:
        hypothesis_id = " ".join(str(hypothesis_id or "").strip().split())
        if not hypothesis_id:
            raise ProxyHypothesisValidationError("hypothesis_id не должен быть пустым")
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
                "result_id": str(row[0]),
                "plan_fingerprint_sha256": str(row[1]),
                "result_fingerprint_sha256": str(row[2]),
                "dataset_fingerprint_sha256": str(row[3]),
                "holdout": {
                    "policy": str(row[4]),
                    "start_year": int(row[5]),
                    "end_year": int(row[6]),
                    "row_count": int(row[7]),
                },
                "baseline_mae": float(row[8]),
                "candidate_mae": float(row[9]),
                "mae_delta": float(row[10]),
                "passed": bool(row[11]),
                "verdict": str(row[12]),
                "runner_id": str(row[13]),
                "runner_version": str(row[14]),
                "executed_at": row[15].isoformat(),
                "recorded_at": row[16].isoformat(),
            }
            for row in rows
        ]
