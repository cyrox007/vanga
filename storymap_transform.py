from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from src.adaptation_analysis import AdaptationValidationError
from src.story_diff import StoryMap
from src.story_transformations import LexicalToneAnalyzer, StoryTransformationAnalyzer


def _read_json(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path}: ожидается JSON object")
    return payload


def _load_story_map(path: Path) -> StoryMap:
    payload = _read_json(path)
    raw = payload.get("story_map", payload)
    if not isinstance(raw, dict):
        raise ValueError(f"{path}: story_map должен быть object")
    return StoryMap.from_dict(raw)


def _write_payload(payload: dict, output: Path | None) -> None:
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if output is None:
        print(text, end="")
        return
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(output)
    print(f"Результат сохранён: {output}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="P4 research: structural transformations и lexical tone baseline"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    transform = sub.add_parser(
        "transformations",
        help="Найти compression/rewrite/reorder поверх подтверждённого alignment",
    )
    transform.add_argument("source_map", type=Path)
    transform.add_argument("adaptation_map", type=Path)
    transform.add_argument("--output", type=Path, default=None)

    tone = sub.add_parser(
        "tone",
        help="Сравнить research-only lexical tone profiles двух summary",
    )
    tone.add_argument("source_text", type=Path)
    tone.add_argument("adaptation_text", type=Path)
    tone.add_argument("--source-language", choices=["ru", "en"], required=True)
    tone.add_argument("--adaptation-language", choices=["ru", "en"], required=True)
    tone.add_argument("--output", type=Path, default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "transformations":
            result = StoryTransformationAnalyzer.compare(
                _load_story_map(args.source_map),
                _load_story_map(args.adaptation_map),
            )
        else:
            source_text = args.source_text.read_text(encoding="utf-8")
            adaptation_text = args.adaptation_text.read_text(encoding="utf-8")
            source = LexicalToneAnalyzer.profile(
                source_text,
                language=args.source_language,
            )
            adaptation = LexicalToneAnalyzer.profile(
                adaptation_text,
                language=args.adaptation_language,
            )
            result = LexicalToneAnalyzer.compare(source, adaptation)
        _write_payload(result, args.output)
        return 0
    except (OSError, ValueError, json.JSONDecodeError, AdaptationValidationError) as exc:
        print(f"Ошибка StoryMap transformation analysis: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
