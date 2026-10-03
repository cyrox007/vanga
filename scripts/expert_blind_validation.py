#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from settings import config
from src.expert_blind_validation import ExpertBlindValidator
from src.expert_corpus import ExpertCorpusStore, ExpertCorpusValidationError


def _write(payload: dict, output: Path | None) -> None:
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if output is None:
        print(text, end="")
        return
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(output)
    print(f"Результат blind validation сохранён: {output}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="P5 Expert Analysis Corpus: blind manifest и post-hoc evaluation"
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=Path(config.EXPERT_CORPUS_DB_PATH),
        help="Путь к expert_corpus.duckdb",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    manifest = sub.add_parser(
        "manifest",
        help="Экспортировать case-only manifest без expert claims/interpetation",
    )
    manifest.add_argument(
        "--split",
        choices=["development", "blind", "external_transfer"],
        default="blind",
    )
    manifest.add_argument("--output", type=Path, default=None)

    evaluate = sub.add_parser(
        "evaluate",
        help="Сравнить сохранённый prediction run с закрытой expert-разметкой",
    )
    evaluate.add_argument("prediction", type=Path)
    evaluate.add_argument(
        "--split",
        choices=["development", "blind", "external_transfer"],
        default="blind",
    )
    evaluate.add_argument("--min-prediction-confidence", type=float, default=0.5)
    evaluate.add_argument("--min-gold-confidence", type=float, default=0.5)
    evaluate.add_argument(
        "--allow-without-supporting-evidence",
        action="store_true",
        help="Не требовать supporting evidence у gold claims (не рекомендуется для blind gate)",
    )
    evaluate.add_argument("--output", type=Path, default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    store = None
    try:
        store = ExpertCorpusStore(args.db)
        validator = ExpertBlindValidator(store)
        if args.command == "manifest":
            payload = validator.export_manifest(split=args.split)
            _write(payload, args.output)
            return 0

        prediction = json.loads(args.prediction.read_text(encoding="utf-8"))
        payload = validator.evaluate(
            prediction,
            split=args.split,
            min_prediction_confidence=args.min_prediction_confidence,
            min_gold_confidence=args.min_gold_confidence,
            require_supporting_evidence=not args.allow_without_supporting_evidence,
        )
        _write(payload, args.output)
        return 0
    except (OSError, json.JSONDecodeError, ValueError, ExpertCorpusValidationError) as exc:
        print(f"Ошибка Expert Corpus blind validation: {exc}", file=sys.stderr)
        return 2
    finally:
        if store is not None:
            store.close()


if __name__ == "__main__":
    raise SystemExit(main())
