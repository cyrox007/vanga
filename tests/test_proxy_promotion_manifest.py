from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from src.proxy_candidate_schema import ProxyCandidateSchemaRegistry
from src.proxy_hypotheses import ProxyHypothesisValidationError
from src.proxy_promotion_manifest import ProxyPromotionManifestRegistry


def _fp(payload) -> str:
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()


class ProxyPromotionManifestTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "proxy.duckdb"
        self.schemas = ProxyCandidateSchemaRegistry(self.db)
        self.registry = ProxyPromotionManifestRegistry(self.schemas)
        self._seed_validated_schema()

    def tearDown(self):
        self.schemas.close()
        self.tmp.cleanup()

    def _seed_validated_schema(self, *, status="combined_validated"):
        schema_id = "schema-p6-1"
        schema_body = {
            "registry_version": 1,
            "schema_id": schema_id,
            "schema_label": "proxy-p6-1",
            "schema_version": 1,
            "status": status,
            "base_schema": "v15",
            "dataset_fingerprint_sha256": "d" * 64,
            "holdout": {
                "policy": "stable_temporal_last_two_years",
                "start_year": 2024,
                "end_year": 2025,
                "row_count": 100,
            },
            "hypothesis_ids": ["h-source", "h-prod"],
            "accepted_results": [],
            "feature_contracts": [
                {
                    "feature_name": "source_work_count",
                    "source_layer": "source_context",
                    "temporal_contract": "known_at_lte_cutoff",
                    "proxy_roles": ["primary"],
                    "supporting_hypothesis_ids": ["h-source"],
                    "acceptance_result_fingerprints": ["a" * 64],
                    "coverage_for_features": [],
                },
                {
                    "feature_name": "production_entity_count",
                    "source_layer": "production_context",
                    "temporal_contract": "known_at_lte_cutoff",
                    "proxy_roles": ["context"],
                    "supporting_hypothesis_ids": ["h-prod"],
                    "acceptance_result_fingerprints": ["b" * 64],
                    "coverage_for_features": [],
                },
            ],
            "model_feature_names": ["production_entity_count", "source_work_count"],
            "required_source_layers": ["production_context", "source_context"],
            "materializer_contract": "proxy_source_materializers_v1",
            "combined_ablation_required": True,
            "production_quality_gate_required": True,
            "automatic_catboost_inclusion": False,
            "production_publication_allowed": False,
        }
        schema_fp = _fp(schema_body)
        now = datetime.now(timezone.utc)
        self.schemas.conn.execute(
            """
            INSERT INTO proxy_candidate_schemas(
                schema_id, schema_label, schema_version, base_schema,
                dataset_fingerprint_sha256, holdout_json, hypothesis_ids_json,
                acceptance_results_json, feature_contracts_json, schema_json,
                schema_fingerprint_sha256, status, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                schema_id,
                schema_body["schema_label"],
                schema_body["schema_version"],
                schema_body["base_schema"],
                schema_body["dataset_fingerprint_sha256"],
                json.dumps(schema_body["holdout"]),
                json.dumps(schema_body["hypothesis_ids"]),
                "[]",
                json.dumps(schema_body["feature_contracts"]),
                json.dumps(schema_body),
                schema_fp,
                status,
                now,
            ],
        )
        # Constructor promotion registry уже создал compatibility result table.
        gate_body = {
            "version": 1,
            "schema_id": schema_id,
            "dummy": True,
        }
        gate_fp = _fp(gate_body)
        self.schemas.conn.execute(
            """
            INSERT INTO proxy_candidate_schema_results(
                result_id, schema_id, schema_fingerprint_sha256,
                plan_fingerprint_sha256, proxy_audit_fingerprint_sha256,
                gate_fingerprint_sha256, dataset_fingerprint_sha256,
                holdout_json, baseline_json, candidate_json, comparison_json,
                passed, verdict, gate_json, recorded_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                "combined-result-1",
                schema_id,
                schema_fp,
                "p" * 64,
                "u" * 64,
                gate_fp,
                "d" * 64,
                json.dumps(schema_body["holdout"]),
                json.dumps({"schema_label": "v15", "test_mae": 1.0}),
                json.dumps({"label": "proxy-p6-1", "test_mae": 0.95}),
                json.dumps({"passed": True, "delta_mae": -0.05}),
                True,
                "combined_validated",
                json.dumps(gate_body),
                now,
            ],
        )

    def _payload(self):
        return {
            "promotion_id": "promotion-1",
            "schema_id": "schema-p6-1",
            "target_model_schema_version": 16,
            "target_model_schema_label": "v16",
            "base_feature_names": ["genres_combined", "director_id", "writer_id"],
        }

    def test_freeze_binds_validated_schema_result_and_feature_order(self):
        result = self.registry.freeze(self._payload())
        self.assertEqual(result["status"], "promotion_manifest_frozen")
        self.assertEqual(result["target_model_schema_label"], "v16")
        self.assertEqual(
            result["full_feature_order"],
            [
                "genres_combined",
                "director_id",
                "writer_id",
                "production_entity_count",
                "source_work_count",
            ],
        )
        self.assertEqual(result["combined_gate_result_id"], "combined-result-1")
        self.assertEqual(result["dataset_fingerprint_sha256"], "d" * 64)
        self.assertFalse(result["publication_allowed"])
        self.assertFalse(result["automatic_catboost_inclusion"])
        self.assertTrue(result["production_quality_gate_required"])
        self.assertTrue(result["final_refit_required"])
        self.assertEqual(len(result["manifest_fingerprint_sha256"]), 64)
        self.assertEqual(len(result["feature_order_fingerprint_sha256"]), 64)

    def test_same_manifest_is_idempotent_but_mutation_is_rejected(self):
        first = self.registry.freeze(self._payload())
        second = self.registry.freeze(self._payload())
        self.assertFalse(first["idempotent"])
        self.assertTrue(second["idempotent"])
        changed = self._payload()
        changed["target_model_schema_label"] = "v16-mutated"
        with self.assertRaises(ProxyHypothesisValidationError):
            self.registry.freeze(changed)

    def test_feature_overlap_with_base_is_rejected(self):
        payload = self._payload()
        payload["base_feature_names"] = ["genres_combined", "source_work_count"]
        with self.assertRaises(ProxyHypothesisValidationError):
            self.registry.freeze(payload)

    def test_target_schema_must_be_newer_than_base(self):
        payload = self._payload()
        payload["target_model_schema_version"] = 15
        payload["target_model_schema_label"] = "v15-new"
        with self.assertRaises(ProxyHypothesisValidationError):
            self.registry.freeze(payload)

    def test_rejected_schema_cannot_be_promoted(self):
        self.schemas.conn.execute(
            "UPDATE proxy_candidate_schemas SET status='combined_rejected' WHERE schema_id='schema-p6-1'"
        )
        with self.assertRaises(ProxyHypothesisValidationError):
            self.registry.freeze(self._payload())

    def test_list_and_get_preserve_fingerprint(self):
        created = self.registry.freeze(self._payload())
        fetched = self.registry.get("promotion-1")
        self.assertEqual(
            fetched["manifest_fingerprint_sha256"],
            created["manifest_fingerprint_sha256"],
        )
        rows = self.registry.list_manifests()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["promotion_id"], "promotion-1")


if __name__ == "__main__":
    unittest.main()
