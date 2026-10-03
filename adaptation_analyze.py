from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from settings import config
from src.adaptation_analysis import (
    AdaptationAnalysisStore,
    AdaptationCase,
    AdaptationValidationError,
    load_case_seed_from_enrichment,
)
from src.story_diff import StoryDiffAnalyzer, StoryMap


def _dump(payload: dict) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


def cmd_init(args) -> int:
    store = AdaptationAnalysisStore(args.db)
    try:
        _dump(
            {
                "ok": True,
                "database": str(store.path),
                "message": "Хранилище ретроспективного анализа готово",
            }
        )
        return 0
    finally:
        store.close()


def cmd_bootstrap(args) -> int:
    payload = load_case_seed_from_enrichment(
        args.imdb_id,
        enrichment_db_path=args.enrichment_db,
    )
    target = Path(args.output)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    _dump(
        {
            "ok": True,
            "output": str(target),
            "imdb_id": args.imdb_id,
            "film_summary_sources": len(payload.get("sources") or []),
            "source_work_id": payload.get("source_work_id"),
            "message": (
                "Черновик создан. Добавьте RU/EN пересказы первоисточника и "
                "структурированные аннотации эксперта, затем выполните import."
            ),
        }
    )
    return 0


def cmd_diff(args) -> int:
    source_payload = json.loads(Path(args.source_map).read_text(encoding="utf-8"))
    adaptation_payload = json.loads(
        Path(args.adaptation_map).read_text(encoding="utf-8")
    )
    source_map = StoryMap.from_dict(source_payload)
    adaptation_map = StoryMap.from_dict(adaptation_payload)
    annotations = StoryDiffAnalyzer.compare(source_map, adaptation_map)

    result = {
        "ok": True,
        "source_map": source_map.map_id,
        "adaptation_map": adaptation_map.map_id,
        "annotations": annotations,
    }
    if args.output:
        target = Path(args.output)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(result, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        result["output"] = str(target)

    _dump(result)
    return 0


def cmd_import(args) -> int:
    source_path = Path(args.input)
    payload = json.loads(source_path.read_text(encoding="utf-8"))
    case = AdaptationCase.from_dict(payload)

    store = AdaptationAnalysisStore(args.db)
    try:
        features = store.replace_case(case)
    finally:
        store.close()

    _dump(
        {
            "ok": True,
            "case_id": case.case_id,
            "imdb_id": case.imdb_id,
            "sources": len(case.sources),
            "annotations": len(case.annotations),
            "features": features,
            "warning": (
                "Все признаки имеют префикс retro_adapt_ и не предназначены "
                "для прямого использования в pre-release модели Vanga."
            ),
        }
    )
    return 0


def cmd_features(args) -> int:
    store = AdaptationAnalysisStore(args.db)
    try:
        features = store.load_features(args.case_id)
    finally:
        store.close()

    if not features:
        _dump(
            {
                "ok": False,
                "case_id": args.case_id,
                "error": "Снимок признаков не найден",
            }
        )
        return 2

    _dump(
        {
            "ok": True,
            "case_id": args.case_id,
            "features": features,
        }
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Ретроспективный анализ адаптаций: film/source summaries + "
            "структурированные экспертные наблюдения."
        )
    )
    parser.add_argument(
        "--db",
        default=config.ADAPTATION_DB_PATH,
        help="Путь к adaptation.duckdb",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init", help="Создать/проверить схему БД")
    init_parser.set_defaults(func=cmd_init)

    bootstrap_parser = subparsers.add_parser(
        "bootstrap",
        help="Создать case JSON из RU/EN plot фильма в enrichment.duckdb",
    )
    bootstrap_parser.add_argument("--imdb-id", required=True)
    bootstrap_parser.add_argument(
        "--enrichment-db",
        default=config.ENRICHMENT_DB_PATH,
    )
    bootstrap_parser.add_argument("--output", required=True)
    bootstrap_parser.set_defaults(func=cmd_bootstrap)

    diff_parser = subparsers.add_parser(
        "diff",
        help=(
            "Сравнить канонические story maps первоисточника и экранизации "
            "и получить observation/structural_consequence annotations"
        ),
    )
    diff_parser.add_argument("--source-map", required=True)
    diff_parser.add_argument("--adaptation-map", required=True)
    diff_parser.add_argument("--output")
    diff_parser.set_defaults(func=cmd_diff)

    import_parser = subparsers.add_parser(
        "import",
        help="Проверить и импортировать заполненный case JSON",
    )
    import_parser.add_argument("--input", required=True)
    import_parser.set_defaults(func=cmd_import)

    features_parser = subparsers.add_parser(
        "features",
        help="Показать ретроспективные признаки case",
    )
    features_parser.add_argument("--case-id", required=True)
    features_parser.set_defaults(func=cmd_features)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except (AdaptationValidationError, FileNotFoundError, KeyError) as exc:
        _dump({"ok": False, "error": str(exc)})
        return 2
    except json.JSONDecodeError as exc:
        _dump({"ok": False, "error": f"Некорректный JSON: {exc}"})
        return 2


if __name__ == "__main__":
    sys.exit(main())
