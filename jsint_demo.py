from __future__ import annotations

import argparse
import json
import sys

from jsint_client import JSIntDemoClient, JSIntDemoError


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Демонстрационная интеграция Vanga с jsint-site control plane."
    )
    parser.add_argument(
        "--site",
        required=True,
        help="Базовый URL jsint-site, например https://example.org",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("health", help="Проверить доступность demo API")

    activate = subparsers.add_parser("activate", help="Активировать эту установку")
    source = activate.add_mutually_exclusive_group(required=True)
    source.add_argument("--code", help="Одноразовый activation code")
    source.add_argument("--license", help="Подписанный license token")

    subparsers.add_parser("heartbeat", help="Отправить heartbeat")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    client = JSIntDemoClient(args.site)

    try:
        if args.command == "health":
            result = client.health()
        elif args.command == "activate":
            result = client.activate(
                activation_code=args.code,
                license_token=args.license,
            )
        else:
            result = client.heartbeat()
    except JSIntDemoError as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
