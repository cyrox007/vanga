#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.expert_corpus import ExpertCorpusStore, ExpertCorpusValidationError
from src.expert_ingest import ExpertIngestError, build_annotation_draft, collect_source_segments


SEALED_SPLITS = {"blind", "external_transfer"}


def _case_material(store: ExpertCorpusStore, case_id: str) -> dict:
    rows = store.conn.execute(
        """
        SELECT
            c.case_id,
            c.film_title,
            c.film_year,
            c.split,
            m.material_id,
            m.expert_id,
            m.title,
            m.source_url
        FROM expert_cases c
        JOIN expert_case_materials cm ON cm.case_id = c.case_id
        JOIN expert_materials m ON m.material_id = cm.material_id
        WHERE c.case_id = ?
        ORDER BY m.material_id
        """,
        [case_id],
    ).fetchall()
    if not rows:
        raise ExpertCorpusValidationError(f"Неизвестный expert case: {case_id}")
    if len(rows) != 1:
        raise ExpertCorpusValidationError(
            f"Для case {case_id} найдено материалов: {len(rows)}; автоматический draft требует ровно один material"
        )
    row = rows[0]
    return {
        "case_id": row[0], "film_title": row[1], "film_year": row[2], "split": row[3],
        "material_id": row[4], "expert_id": row[5], "material_title": row[6], "source_url": row[7],
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Автоматический сбор источника и черновая pre-annotation Expert Corpus")
    parser.add_argument("case_id", help="case_id из scripts/expert_corpus_annotate.py list")
    parser.add_argument("--db", type=Path, default=None, help="путь к expert_corpus.duckdb (по умолчанию settings.py)")
    parser.add_argument("--output", type=Path, default=None, help="куда сохранить JSON draft; по умолчанию data/expert/annotations/<case_id>.auto.json")
    parser.add_argument("--max-candidates", type=int, default=20, help="максимум кандидатов claims (по умолчанию 20)")
    parser.add_argument("--allow-sealed", action="store_true", help="явно разрешить blind/external_transfer; по умолчанию запрещено во избежание leakage")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.max_candidates <= 0 or args.max_candidates > 100:
        print("Ошибка Expert Corpus autodraft: --max-candidates должен быть в диапазоне 1..100", file=sys.stderr)
        return 2

    store = ExpertCorpusStore(args.db)
    try:
        meta = _case_material(store, args.case_id)
        if meta["split"] in SEALED_SPLITS and not args.allow_sealed:
            raise ExpertCorpusValidationError(
                f"case {args.case_id} относится к sealed split {meta['split']}; автоматический доступ заблокирован. "
                "Используйте --allow-sealed только после фиксации prediction artifact."
            )

        print(f"Expert Corpus: собираю источник для {meta['case_id']} — {meta['film_title']} ({meta['material_title']})", flush=True)
        provider, segments = collect_source_segments(meta["source_url"])
        print(f"Expert Corpus: источник получен; provider={provider}, сегментов={len(segments)}", flush=True)
        draft = build_annotation_draft(
            case_id=meta["case_id"], material_id=meta["material_id"], source_url=meta["source_url"],
            segments=segments, max_candidates=args.max_candidates,
        )
        draft["meta"].update({
            "film_title": meta["film_title"], "film_year": meta["film_year"], "split": meta["split"],
            "expert_id": meta["expert_id"], "material_title": meta["material_title"], "provider": provider,
        })

        output = args.output or ROOT / "data" / "expert" / "annotations" / f"{args.case_id}.auto.json"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(draft, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({
            "ok": True, "case_id": args.case_id, "provider": provider, "segments": len(segments),
            "candidates": len(draft["claims"]), "output": str(output), "requires_human_review": True,
            "next": f"Проверьте все поля «ЗАПОЛНИТЬ:», удалите ложные кандидаты и затем выполните validate {output}",
        }, ensure_ascii=False, indent=2))
        return 0
    except (ExpertCorpusValidationError, ExpertIngestError, OSError, ValueError) as exc:
        print(f"Ошибка Expert Corpus autodraft: {exc}", file=sys.stderr)
        return 2
    finally:
        store.close()


if __name__ == "__main__":
    raise SystemExit(main())
