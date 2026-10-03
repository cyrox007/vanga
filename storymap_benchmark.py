from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from src.adaptation_analysis import AdaptationValidationError
from src.story_alignment import story_map_from_payload
from src.story_benchmark import StoryAlignmentBenchmark, StoryMatchCandidateGenerator
from src.story_benchmark_runner import StoryBenchmarkSuiteRunner


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write(payload: dict[str, Any], output: Path | None) -> None:
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if output is None:
        print(text, end="")
        return
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(output)
    print(f"StoryMap benchmark результат сохранён: {output}")


def _known_character_matches(source, aligned_payload: dict[str, Any] | None) -> dict[str, str]:
    if not aligned_payload:
        return {}
    aligned = story_map_from_payload(aligned_payload)
    source_by_key = {node.key: node for node in source.nodes}
    result: dict[str, str] = {}
    for node in aligned.nodes:
        if node.kind != "character" or len(node.maps_from) != 1:
            continue
        source_key = node.maps_from[0]
        source_node = source_by_key.get(source_key)
        if source_node is not None and source_node.kind == "character":
            result[node.key] = source_key
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="P4: StoryMap candidate ranking и gold benchmark"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    candidates = sub.add_parser(
        "candidates",
        help="сгенерировать research-only source↔adaptation match candidates",
    )
    candidates.add_argument("source", type=Path)
    candidates.add_argument("adaptation", type=Path)
    candidates.add_argument(
        "--aligned",
        type=Path,
        default=None,
        help="опциональный alignment JSON для подтверждённого character context",
    )
    candidates.add_argument("--top-k", type=int, default=5)
    candidates.add_argument("--min-score", type=float, default=0.15)
    candidates.add_argument("--output", type=Path, default=None)

    evaluate = sub.add_parser(
        "evaluate",
        help="посчитать precision/recall/F1 accepted maps_from против gold case",
    )
    evaluate.add_argument("source", type=Path)
    evaluate.add_argument("adaptation", type=Path)
    evaluate.add_argument("gold", type=Path)
    evaluate.add_argument("predicted", type=Path)
    evaluate.add_argument("--output", type=Path, default=None)

    suite = sub.add_parser(
        "suite",
        help="запустить manifest из нескольких benchmark cases и агрегировать метрики",
    )
    suite.add_argument("manifest", type=Path, help="JSON suite manifest version=1")
    suite.add_argument(
        "--split",
        choices=("train", "development", "blind"),
        default=None,
        help="запустить только один split; для blind-проверки рекомендуется указывать явно",
    )
    suite.add_argument("--output", type=Path, default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "candidates":
            source = story_map_from_payload(_load(args.source))
            adaptation = story_map_from_payload(_load(args.adaptation))
            aligned_payload = _load(args.aligned) if args.aligned is not None else None
            known = _known_character_matches(source, aligned_payload)
            result = StoryMatchCandidateGenerator.generate(
                source,
                adaptation,
                known_character_matches=known,
                top_k=args.top_k,
                min_score=args.min_score,
            )
            result["known_character_match_count"] = len(known)
            _write(result, args.output)
            return 0

        if args.command == "evaluate":
            source = story_map_from_payload(_load(args.source))
            adaptation = story_map_from_payload(_load(args.adaptation))
            gold = _load(args.gold)
            predicted = story_map_from_payload(_load(args.predicted))
            result = StoryAlignmentBenchmark.evaluate(
                source=source,
                adaptation=adaptation,
                gold=gold,
                predicted_map=predicted,
            )
            _write(result, args.output)
            return 0

        if args.command == "suite":
            manifest_path = args.manifest.resolve()
            manifest = _load(manifest_path)
            if not isinstance(manifest, dict):
                raise AdaptationValidationError("Suite manifest должен быть JSON-объектом")
            result = StoryBenchmarkSuiteRunner(manifest_path.parent).run(
                manifest,
                only_split=args.split,
            )
            _write(result, args.output)
            return 0

        raise AdaptationValidationError("Неизвестная benchmark command")
    except (OSError, json.JSONDecodeError, ValueError, AdaptationValidationError) as exc:
        print(f"Ошибка StoryMap benchmark: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
