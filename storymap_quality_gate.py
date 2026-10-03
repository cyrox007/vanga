from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from src.adaptation_analysis import AdaptationValidationError
from src.story_benchmark_gate import StoryBenchmarkQualityGate


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="P4 research: проверить benchmark report по preregistered quality policy"
    )
    parser.add_argument("report", type=Path, help="JSON report benchmark suite")
    parser.add_argument("policy", type=Path, help="JSON quality policy")
    parser.add_argument("--output", type=Path, default=None)
    return parser


def _load_json(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise AdaptationValidationError(f"{path}: ожидается JSON-объект")
    return payload


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        report = _load_json(args.report)
        policy = _load_json(args.policy)
        verdict = StoryBenchmarkQualityGate.evaluate(report, policy)
    except (OSError, json.JSONDecodeError, ValueError, AdaptationValidationError) as exc:
        print(f"Ошибка StoryMap quality gate: {exc}", file=sys.stderr)
        return 2

    serialized = json.dumps(verdict, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output is None:
        print(serialized, end="")
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        temporary = args.output.with_suffix(args.output.suffix + ".tmp")
        temporary.write_text(serialized, encoding="utf-8")
        temporary.replace(args.output)
        print(f"StoryMap quality verdict сохранён: {args.output}")

    return 0 if verdict["passed"] else 3


if __name__ == "__main__":
    raise SystemExit(main())
