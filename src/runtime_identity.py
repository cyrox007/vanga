from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from settings import config


def _canonical_json(payload: Any) -> str:
    return json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _fingerprint(payload: Any) -> str:
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def _utc_iso_from_stat(path: Path) -> str | None:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat()
    except OSError:
        return None


def _load_freshness_manifest(db_path: Path) -> dict[str, Any] | None:
    manifest_path = Path(config.ABSPATH) / "data" / "imdb" / "freshness-manifest.json"
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None

    try:
        manifest_db = Path(str(payload.get("database") or "")).resolve()
        actual_db = db_path.resolve()
        manifest_size = int(payload.get("database_size_bytes"))
        actual_size = int(db_path.stat().st_size)
        manifest_mtime = datetime.fromisoformat(
            str(payload.get("database_mtime") or "").replace("Z", "+00:00")
        )
        if manifest_mtime.tzinfo is None:
            manifest_mtime = manifest_mtime.replace(tzinfo=timezone.utc)
        actual_mtime = datetime.fromtimestamp(db_path.stat().st_mtime, tz=timezone.utc)
    except (OSError, TypeError, ValueError):
        return None

    if manifest_db != actual_db or manifest_size != actual_size:
        return None
    if abs((manifest_mtime.astimezone(timezone.utc) - actual_mtime).total_seconds()) > 0.001:
        return None
    return payload


def build_runtime_identity(engine, generation: str) -> dict[str, Any]:
    """Возвращает descriptor именно тех model+IMDb DB, что использует запрос.

    Model generation сам по себе недостаточен: один model release может работать
    после обновления IMDb snapshot. Descriptor поэтому включает физическую
    идентичность открытой БД и, когда manifest соответствует этому exact файлу,
    logical IMDb fingerprint.
    """

    metadata = engine.metadata if isinstance(engine.metadata, dict) else {}
    db_path = Path(engine.db_path)
    model_path = Path(engine.model_path)

    try:
        db_stat = db_path.stat()
        db_size = int(db_stat.st_size)
        db_mtime_ns = int(db_stat.st_mtime_ns)
    except OSError:
        db_size = None
        db_mtime_ns = None

    manifest = _load_freshness_manifest(db_path)
    logical_fingerprint = (
        str(manifest.get("logical_fingerprint_sha256") or "").strip() or None
        if manifest
        else None
    )
    database = {
        "path": str(db_path),
        "name": db_path.name,
        "size_bytes": db_size,
        "mtime_utc": _utc_iso_from_stat(db_path),
        "physical_fingerprint_sha256": _fingerprint(
            {
                "path": str(db_path.resolve()) if db_path.exists() else str(db_path),
                "size_bytes": db_size,
                "mtime_ns": db_mtime_ns,
            }
        ),
        "logical_fingerprint_sha256": logical_fingerprint,
        "freshness_manifest_verified": bool(manifest),
        "freshness_manifest_as_of": manifest.get("as_of") if manifest else None,
    }

    model = {
        "generation": generation,
        "path": str(model_path),
        "schema_version": metadata.get("schema_version"),
        "training_mode": metadata.get("training_mode"),
        "published_model_fit": metadata.get("published_model_fit"),
        "published_metrics_source": metadata.get("published_metrics_source"),
        "validation_target_max_year": metadata.get("validation_target_max_year"),
        "refit_year_from": metadata.get("refit_year_from"),
        "refit_year_to": metadata.get("refit_year_to"),
        "refit_rows": metadata.get("refit_rows"),
        "model_size_bytes": metadata.get("model_size_bytes"),
        "validation_test_dataset_fingerprint_sha256": metadata.get(
            "test_dataset_fingerprint_sha256"
        ),
        "training_imdb_fingerprint_sha256": (
            (metadata.get("imdb_data_freshness") or {}).get(
                "logical_fingerprint_sha256"
            )
            if isinstance(metadata.get("imdb_data_freshness"), dict)
            else None
        ),
    }
    descriptor = {
        "version": 1,
        "model": model,
        "database": database,
    }
    descriptor["runtime_fingerprint_sha256"] = _fingerprint(descriptor)
    return descriptor
