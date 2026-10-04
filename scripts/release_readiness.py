from __future__ import annotations

import argparse
import json
from pathlib import Path

from src.release_readiness import ReleaseReadiness


_STATUS_LABELS = {
    "pass": "OK",
    "warn": "ПРЕДУПРЕЖДЕНИЕ",
    "fail": "БЛОКЕР",
}


def _markets(value: str) -> list[str]:
    result = [item.strip().upper() for item in value.split(",") if item.strip()]
    if not result:
        raise argparse.ArgumentTypeError("Нужно указать хотя бы один рынок")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Проверка готовности Vanga к production rollout без загрузки CatBoost"
    )
    parser.add_argument("--imdb-db", help="Путь к IMDb DuckDB")
    parser.add_argument("--future-db", help="Путь к P9 future_releases.duckdb")
    parser.add_argument(
        "--live-api",
        action="store_true",
        help="Дополнительно проверить живой локальный HTTP API Vanga",
    )
    parser.add_argument(
        "--api-base-url",
        default="http://127.0.0.1:9100",
        help="Base URL Vanga API для --live-api",
    )
    parser.add_argument(
        "--markets",
        type=_markets,
        default=["DE", "US", "NL"],
        help="Рынки future catalog через запятую, по умолчанию DE,US,NL",
    )
    parser.add_argument(
        "--strict-p9",
        action="store_true",
        help="Считать пустой/отсутствующий P9 registry блокером, а не предупреждением",
    )
    parser.add_argument("--json", action="store_true", help="Вывести машинный JSON")
    args = parser.parse_args()

    checker = ReleaseReadiness(
        imdb_db_path=args.imdb_db,
        future_db_path=args.future_db,
        root=Path(__file__).resolve().parents[1],
        strict_p9=args.strict_p9,
    )
    report = checker.run(
        live_api=args.live_api,
        api_base_url=args.api_base_url,
        markets=args.markets,
    )

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    else:
        print("Проверка готовности Vanga к релизу")
        print("=" * 56)
        for item in report["checks"]:
            label = _STATUS_LABELS.get(item["status"], item["status"].upper())
            print(f"[{label}] {item['name']}: {item['message']}")
        summary = report["summary"]
        print("-" * 56)
        print(
            "Итог: "
            f"OK={summary['pass']}, предупреждений={summary['warn']}, "
            f"блокеров={summary['fail']}"
        )
        print(
            "Статус: ГОТОВО К ВЫКЛАДКЕ"
            if report["ready"]
            else "Статус: ВЫКЛАДКА ЗАБЛОКИРОВАНА"
        )

    return 0 if report["ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
