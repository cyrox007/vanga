#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from src.expert_agreement import ExpertAgreementAnalyzer
from src.expert_corpus import ExpertCorpusStore, ExpertCorpusValidationError


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "P5 research: agreement/disagreement структурной разметки экспертов "
            "без построения общей вкусовой шкалы"
        )
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=None,
        help="Путь к expert_corpus.duckdb; по умолчанию используется settings.py",
    )
    parser.add_argument(
        "--split",
        default="development",
        choices=["train", "development", "blind", "external_transfer"],
    )
    parser.add_argument("--min-confidence", type=float, default=0.5)
    parser.add_argument(
        "--allow-without-supporting-evidence",
        action="store_true",
        help="Не требовать supporting evidence для включения claim в анализ",
    )
    parser.add_argument("--output", type=Path, default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    store = None
    try:
        store = ExpertCorpusStore(args.db)
        report = ExpertAgreementAnalyzer(store).analyze(
            split=args.split,
            min_confidence=args.min_confidence,
            require_supporting_evidence=not args.allow_without_supporting_evidence,
        )
    except (OSError, ValueError, ExpertCorpusValidationError) as exc:
        print(f"Ошибка анализа agreement/disagreement: {exc}", file=sys.stderr)
        return 2
    finally:
        if store is not None:
            store.close()

    payload = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output is None:
        print(payload, end="")
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        temporary = args.output.with_suffix(args.output.suffix + ".tmp")
        temporary.write_text(payload, encoding="utf-8")
        temporary.replace(args.output)
        print(f"Отчёт agreement/disagreement сохранён: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
