from __future__ import annotations

import argparse
import json
from pathlib import Path

from src.future_release_import import FutureReleaseBatchImporter


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Атомарный импорт fingerprinted batch от P9 collector"
    )
    parser.add_argument("--db", help="Путь к future_releases.duckdb")
    sub = parser.add_subparsers(dest="command", required=True)

    imp = sub.add_parser("import", help="Импортировать collector batch JSON")
    imp.add_argument("batch_json")

    history = sub.add_parser("history", help="Показать историю импортов")
    history.add_argument("--provider")

    args = parser.parse_args()
    kwargs = {"store": args.db} if args.db else {"store": None}
    with FutureReleaseBatchImporter(**kwargs) as importer:
        if args.command == "import":
            payload = json.loads(Path(args.batch_json).read_text(encoding="utf-8"))
            result = importer.import_batch(payload)
        else:
            result = importer.history(args.provider)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
