#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.expert_corpus_gate import evaluate_expert_corpus


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Readiness gate Expert Analysis Corpus")
    parser.add_argument("--db", type=Path, default=None)
    parser.add_argument("--min-train", type=int, default=5)
    parser.add_argument("--min-blind", type=int, default=2)
    parser.add_argument("--min-external", type=int, default=2)
    parser.add_argument("--min-claims", type=int, default=10)
    args = parser.parse_args(argv)
    result = evaluate_expert_corpus(
        args.db,
        min_train_cases=args.min_train,
        min_blind_cases=args.min_blind,
        min_external_cases=args.min_external,
        min_claims=args.min_claims,
    )
    print(json.dumps(result.as_dict(), ensure_ascii=False, indent=2))
    if result.blockers:
        print("Expert Corpus НЕ готов к blind/transfer испытанию", file=sys.stderr)
        return 2
    print("Expert Corpus готов к blind/transfer испытанию")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
