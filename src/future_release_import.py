from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.future_releases import FutureReleaseError, FutureReleaseStore


IMPORT_CONTRACT_VERSION = 1
BUNDLE_ARRAYS = (
    "sources",
    "projects",
    "people",
    "entities",
    "aliases",
    "release_windows",
    "statuses",
    "project_people",
    "project_entities",
)


class FutureReleaseImportError(ValueError):
    pass


def _canonical_json(payload: Any) -> str:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _fingerprint(payload: Any) -> str:
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def _clean(value: Any, *, field_name: str, limit: int = 500, required: bool = False) -> str:
    text = " ".join(str(value or "").strip().split())
    if required and not text:
        raise FutureReleaseImportError(f"{field_name} не должен быть пустым")
    if len(text) > limit:
        raise FutureReleaseImportError(
            f"{field_name} длиннее допустимых {limit} символов"
        )
    return text


def _parse_dt(value: Any, *, field_name: str) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value or "").strip().replace("Z", "+00:00"))
        except ValueError as exc:
            raise FutureReleaseImportError(
                f"{field_name} должен быть ISO-8601 datetime"
            ) from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _sha256(value: Any, *, field_name: str) -> str:
    text = _clean(value, field_name=field_name, limit=64, required=True).lower()
    if len(text) != 64 or any(ch not in "0123456789abcdef" for ch in text):
        raise FutureReleaseImportError(f"{field_name} должен быть SHA-256 hex")
    return text


