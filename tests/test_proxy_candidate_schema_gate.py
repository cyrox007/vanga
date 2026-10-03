from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from src.proxy_ablation import TemporalProxyAvailabilityAuditor
from src.proxy_candidate_schema import ProxyCandidateSchemaRegistry
from src.proxy_candidate_schema_gate import ProxyCandidateSchemaGate
from src.proxy_candidate_schema_plan import ProxyCandidateSchemaPlanBuilder
from src.proxy_hypotheses import ProxyHypothesisStore, ProxyHypothesisValidationError


def _canonical(payload):
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _fingerprint(payload):
    return hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()


class ProxyCandidateSchemaGateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = ProxyHypothesisStore(Path(self.tmp.name) / "proxy.duckdb")
        self.registry = ProxyCandidateSchemaRegistry(self.store)
        self._insert_frozen_schema()
        self.builder = ProxyCandidateSchemaPlanBuilder(self.registry)
        self.gate = ProxyCandidateSchemaGate(self.registry)

    def tearDown(self):
        self.registry.close()
        self.store.close()
        self.tmp.cleanup()

    def _insert_frozen_schema(self):
        body = {
            "registry_version": 1,
            "schema_id": "schema-1",
            "schema_label": "p6-candidate-1",
            "schema_version": 1,
            "status": "frozen_research_candidate",
            "base_schema": "v15",
            "dataset_fingerprint_sha256": "a" * 64,
            "holdout": {
                "policy": "stable_temporal_last_two_years",
                "start_year": 2024,
                "end_year": 2025,
                "row_count": 200,
            },
            "hypothesis_ids": ["h1"],
            "accepted_results": [],
            "feature_contracts": [
                {
                    "feature_name": "planned_runtime_minutes",
                    "source_layer": "source_context",
                    "temporal_contract": "planned_before_release",
                    "proxy_roles": ["primary"],
                    "supporting_hypothesis_ids": ["h1"],
                    "acceptance_result_fingerprints": ["1" * 64],
                    "coverage_for_features": [],
                }
            ],
            "model_feature_names": ["planned_runtime_minutes"],
            "required_source_layers": ["source_context"],
            "materializer_contract": "proxy_source_materializers_v1",
            "combined_ablation_required": False,
            "production_quality_gate_required": True,
            "automatic_catboost_inclusion": False,
            "production_publication_allowed": False,
        }
        fp = _fingerprint(body)
        now = datetime.now(timezone.utc)
        self.registry.conn.execute(
            """
            INSERT INTO proxy_candidate_schemas(
                schema_id, schema_label, schema_version, base_schema,
                dataset_fingerprint_sha256, holdout_json, hypothesis_ids_json,
                acceptance_results_json, feature_contracts_json, schema_json,
                schema_fingerprint_sha256, status, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                "schema-1",
                "p6-candidate-1",
                1,
                "v15",
                "a" * 64,
                _canonical(body["holdout"]),
                _canonical(body["hypothesis_ids"]),
                _canonical([]),
                _canonical(body["feature_contracts"]),
                _canonical(body),
                fp,
                "frozen_research_candidate",
                now,
            ],
        )

    def _plan_and_audit(self, *, allow_regression=False):
        plan = self.builder.build(
            "schema-1",
            require_improvement=not allow_regression,
            max_mae_regression=0.02 if allow_regression else 0.0,
        )
        materialization = {
            "version": 1,
            "targets": [
                {
                    "target_id": "tt001",
                    "target_year": 2025,
                    "cutoff_at": "2025-02-01T00:00:00+00:00",
                    "release_at": "2025-06-01T00:00:00+00:00",
                }
            ],
            "observations": [
                {
                    "target_id": "tt001",
                    "feature_name": "planned_runtime_minutes",
                    "source_layer": "source_context",
                    "temporal_contract": "planned_before_release",
                    "available": True,
                    "value": 120.0,
                    "provenance_id": "source-context:runtime",
                    "source_timestamp": "2025-01-15T00:00:00+00:00",
                }
            ],
        }
        audit = TemporalProxyAvailabilityAuditor.audit(plan, materialization)
        self.assertTrue(audit["passed"])
        return plan, audit

    @staticmethod
    def _model_results(plan, audit, *, candidate_mae=0.95, dataset_fp="a" * 64):
        common = {
            "dataset_fingerprint_sha256": dataset_fp,
            "train_year_from": 1910,
            "train_year_to": 2023,
            "test_year_from": 2024,
            "test_year_to": 2025,
            "train_rows": 1000,
            "test_rows": 200,
            "total_rows": 1200,
        }
        baseline = {
            **common,
            "schema_label": "v15",
            "feature_names": ["startYear", "genres_combined"],
            "test_mae": 1.0,
            "test_rmse": 1.3,
            "test_r2": 0.2,
        }
        candidate = {
            **common,
            "label": plan["ablation"]["candidate_label"],
            "plan_fingerprint_sha256": plan["plan_fingerprint_sha256"],
            "proxy_audit_fingerprint_sha256": audit["audit_fingerprint_sha256"],
            "feature_names": ["startYear", "genres_combined", "planned_runtime_minutes"],
            "test_mae": candidate_mae,
            "test_rmse": 1.2,
            "test_r2": 0.25,
        }
        return baseline, candidate

    def test_passed_combined_gate_transitions_only_schema(self):
        plan, audit = self._plan_and_audit()
        baseline, candidate = self._model_results(plan, audit, candidate_mae=0.95)
        result = self.gate.evaluate_and_record(
            schema_id="schema-1",
            plan=plan,
            audit_report=audit,
            baseline=baseline,
            candidate=candidate,
            result_id="combined-1",
        )
        status = self.registry.conn.execute(
            "SELECT status FROM proxy_candidate_schemas WHERE schema_id='schema-1'"
        ).fetchone()[0]
        self.assertEqual(status, "combined_validated")
        self.assertTrue(result["passed"])
        self.assertEqual(result["verdict"], "combined_validated")
        self.assertFalse(result["production_publication_allowed"])
        saved = self.gate.result("schema-1")
        self.assertEqual(saved["gate_fingerprint_sha256"], result["gate_fingerprint_sha256"])
        self.assertEqual(saved["lifecycle_status"], "combined_validated")

    def test_rejected_combined_gate_marks_schema_rejected(self):
        plan, audit = self._plan_and_audit()
        baseline, candidate = self._model_results(plan, audit, candidate_mae=1.01)
        result = self.gate.evaluate_and_record(
            schema_id="schema-1",
            plan=plan,
            audit_report=audit,
            baseline=baseline,
            candidate=candidate,
        )
        status = self.registry.conn.execute(
            "SELECT status FROM proxy_candidate_schemas WHERE schema_id='schema-1'"
        ).fetchone()[0]
        self.assertEqual(status, "combined_rejected")
        self.assertFalse(result["passed"])

    def test_same_gate_is_idempotent_after_final_status(self):
        plan, audit = self._plan_and_audit()
        baseline, candidate = self._model_results(plan, audit)
        first = self.gate.evaluate_and_record(
            schema_id="schema-1",
            plan=plan,
            audit_report=audit,
            baseline=baseline,
            candidate=candidate,
            result_id="stable-result",
        )
        second = self.gate.evaluate_and_record(
            schema_id="schema-1",
            plan=plan,
            audit_report=audit,
            baseline=baseline,
            candidate=candidate,
            result_id="ignored",
        )
        self.assertFalse(first["idempotent"])
        self.assertTrue(second["idempotent"])
        self.assertEqual(second["result_id"], "stable-result")
        count = self.registry.conn.execute(
            "SELECT COUNT(*) FROM proxy_candidate_schema_results WHERE schema_id='schema-1'"
        ).fetchone()[0]
        self.assertEqual(count, 1)

    def test_new_gate_after_final_status_requires_new_schema_version(self):
        plan, audit = self._plan_and_audit()
        baseline, candidate = self._model_results(plan, audit)
        self.gate.evaluate_and_record(
            schema_id="schema-1", plan=plan, audit_report=audit,
            baseline=baseline, candidate=candidate,
        )
        second_plan, second_audit = self._plan_and_audit(allow_regression=True)
        second_baseline, second_candidate = self._model_results(
            second_plan, second_audit, candidate_mae=1.005
        )
        with self.assertRaises(ProxyHypothesisValidationError):
            self.gate.evaluate_and_record(
                schema_id="schema-1",
                plan=second_plan,
                audit_report=second_audit,
                baseline=second_baseline,
                candidate=second_candidate,
            )

    def test_schema_dataset_binding_is_stricter_than_generic_gate(self):
        plan, audit = self._plan_and_audit()
        baseline, candidate = self._model_results(
            plan, audit, dataset_fp="b" * 64
        )
        # Generic gate видит одинаковый dataset у baseline/candidate, но schema
        # зафиксирована на dataset=a... и должна отклонить такой experiment.
        with self.assertRaises(ProxyHypothesisValidationError):
            self.gate.evaluate_and_record(
                schema_id="schema-1",
                plan=plan,
                audit_report=audit,
                baseline=baseline,
                candidate=candidate,
            )
        status = self.registry.conn.execute(
            "SELECT status FROM proxy_candidate_schemas WHERE schema_id='schema-1'"
        ).fetchone()[0]
        self.assertEqual(status, "frozen_research_candidate")

    def test_schema_fingerprint_mismatch_is_rejected(self):
        plan, audit = self._plan_and_audit()
        plan["candidate_schema_fingerprint_sha256"] = "f" * 64
        body = dict(plan)
        body.pop("plan_fingerprint_sha256")
        plan["plan_fingerprint_sha256"] = _fingerprint(body)
        baseline, candidate = self._model_results(plan, audit)
        candidate["plan_fingerprint_sha256"] = plan["plan_fingerprint_sha256"]
        with self.assertRaises(ProxyHypothesisValidationError):
            self.gate.evaluate_and_record(
                schema_id="schema-1",
                plan=plan,
                audit_report=audit,
                baseline=baseline,
                candidate=candidate,
            )


if __name__ == "__main__":
    unittest.main()
