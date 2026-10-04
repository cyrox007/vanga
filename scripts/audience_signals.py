from __future__ import annotations

import argparse
import json
from pathlib import Path

from src.audience_signals import AudienceSignalStore


def _load(path: str):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="P8 registry агрегатных pre-release audience signals"
    )
    parser.add_argument("--db", help="Путь к audience_signals.duckdb")
    sub = parser.add_subparsers(dest="command", required=True)

    imp = sub.add_parser("import-bundle", help="Импортировать sources/projects/observations")
    imp.add_argument("bundle_json")

    snap = sub.add_parser("snapshot", help="Показать видимые audience signals as-of")
    snap.add_argument("project_id")
    snap.add_argument("--cutoff", required=True)
    snap.add_argument("--release-at", required=True)

    features = sub.add_parser("features", help="Материализовать exact protocol features")
    features.add_argument("project_id")
    features.add_argument("protocols_json")
    features.add_argument("--cutoff", required=True)
    features.add_argument("--release-at", required=True)

    args = parser.parse_args()
    kwargs = {"path": args.db} if args.db else {}
    with AudienceSignalStore(**kwargs) as store:
        if args.command == "import-bundle":
            payload = _load(args.bundle_json)
            if not isinstance(payload, dict):
                raise ValueError("Bundle должен быть JSON-объектом")
            source_ids = [store.upsert_source(item) for item in payload.get("sources", [])]
            project_ids = [store.upsert_project(item) for item in payload.get("projects", [])]
            observation_ids = [
                store.add_observation(item) for item in payload.get("observations", [])
            ]
            result = {
                "sources": source_ids,
                "projects": project_ids,
                "observations": observation_ids,
            }
        elif args.command == "snapshot":
            result = store.observations_as_of(
                args.project_id,
                args.cutoff,
                release_at=args.release_at,
            )
        else:
            protocols = _load(args.protocols_json)
            if isinstance(protocols, dict):
                protocols = protocols.get("protocols")
            result = store.features_as_of(
                args.project_id,
                args.cutoff,
                release_at=args.release_at,
                protocols=protocols,
            )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