class FutureReleaseBatchImporter:
    """Атомарный import contract для внешних P9 collectors.

    Collector может быть любым, но registry принимает только воспроизводимый batch
    с provider/retrieved_at/source fingerprint. Повтор того же source snapshot
    идемпотентен, а частично импортированный bundle невозможен.
    """

    def __init__(self, store: FutureReleaseStore | str | Path) -> None:
        if isinstance(store, FutureReleaseStore):
            self.store = store
            self._owns_store = False
        else:
            self.store = FutureReleaseStore(store)
            self._owns_store = True
        self.conn = self.store.conn
        self._ensure_schema()

    def close(self) -> None:
        if self._owns_store:
            self.store.close()

    def __enter__(self) -> "FutureReleaseBatchImporter":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def _ensure_schema(self) -> None:
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS future_release_import_batches(
                batch_id VARCHAR PRIMARY KEY,
                provider VARCHAR NOT NULL,
                retrieved_at TIMESTAMPTZ NOT NULL,
                cursor_value VARCHAR,
                source_fingerprint_sha256 VARCHAR NOT NULL,
                bundle_fingerprint_sha256 VARCHAR NOT NULL,
                counts_json VARCHAR NOT NULL,
                imported_at TIMESTAMPTZ NOT NULL,
                UNIQUE(provider, source_fingerprint_sha256)
            )
            """
        )

    @staticmethod
    def _normalize_bundle(payload: Any) -> dict[str, list[dict[str, Any]]]:
        if not isinstance(payload, dict):
            raise FutureReleaseImportError("bundle должен быть JSON-объектом")
        unknown = sorted(set(payload) - set(BUNDLE_ARRAYS))
        if unknown:
            raise FutureReleaseImportError(
                "Неизвестные массивы bundle: " + ", ".join(unknown)
            )
        result: dict[str, list[dict[str, Any]]] = {}
        for key in BUNDLE_ARRAYS:
            raw = payload.get(key, [])
            if not isinstance(raw, list):
                raise FutureReleaseImportError(f"bundle.{key} должен быть массивом")
            items: list[dict[str, Any]] = []
            for index, item in enumerate(raw):
                if not isinstance(item, dict):
                    raise FutureReleaseImportError(
                        f"bundle.{key}[{index}] должен быть JSON-объектом"
                    )
                items.append(dict(item))
            result[key] = items
        return result

    @staticmethod
    def _validate_source_times(
        bundle: dict[str, list[dict[str, Any]]],
        retrieved_at: datetime,
    ) -> None:
        for index, source in enumerate(bundle["sources"]):
            source_retrieved = source.get("retrieved_at")
            if source_retrieved in {None, ""}:
                raise FutureReleaseImportError(
                    f"bundle.sources[{index}].retrieved_at обязателен"
                )
            parsed = _parse_dt(
                source_retrieved,
                field_name=f"bundle.sources[{index}].retrieved_at",
            )
            if parsed > retrieved_at:
                raise FutureReleaseImportError(
                    f"bundle.sources[{index}].retrieved_at не может быть позже batch.retrieved_at"
                )

    def import_batch(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise FutureReleaseImportError("batch должен быть JSON-объектом")
        version = payload.get("version", IMPORT_CONTRACT_VERSION)
        try:
            version = int(version)
        except (TypeError, ValueError) as exc:
            raise FutureReleaseImportError("version должен быть целым") from exc
        if version != IMPORT_CONTRACT_VERSION:
            raise FutureReleaseImportError(
                f"Поддерживается import contract version={IMPORT_CONTRACT_VERSION}"
            )

        batch_id = _clean(
            payload.get("batch_id"), field_name="batch_id", limit=180, required=True
        )
        provider = _clean(
            payload.get("provider"), field_name="provider", limit=180, required=True
        )
        retrieved_at = _parse_dt(payload.get("retrieved_at"), field_name="retrieved_at")
        cursor_value = _clean(
            payload.get("cursor"), field_name="cursor", limit=500
        ) or None
        source_fingerprint = _sha256(
            payload.get("source_fingerprint_sha256"),
            field_name="source_fingerprint_sha256",
        )
        bundle = self._normalize_bundle(payload.get("bundle"))
        self._validate_source_times(bundle, retrieved_at)
        bundle_fingerprint = _fingerprint(bundle)

        existing = self.conn.execute(
            """
            SELECT provider, source_fingerprint_sha256, bundle_fingerprint_sha256,
                   counts_json, imported_at
            FROM future_release_import_batches
            WHERE batch_id = ?
            """,
            [batch_id],
        ).fetchone()
        if existing is not None:
            if (
                str(existing[0]) != provider
                or str(existing[1]) != source_fingerprint
                or str(existing[2]) != bundle_fingerprint
            ):
                raise FutureReleaseImportError(
                    f"batch_id={batch_id} уже импортирован с другим fingerprint"
                )
            return {
                "version": IMPORT_CONTRACT_VERSION,
                "batch_id": batch_id,
                "provider": provider,
                "source_fingerprint_sha256": source_fingerprint,
                "bundle_fingerprint_sha256": bundle_fingerprint,
                "counts": json.loads(str(existing[3])),
                "imported_at": existing[4].isoformat(),
                "idempotent": True,
            }

        same_source = self.conn.execute(
            """
            SELECT batch_id, bundle_fingerprint_sha256, counts_json, imported_at
            FROM future_release_import_batches
            WHERE provider = ? AND source_fingerprint_sha256 = ?
            """,
            [provider, source_fingerprint],
        ).fetchone()
        if same_source is not None:
            if str(same_source[1]) != bundle_fingerprint:
                raise FutureReleaseImportError(
                    "Один source fingerprint не может соответствовать разным bundles"
                )
            return {
                "version": IMPORT_CONTRACT_VERSION,
                "batch_id": str(same_source[0]),
                "provider": provider,
                "source_fingerprint_sha256": source_fingerprint,
                "bundle_fingerprint_sha256": bundle_fingerprint,
                "counts": json.loads(str(same_source[2])),
                "imported_at": same_source[3].isoformat(),
                "idempotent": True,
                "duplicate_batch_id": batch_id,
            }

        counts = {key: len(bundle[key]) for key in BUNDLE_ARRAYS}
        imported_at = datetime.now(timezone.utc)
        self.conn.execute("BEGIN TRANSACTION")
        try:
            for item in bundle["sources"]:
                self.store.upsert_source(item)
            for item in bundle["projects"]:
                self.store.upsert_project(item)
            for item in bundle["people"]:
                self.store.upsert_person(item)
            for item in bundle["entities"]:
                self.store.upsert_entity(item)
            for item in bundle["aliases"]:
                self.store.add_alias(item)
            for item in bundle["release_windows"]:
                self.store.add_release_window(item)
            for item in bundle["statuses"]:
                self.store.add_status(item)
            for item in bundle["project_people"]:
                self.store.link_person(item)
            for item in bundle["project_entities"]:
                self.store.link_entity(item)

            self.conn.execute(
                """
                INSERT INTO future_release_import_batches(
                    batch_id, provider, retrieved_at, cursor_value,
                    source_fingerprint_sha256, bundle_fingerprint_sha256,
                    counts_json, imported_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    batch_id,
                    provider,
                    retrieved_at,
                    cursor_value,
                    source_fingerprint,
                    bundle_fingerprint,
                    _canonical_json(counts),
                    imported_at,
                ],
            )
            self.conn.execute("COMMIT")
        except Exception as exc:
            self.conn.execute("ROLLBACK")
            if isinstance(exc, (FutureReleaseError, FutureReleaseImportError)):
                raise
            raise FutureReleaseImportError(
                f"P9 batch import откатан: {exc}"
            ) from exc

        return {
            "version": IMPORT_CONTRACT_VERSION,
            "batch_id": batch_id,
            "provider": provider,
            "retrieved_at": retrieved_at.isoformat(),
            "cursor": cursor_value,
            "source_fingerprint_sha256": source_fingerprint,
            "bundle_fingerprint_sha256": bundle_fingerprint,
            "counts": counts,
            "imported_at": imported_at.isoformat(),
            "idempotent": False,
        }

    def history(self, provider: str | None = None) -> list[dict[str, Any]]:
        if provider:
            rows = self.conn.execute(
                """
                SELECT batch_id, provider, retrieved_at, cursor_value,
                       source_fingerprint_sha256, bundle_fingerprint_sha256,
                       counts_json, imported_at
                FROM future_release_import_batches
                WHERE provider = ?
                ORDER BY retrieved_at, batch_id
                """,
                [provider],
            ).fetchall()
        else:
            rows = self.conn.execute(
                """
                SELECT batch_id, provider, retrieved_at, cursor_value,
                       source_fingerprint_sha256, bundle_fingerprint_sha256,
                       counts_json, imported_at
                FROM future_release_import_batches
                ORDER BY provider, retrieved_at, batch_id
                """
            ).fetchall()
        return [
            {
                "batch_id": str(row[0]),
                "provider": str(row[1]),
                "retrieved_at": row[2].isoformat(),
                "cursor": str(row[3]) if row[3] is not None else None,
                "source_fingerprint_sha256": str(row[4]),
                "bundle_fingerprint_sha256": str(row[5]),
                "counts": json.loads(str(row[6])),
                "imported_at": row[7].isoformat(),
            }
            for row in rows
        ]
