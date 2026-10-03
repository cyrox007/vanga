from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from src.proxy_ablation_gate import ProxyAblationResultGate
from src.proxy_hypotheses import ProxyHypothesisStore, ProxyHypothesisValidationError


class LegacyProxyAblationResultGateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = ProxyHypothesisStore(Path(self.tmp.name) / "proxy.duckdb")
        self.compat = ProxyAblationResultGate(self.store)

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_legacy_evaluate_is_fail_fast(self):
        with self.assertRaises(ProxyHypothesisValidationError) as context:
            self.compat.evaluate({"anything": "ignored"})
        self.assertIn("ProxyAblationPipeline", str(context.exception))
        self.assertIn("temporal audit", str(context.exception))

    def test_compatibility_schema_and_history_remain_readable(self):
        now = datetime.now(timezone.utc)
        self.store.conn.execute(
            """
            INSERT INTO proxy_ablation_results(
                result_id, hypothesis_id, plan_fingerprint_sha256,
                result_fingerprint_sha256, dataset_fingerprint_sha256,
                holdout_policy, holdout_start_year, holdout_end_year,
                holdout_row_count, baseline_mae, candidate_mae, mae_delta,
                passed, verdict, runner_id, runner_version,
                executed_at, recorded_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                "result-1",
                "hypothesis-1",
                "a" * 64,
                "b" * 64,
                "c" * 64,
                "stable_temporal_last_two_years",
                2024,
                2025,
                100,
                1.0,
                0.95,
                -0.05,
                True,
                "accepted_ablation",
                "canonical-pipeline",
                "1",
                now,
                now,
            ],
        )

        rows = self.compat.result_history("hypothesis-1")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["result_id"], "result-1")
        self.assertTrue(rows[0]["passed"])
        self.assertAlmostEqual(rows[0]["mae_delta"], -0.05)

    def test_empty_hypothesis_id_is_rejected(self):
        with self.assertRaises(ProxyHypothesisValidationError):
            self.compat.result_history("   ")


if __name__ == "__main__":
    unittest.main()
