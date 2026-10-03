from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from src.adaptation_analysis import AdaptationValidationError
from src.story_extractor import RuleBasedStoryExtractor


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="P4 baseline: воспроизводимое преобразование summary text в raw StoryMap"
    )
    parser.add_argument("text_file", type=Path, help="UTF-8 файл с пересказом/summary")
    parser.add_argument("--source-id", required=True)
    parser.add_argument("--language", default=None, help="например ru или en")
    parser.add_argument("--map-id", default=None)
    parser.add_argument("--max-sentences", type=int, default=120)
    parser.add_argument("--output", type=Path, default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        text = args.text_file.read_text(encoding="utf-8")
        result = RuleBasedStoryExtractor(
            max_sentences=args.max_sentences
        ).extract(
            text,
            source_id=args.source_id,
            language=args.language,
            map_id=args.map_id,
        )
    except (OSError, ValueError, AdaptationValidationError) as exc:
        print(f"Ошибка StoryMap extractor: {exc}", file=sys.stderr)
        return 2

    payload = json.dumps(
        result.as_dict(),
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    ) + "\n"
    if args.output is None:
        print(payload, end="")
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        temporary = args.output.with_suffix(args.output.suffix + ".tmp")
        temporary.write_text(payload, encoding="utf-8")
        temporary.replace(args.output)
        print(f"StoryMap сохранён: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
