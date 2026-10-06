#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.expert_corpus import (
    EXPERT_SPLITS,
    ExpertCorpusStore,
    ExpertCorpusValidationError,
)


_PLACEHOLDER_PREFIX = "ЗАПОЛНИТЬ:"
_REQUIRED_ANNOTATION_FIELDS = {
    "claims": (
        "timecode_or_section",
        "claim_summary",
        "observation",
        "structural_consequence",
        "expert_interpretation",
    ),
    "evidence": ("description", "locator"),
}


def _dump(payload: Any) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))


def _load_bundle(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ExpertCorpusValidationError("Корень annotation bundle должен быть JSON-объектом")
    for key in ("claims", "evidence"):
        rows = payload.get(key, [])
        if not isinstance(rows, list):
            raise ExpertCorpusValidationError(f"{key} должен быть массивом")
        if any(not isinstance(row, dict) for row in rows):
            raise ExpertCorpusValidationError(f"Каждый элемент {key} должен быть JSON-объектом")
    return payload


def _reject_unfilled_placeholders(payload: dict[str, Any]) -> None:
    for entity, fields in _REQUIRED_ANNOTATION_FIELDS.items():
        for index, row in enumerate(payload.get(entity, []), start=1):
            for field in fields:
                value = str(row.get(field) or "").strip()
                if value.startswith(_PLACEHOLDER_PREFIX):
                    raise ExpertCorpusValidationError(
                        f"{entity}[{index}].{field} не заполнено: замените шаблон «{_PLACEHOLDER_PREFIX}» реальной разметкой"
                    )


def _list_cases(store: ExpertCorpusStore, split: str | None) -> list[dict[str, Any]]:
    params: list[Any] = []
    where = ""
    if split:
        split = split.strip().casefold()
        if split not in EXPERT_SPLITS:
            raise ExpertCorpusValidationError(
                "split должен быть train, development, blind или external_transfer"
            )
        where = "WHERE c.split = ?"
        params.append(split)

    rows = store.conn.execute(
        f"""
        SELECT
            c.case_id,
            c.film_title,
            c.film_year,
            c.split,
            m.material_id,
            m.expert_id,
            p.display_name,
            m.title,
            m.source_url,
            COUNT(DISTINCT cl.claim_id) AS claim_count
        FROM expert_cases c
        JOIN expert_case_materials cm ON cm.case_id = c.case_id
        JOIN expert_materials m ON m.material_id = cm.material_id
        JOIN expert_profiles p ON p.expert_id = m.expert_id
        LEFT JOIN expert_claims cl ON cl.case_id = c.case_id AND cl.material_id = m.material_id
        {where}
        GROUP BY
            c.case_id, c.film_title, c.film_year, c.split,
            m.material_id, m.expert_id, p.display_name, m.title, m.source_url
        ORDER BY c.split, c.case_id, m.material_id
        """,
        params,
    ).fetchall()
    return [
        {
            "case_id": row[0],
            "film_title": row[1],
            "film_year": row[2],
            "split": row[3],
            "material_id": row[4],
            "expert_id": row[5],
            "expert": row[6],
            "material_title": row[7],
            "source_url": row[8],
            "claims": int(row[9]),
        }
        for row in rows
    ]


def _template(store: ExpertCorpusStore, case_id: str) -> dict[str, Any]:
    rows = store.conn.execute(
        """
        SELECT
            c.case_id, c.film_title, c.film_year, c.split,
            m.material_id, m.expert_id, p.display_name,
            m.title, m.source_url
        FROM expert_cases c
        JOIN expert_case_materials cm ON cm.case_id = c.case_id
        JOIN expert_materials m ON m.material_id = cm.material_id
        JOIN expert_profiles p ON p.expert_id = m.expert_id
        WHERE c.case_id = ?
        ORDER BY m.material_id
        """,
        [case_id],
    ).fetchall()
    if not rows:
        raise ExpertCorpusValidationError(f"Неизвестный expert case: {case_id}")
    if len(rows) != 1:
        raise ExpertCorpusValidationError(
            f"Для case {case_id} найдено материалов: {len(rows)}; укажите разметку вручную по нужному material_id"
        )

    row = rows[0]
    claim_id = f"{row[0]}-claim-001"
    evidence_id = f"{claim_id}-support-001"
    return {
        "meta": {
            "case_id": row[0],
            "film_title": row[1],
            "film_year": row[2],
            "split": row[3],
            "material_id": row[4],
            "expert_id": row[5],
            "expert": row[6],
            "material_title": row[7],
            "source_url": row[8],
            "instruction": "Заполните поля-заглушки по оригинальному материалу. Полные тексты/транскрипты не сохраняйте.",
        },
        "claims": [
            {
                "claim_id": claim_id,
                "case_id": row[0],
                "material_id": row[4],
                "dimension": "motivation",
                "change_type": "rewritten",
                "timecode_or_section": "ЗАПОЛНИТЬ: таймкод или раздел",
                "claim_summary": "ЗАПОЛНИТЬ: краткий формализованный тезис эксперта",
                "observation": "ЗАПОЛНИТЬ: наблюдаемый факт без оценки",
                "structural_consequence": "ЗАПОЛНИТЬ: структурное следствие наблюдения",
                "expert_interpretation": "ЗАПОЛНИТЬ: интерпретация эксперта отдельно от факта",
                "confidence": 0.8,
                "tags": [],
            }
        ],
        "evidence": [
            {
                "evidence_id": evidence_id,
                "claim_id": claim_id,
                "polarity": "supporting",
                "evidence_kind": "material_reference",
                "description": "ЗАПОЛНИТЬ: что именно подтверждает claim",
                "locator": "ЗАПОЛНИТЬ: таймкод/раздел/сцена",
                "reference_id": None,
                "confidence": 0.8,
            }
        ],
    }


def _validate(store: ExpertCorpusStore, payload: dict[str, Any]) -> dict[str, int]:
    claims = payload.get("claims", [])
    evidence = payload.get("evidence", [])
    forbidden = {"profiles", "cases", "materials", "case_materials"}.intersection(payload)
    if forbidden:
        raise ExpertCorpusValidationError(
            "Annotation bundle не должен менять metadata pilot: " + ", ".join(sorted(forbidden))
        )
    _reject_unfilled_placeholders(payload)
    store.conn.execute("BEGIN TRANSACTION")
    try:
        for row in claims:
            store.upsert_claim(row)
        for row in evidence:
            store.upsert_evidence(row)
    finally:
        store.conn.execute("ROLLBACK")
    return {"claims": len(claims), "evidence": len(evidence)}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Помощник разметки Expert Corpus Vanga"
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=None,
        help="путь к expert_corpus.duckdb (по умолчанию settings.py)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    list_parser = sub.add_parser("list", help="показать cases и материалы для разметки")
    list_parser.add_argument("--split", choices=sorted(EXPERT_SPLITS))

    template_parser = sub.add_parser("template", help="создать JSON-шаблон разметки case")
    template_parser.add_argument("case_id")
    template_parser.add_argument("--output", type=Path, default=None)

    validate_parser = sub.add_parser("validate", help="проверить annotation bundle без записи в БД")
    validate_parser.add_argument("bundle", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    store = ExpertCorpusStore(args.db)
    try:
        if args.command == "list":
            rows = _list_cases(store, args.split)
            _dump({"ok": True, "count": len(rows), "cases": rows})
            return 0
        if args.command == "template":
            payload = _template(store, args.case_id)
            if args.output:
                args.output.parent.mkdir(parents=True, exist_ok=True)
                args.output.write_text(
                    json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
                _dump({"ok": True, "output": str(args.output), "case_id": args.case_id})
            else:
                _dump(payload)
            return 0
        if args.command == "validate":
            result = _validate(store, _load_bundle(args.bundle))
            _dump({"ok": True, "validated": result, "message": "Разметка валидна; БД не изменена"})
            return 0
        raise ExpertCorpusValidationError("Неизвестная команда annotation CLI")
    except (ExpertCorpusValidationError, OSError, json.JSONDecodeError) as exc:
        print(f"Ошибка разметки Expert Corpus: {exc}", file=sys.stderr)
        return 2
    finally:
        store.close()


if __name__ == "__main__":
    raise SystemExit(main())
