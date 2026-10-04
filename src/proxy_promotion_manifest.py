from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from src.proxy_ablation import PROXY_AUDIT_VERSION
from src.proxy_candidate_schema import (
    CANDIDATE_SCHEMA_REGISTRY_VERSION,
    ProxyCandidateSchemaRegistry,
)
from src.proxy_candidate_schema_gate import (
    CANDIDATE_SCHEMA_GATE_VERSION,
    ProxyCandidateSchemaGate,
)
from src.proxy_hypotheses import ProxyHypothesisValidationError
from src.proxy_materializers import MATERIALIZER_VERSION


PROMOTION_MANIFEST_VERSION = 1
PROMOTION_STATUS = "promotion_manifest_frozen"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _canonical_json(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _fingerprint(payload: Any) -> str:
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def _clean(value: Any, *, field_name: str, limit: int = 300, required: bool = False) -> str:
    text = " ".join(str(value or "").strip().split())
    if required and not text:
        raise ProxyHypothesisValidationError(f"{field_name} не должен быть пустым")
    if len(text) > limit:
        raise ProxyHypothesisValidationError(
            f"{field_name} длиннее допустимых {limit} символов"
        )
    return text


def _positive_int(value: Any, *, field_name: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ProxyHypothesisValidationError(f"{field_name} должен быть целым числом") from exc
    if parsed < 1:
        raise ProxyHypothesisValidationError(f"{field_name} должен быть >= 1")
    return parsed


def _base_schema_version(label: str) -> int | None:
    match = re.fullmatch(r"v(\d+)", str(label or "").strip().casefold())
    return int(match.group(1)) if match else None


class ProxyPromotionManifestRegistry:
    """Immutable bridge: combined_validated research schema -> proposed model schema.

    Manifest фиксирует полный порядок features и версии materializer/audit/gate, но
    намеренно не меняет ``traning.py`` и не публикует модель.
    """

    def __init__(self, registry: ProxyCandidateSchemaRegistry | str | Path) -> None:
        if isinstance(registry, ProxyCandidateSchemaRegistry):
            self.schemas = registry
            self._owns_registry = False
        else:
            self.schemas = ProxyCandidateSchemaRegistry(registry)
            self._owns_registry = True
        self.conn = self.schemas.conn
        self.schema_gate = ProxyCandidateSchemaGate(self.schemas)
        self._ensure_schema()

    def close(self) -> None:
        if self._owns_registry:
            self.schemas.close()

    def __enter__(self) -> "ProxyPromotionManifestRegistry":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def _ensure_schema(self) -> None:
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS proxy_promotion_manifests (
                promotion_id VARCHAR PRIMARY KEY,
                schema_id VARCHAR NOT NULL UNIQUE,
                target_model_schema_version INTEGER NOT NULL UNIQUE,
                target_model_schema_label VARCHAR NOT NULL UNIQUE,
                candidate_schema_fingerprint_sha256 VARCHAR NOT NULL,
                combined_gate_fingerprint_sha256 VARCHAR NOT NULL,
                feature_order_fingerprint_sha256 VARCHAR NOT NULL,
                manifest_json VARCHAR NOT NULL,
                manifest_fingerprint_sha256 VARCHAR NOT NULL UNIQUE,
                status VARCHAR NOT NULL,
                created_at TIMESTAMPTZ NOT NULL
            )
            """
        )

    @staticmethod
    def _base_feature_names(value: Any) -> list[str]:
        if not isinstance(value, (list, tuple)) or not value:
            raise ProxyHypothesisValidationError(
                "base_feature_names должен быть непустым ordered массивом"
            )
        result: list[str] = []
        seen: set[str] = set()
        for raw in value:
            name = _clean(
                raw,
                field_name="base_feature_names[]",
                limit=180,
                required=True,
            )
            if name in seen:
                raise ProxyHypothesisValidationError(
                    f"base_feature_names содержит дубликат: {name}"
                )
            seen.add(name)
            result.append(name)
        return result

    def freeze(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise ProxyHypothesisValidationError(
                "promotion manifest payload должен быть JSON-объектом"
            )
        schema_id = _clean(
            payload.get("schema_id"),
            field_name="schema_id",
            limit=180,
            required=True,
        )
        schema = self.schemas.get(schema_id)
        result = self.schema_gate.result(schema_id)
        if result is None:
            raise ProxyHypothesisValidationError(
                f"Candidate schema {schema_id} не имеет combined ablation result"
            )
        if result["lifecycle_status"] != "combined_validated" or not result["passed"]:
            raise ProxyHypothesisValidationError(
                f"Promotion разрешён только для combined_validated schema, сейчас "
                f"{result['lifecycle_status']}"
            )
        if result["schema_fingerprint_sha256"] != schema[
            "schema_fingerprint_sha256"
        ]:
            raise ProxyHypothesisValidationError(
                "Combined result относится к другому frozen schema fingerprint"
            )
        if result["dataset_fingerprint_sha256"] != schema[
            "dataset_fingerprint_sha256"
        ]:
            raise ProxyHypothesisValidationError(
                "Combined result dataset fingerprint не совпадает с frozen schema"
            )

        target_version = _positive_int(
            payload.get("target_model_schema_version"),
            field_name="target_model_schema_version",
        )
        target_label = _clean(
            payload.get("target_model_schema_label") or f"v{target_version}",
            field_name="target_model_schema_label",
            limit=100,
            required=True,
        )
        base_version = _base_schema_version(schema["base_schema"])
        if base_version is not None and target_version <= base_version:
            raise ProxyHypothesisValidationError(
                f"target_model_schema_version должен быть > base {base_version}"
            )
        if target_label.casefold() == str(schema["base_schema"]).casefold():
            raise ProxyHypothesisValidationError(
                "target model schema label не должен совпадать с base schema"
            )

        base_features = self._base_feature_names(payload.get("base_feature_names"))
        added_features = list(schema["model_feature_names"])
        overlap = sorted(set(base_features) & set(added_features))
        if overlap:
            raise ProxyHypothesisValidationError(
                "Candidate schema содержит features, уже присутствующие в base feature order: "
                + ", ".join(overlap)
            )
        full_feature_order = base_features + added_features
        feature_order_fingerprint = _fingerprint(full_feature_order)

        promotion_id = _clean(
            payload.get("promotion_id") or f"proxy-promotion-{uuid4().hex[:16]}",
            field_name="promotion_id",
            limit=180,
            required=True,
        )
        body = {
            "version": PROMOTION_MANIFEST_VERSION,
            "promotion_id": promotion_id,
            "status": PROMOTION_STATUS,
            "candidate_schema_id": schema_id,
            "candidate_schema_label": schema["schema_label"],
            "candidate_schema_version": schema["schema_version"],
            "candidate_schema_fingerprint_sha256": schema[
                "schema_fingerprint_sha256"
            ],
            "combined_gate_result_id": result["result_id"],
            "combined_gate_fingerprint_sha256": result[
                "gate_fingerprint_sha256"
            ],
            "dataset_fingerprint_sha256": result[
                "dataset_fingerprint_sha256"
            ],
            "base_model_schema": schema["base_schema"],
            "target_model_schema_version": target_version,
            "target_model_schema_label": target_label,
            "base_feature_names": base_features,
            "added_feature_names": added_features,
            "full_feature_order": full_feature_order,
            "feature_order_fingerprint_sha256": feature_order_fingerprint,
            "added_feature_types": {
                feature_name: "numeric" for feature_name in added_features
            },
            "materializer_contract": {
                "proxy_source_materializers_version": MATERIALIZER_VERSION,
                "temporal_proxy_auditor_version": PROXY_AUDIT_VERSION,
                "candidate_schema_registry_version": CANDIDATE_SCHEMA_REGISTRY_VERSION,
                "candidate_schema_gate_version": CANDIDATE_SCHEMA_GATE_VERSION,
            },
            "required_source_layers": schema["required_source_layers"],
            "source_hypothesis_ids": schema["hypothesis_ids"],
            "model_training_required": True,
            "production_quality_gate_required": True,
            "final_refit_required": True,
            "automatic_training_code_change": False,
            "automatic_catboost_inclusion": False,
            "publication_allowed": False,
        }
        manifest_fingerprint = _fingerprint(body)

        existing = self.conn.execute(
            """
            SELECT manifest_json, manifest_fingerprint_sha256, created_at
            FROM proxy_promotion_manifests WHERE promotion_id = ?
            """,
            [promotion_id],
        ).fetchone()
        if existing is not None:
            if str(existing[1]) != manifest_fingerprint:
                raise ProxyHypothesisValidationError(
                    f"promotion_id={promotion_id} уже frozen с другим fingerprint"
                )
            restored = json.loads(str(existing[0]))
            restored["manifest_fingerprint_sha256"] = str(existing[1])
            restored["created_at"] = existing[2].isoformat()
            restored["idempotent"] = True
            return restored

        collision = self.conn.execute(
            """
            SELECT promotion_id FROM proxy_promotion_manifests
            WHERE schema_id = ? OR target_model_schema_version = ?
               OR target_model_schema_label = ?
            LIMIT 1
            """,
            [schema_id, target_version, target_label],
        ).fetchone()
        if collision is not None:
            raise ProxyHypothesisValidationError(
                f"Schema/model version уже связан с promotion_id={collision[0]}"
            )

        created_at = _now()
        self.conn.execute(
            """
            INSERT INTO proxy_promotion_manifests(
                promotion_id, schema_id, target_model_schema_version,
                target_model_schema_label, candidate_schema_fingerprint_sha256,
                combined_gate_fingerprint_sha256,
                feature_order_fingerprint_sha256, manifest_json,
                manifest_fingerprint_sha256, status, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                promotion_id,
                schema_id,
                target_version,
                target_label,
                schema["schema_fingerprint_sha256"],
                result["gate_fingerprint_sha256"],
                feature_order_fingerprint,
                _canonical_json(body),
                manifest_fingerprint,
                PROMOTION_STATUS,
                created_at,
            ],
        )
        exported = dict(body)
        exported["manifest_fingerprint_sha256"] = manifest_fingerprint
        exported["created_at"] = created_at.isoformat()
        exported["idempotent"] = False
        return exported

    def get(self, promotion_id: str) -> dict[str, Any]:
        promotion_id = _clean(
            promotion_id,
            field_name="promotion_id",
            limit=180,
            required=True,
        )
        row = self.conn.execute(
            """
            SELECT manifest_json, manifest_fingerprint_sha256, created_at
            FROM proxy_promotion_manifests WHERE promotion_id = ?
            """,
            [promotion_id],
        ).fetchone()
        if row is None:
            raise ProxyHypothesisValidationError(
                f"Неизвестный promotion manifest: {promotion_id}"
            )
        payload = json.loads(str(row[0]))
        payload["manifest_fingerprint_sha256"] = str(row[1])
        payload["created_at"] = row[2].isoformat()
        return payload

    def list_manifests(self) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """
            SELECT promotion_id, schema_id, target_model_schema_version,
                   target_model_schema_label, manifest_fingerprint_sha256,
                   status, created_at
            FROM proxy_promotion_manifests
            ORDER BY target_model_schema_version, promotion_id
            """
        ).fetchall()
        return [
            {
                "promotion_id": str(row[0]),
                "schema_id": str(row[1]),
                "target_model_schema_version": int(row[2]),
                "target_model_schema_label": str(row[3]),
                "manifest_fingerprint_sha256": str(row[4]),
                "status": str(row[5]),
                "created_at": row[6].isoformat(),
            }
            for row in rows
        ]
