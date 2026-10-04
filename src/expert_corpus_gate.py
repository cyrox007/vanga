from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import duckdb

from settings import config


@dataclass(frozen=True)
class ExpertCorpusGateResult:
    passed: bool
    blockers: list[str]
    warnings: list[str]
    metrics: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def evaluate_expert_corpus(
    path: str | Path | None = None,
    *,
    required_experts: int = 2,
    min_train_cases: int = 5,
    min_blind_cases: int = 2,
    min_external_cases: int = 2,
    min_claims: int = 10,
) -> ExpertCorpusGateResult:
    """Проверяет, что P5 готов к содержательному blind/transfer прогону.

    Gate проверяет не качество мнений эксперта, а достаточность и структуру
    корпуса: независимые профили, splits, claim→evidence и отсутствие пустой
    blind/external выборки. Соединение открывается в обычном режиме DuckDB,
    чтобы gate можно было запускать рядом с уже открытым ExpertCorpusStore;
    сам gate выполняет только SELECT-запросы.
    """
    db_path = Path(
        path
        or getattr(
            config,
            "EXPERT_CORPUS_DB_PATH",
            str(Path(config.ABSPATH) / "expert_corpus.duckdb"),
        )
    )
    if not db_path.is_file():
        return ExpertCorpusGateResult(
            False,
            [f"expert_corpus.duckdb не найден: {db_path}"],
            [],
            {},
        )

    conn = duckdb.connect(str(db_path))
    try:
        tables = {str(row[0]) for row in conn.execute("SHOW TABLES").fetchall()}
        required_tables = {
            "expert_profiles",
            "expert_cases",
            "expert_materials",
            "expert_case_materials",
            "expert_claims",
            "expert_evidence",
        }
        missing = sorted(required_tables - tables)
        if missing:
            return ExpertCorpusGateResult(
                False,
                ["Отсутствуют таблицы: " + ", ".join(missing)],
                [],
                {},
            )

        def count(sql: str, params: list[Any] | None = None) -> int:
            return int(conn.execute(sql, params or []).fetchone()[0])

        metrics = {
            "experts": count("SELECT COUNT(*) FROM expert_profiles"),
            "cases": count("SELECT COUNT(*) FROM expert_cases"),
            "materials": count("SELECT COUNT(*) FROM expert_materials"),
            "claims": count("SELECT COUNT(*) FROM expert_claims"),
            "evidence": count("SELECT COUNT(*) FROM expert_evidence"),
            "supporting_evidence": count(
                "SELECT COUNT(*) FROM expert_evidence WHERE polarity='supporting'"
            ),
            "contradicting_evidence": count(
                "SELECT COUNT(*) FROM expert_evidence WHERE polarity='contradicting'"
            ),
            "train_cases": count(
                "SELECT COUNT(*) FROM expert_cases WHERE split='train'"
            ),
            "development_cases": count(
                "SELECT COUNT(*) FROM expert_cases WHERE split='development'"
            ),
            "blind_cases": count(
                "SELECT COUNT(*) FROM expert_cases WHERE split='blind'"
            ),
            "external_transfer_cases": count(
                "SELECT COUNT(*) FROM expert_cases WHERE split='external_transfer'"
            ),
            "experts_with_materials": count(
                "SELECT COUNT(DISTINCT expert_id) FROM expert_materials"
            ),
            "claims_with_supporting_evidence": count(
                """
                SELECT COUNT(DISTINCT c.claim_id)
                FROM expert_claims c
                JOIN expert_evidence e ON e.claim_id=c.claim_id
                WHERE e.polarity='supporting'
                """
            ),
        }
        blockers: list[str] = []
        warnings: list[str] = []
        if metrics["experts"] < required_experts:
            blockers.append(
                f"Нужно минимум {required_experts} независимых экспертных профиля"
            )
        if metrics["experts_with_materials"] < required_experts:
            blockers.append(
                "Не у всех обязательных экспертных профилей есть материалы"
            )
        if metrics["train_cases"] < min_train_cases:
            blockers.append(
                f"Недостаточно train cases: {metrics['train_cases']} < {min_train_cases}"
            )
        if metrics["blind_cases"] < min_blind_cases:
            blockers.append(
                f"Недостаточно blind cases: {metrics['blind_cases']} < {min_blind_cases}"
            )
        if metrics["external_transfer_cases"] < min_external_cases:
            blockers.append(
                "Недостаточно external_transfer cases: "
                f"{metrics['external_transfer_cases']} < {min_external_cases}"
            )
        if metrics["claims"] < min_claims:
            blockers.append(
                f"Недостаточно структурированных claims: {metrics['claims']} < {min_claims}"
            )
        if metrics["claims_with_supporting_evidence"] < metrics["claims"]:
            blockers.append("Есть claims без supporting evidence")
        if metrics["contradicting_evidence"] == 0:
            warnings.append(
                "В корпусе пока нет contradicting evidence; возможен confirmation bias"
            )
        if metrics["development_cases"] == 0:
            warnings.append("Development split пуст")
        return ExpertCorpusGateResult(not blockers, blockers, warnings, metrics)
    finally:
        conn.close()
