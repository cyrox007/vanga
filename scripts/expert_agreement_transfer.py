#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from settings import config
from src.expert_agreement_transfer import (
    ExpertAgreementAnalyzer,
    HeldOutExpertTransferEvaluator,
)
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
    print(f"Результат expert evaluation сохранён: {output}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="P5: agreement/disagreement и held-out expert transfer evaluation"
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=Path(config.EXPERT_CORPUS_DB_PATH),
        help="Путь к expert_corpus.duckdb",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    agreement = sub.add_parser(
        "agreement",
        help="Сравнить явно размеченные structural classes разных экспертов",
    )
    agreement.add_argument(
        "--split",
        choices=["development", "blind", "external_transfer"],
        default="blind",
    )
    agreement.add_argument("--min-gold-confidence", type=float, default=0.5)
    agreement.add_argument(
        "--allow-without-supporting-evidence",
        action="store_true",
    )
    agreement.add_argument("--output", type=Path, default=None)

    manifest = sub.add_parser(
        "transfer-manifest",
        help="Экспортировать case-only manifest для held-out expert",
    )
    manifest.add_argument("--held-out-expert", required=True)
    manifest.add_argument(
        "--split",
        choices=["development", "blind", "external_transfer"],
        default="external_transfer",
    )
    manifest.add_argument("--min-gold-confidence", type=float, default=0.5)
    manifest.add_argument(
        "--allow-without-supporting-evidence",
        action="store_true",
    )
    manifest.add_argument("--output", type=Path, default=None)

    evaluate = sub.add_parser(
        "transfer-evaluate",
        help="Оценить prediction run на полностью held-out expert profile",
    )
    evaluate.add_argument("prediction", type=Path)
    evaluate.add_argument("--held-out-expert", required=True)
    evaluate.add_argument(
        "--split",
        choices=["development", "blind", "external_transfer"],
        default="external_transfer",
    )
    evaluate.add_argument("--min-prediction-confidence", type=float, default=0.5)
    evaluate.add_argument("--min-gold-confidence", type=float, default=0.5)
    evaluate.add_argument(
        "--allow-without-supporting-evidence",
        action="store_true",
    )
    evaluate.add_argument("--output", type=Path, default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    store = None
    try:
        store = ExpertCorpusStore(args.db)
        if args.command == "agreement":
            payload = ExpertAgreementAnalyzer(store).analyze(
                split=args.split,
                min_gold_confidence=args.min_gold_confidence,
                require_supporting_evidence=not args.allow_without_supporting_evidence,
            )
        elif args.command == "transfer-manifest":
            payload = HeldOutExpertTransferEvaluator(store).export_manifest(
                held_out_expert_id=args.held_out_expert,
                split=args.split,
                min_gold_confidence=args.min_gold_confidence,
                require_supporting_evidence=not args.allow_without_supporting_evidence,
            )
        else:
            prediction = json.loads(args.prediction.read_text(encoding="utf-8"))
            payload = HeldOutExpertTransferEvaluator(store).evaluate(
                prediction,
                held_out_expert_id=args.held_out_expert,
                split=args.split,
                min_prediction_confidence=args.min_prediction_confidence,
                min_gold_confidence=args.min_gold_confidence,
                require_supporting_evidence=not args.allow_without_supporting_evidence,
            )
        _write(payload, args.output)
        return 0
    except (OSError, json.JSONDecodeError, ValueError, ExpertCorpusValidationError) as exc:
        print(f"Ошибка Expert agreement/transfer evaluation: {exc}", file=sys.stderr)
        return 2
    finally:
        if store is not None:
            store.close()


if __name__ == "__main__":
    raise SystemExit(main())
