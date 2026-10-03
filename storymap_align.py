from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from src.adaptation_analysis import AdaptationValidationError
from src.story_alignment import (
    BilingualStoryMapMerger,
    SourceAdaptationAligner,
    story_map_from_payload,
)


def _read_json(path: Path) -> Any:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload


def _read_optional_list(path: Path | None, *, field_name: str) -> list[dict[str, Any]]:
    if path is None:
        return []
    payload = _read_json(path)
    if isinstance(payload, dict):
        payload = payload.get(field_name)
    if not isinstance(payload, list):
        raise AdaptationValidationError(
            f"{path}: ожидается JSON-массив или объект с полем {field_name!r}"
        )
    if not all(isinstance(item, dict) for item in payload):
        raise AdaptationValidationError(f"{path}: каждый элемент должен быть JSON-объектом")
    return payload


def _write(payload: dict[str, Any], output: Path | None) -> None:
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if output is None:
        print(text, end="")
        return
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(output)
    print(f"StoryMap результат сохранён: {output}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="P4: RU/EN canonical merge и source↔adaptation StoryMap alignment"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    merge = sub.add_parser(
        "merge",
        help="объединить raw RU/EN maps одной стороны в canonical StoryMap",
    )
    merge.add_argument("maps", type=Path, nargs="+", help="JSON raw/extractor StoryMap")
    merge.add_argument("--map-id", required=True, help="ID derived canonical map")
    merge.add_argument(
        "--aliases",
        type=Path,
        default=None,
        help="JSON aliases[] для подтверждённых bilingual identities",
    )
    merge.add_argument("--output", type=Path, default=None)

    align = sub.add_parser(
        "align",
        help="сопоставить canonical source и adaptation maps и построить StoryDiff",
    )
    align.add_argument("source", type=Path)
    align.add_argument("adaptation", type=Path)
    align.add_argument(
        "--matches",
        type=Path,
        default=None,
        help="JSON matches[] для explicit source→adaptation mappings",
    )
    align.add_argument("--map-id", default=None, help="ID derived aligned adaptation map")
    align.add_argument("--output", type=Path, default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "merge":
            story_maps = [story_map_from_payload(_read_json(path)) for path in args.maps]
            aliases = _read_optional_list(args.aliases, field_name="aliases")
            result = BilingualStoryMapMerger.merge(
                story_maps,
                canonical_map_id=args.map_id,
                aliases=aliases,
            )
            _write(result, args.output)
            return 0

        if args.command == "align":
            source = story_map_from_payload(_read_json(args.source))
            adaptation = story_map_from_payload(_read_json(args.adaptation))
            matches = _read_optional_list(args.matches, field_name="matches")
            result = SourceAdaptationAligner.align(
                source,
                adaptation,
                explicit_matches=matches,
                derived_map_id=args.map_id,
            )
            _write(result, args.output)
            return 0

        raise AdaptationValidationError("Неизвестная команда storymap_align")
    except (OSError, json.JSONDecodeError, ValueError, AdaptationValidationError) as exc:
        print(f"Ошибка StoryMap alignment: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
