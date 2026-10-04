from __future__ import annotations

import os
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

BROKEN_IN_AUDIT = (
    "audience_signals.py",
    "expert_agreement_transfer.py",
    "expert_blind_validation.py",
    "expert_consensus.py",
    "future_prediction_payload.py",
    "future_release_import.py",
    "future_releases.py",
    "proxy_ablation.py",
    "proxy_ablation_gate.py",
    "proxy_ablation_pipeline.py",
    "proxy_candidate_schema.py",
    "proxy_candidate_schema_gate.py",
    "proxy_hypotheses.py",
    "proxy_materialize.py",
    "proxy_materialize_unified.py",
    "proxy_promotion_manifest.py",
    "rating_dataset.py",
    "rating_history.py",
    "rating_milestones.py",
    "wikidata_future_releases.py",
)


class ScriptEntrypointTests(unittest.TestCase):
    def test_documented_script_invocation_works_without_pythonpath(self) -> None:
        env = os.environ.copy()
        env.pop("PYTHONPATH", None)
        failures: list[str] = []
        for name in BROKEN_IN_AUDIT:
            completed = subprocess.run(
                [sys.executable, str(ROOT / "scripts" / name), "--help"],
                cwd=ROOT,
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                timeout=30,
                check=False,
            )
            if completed.returncode != 0:
                failures.append(
                    f"{name}: exit={completed.returncode}\n{completed.stdout[-2000:]}"
                )
        self.assertFalse(failures, "\n\n".join(failures))

    def test_predict_uses_creative_inference_entrypoint(self) -> None:
        source = (ROOT / "predict.py").read_text(encoding="utf-8")
        self.assertIn("from src.creative_kinovanga import KinoVanga", source)
        self.assertNotIn("from src.kinovanga import KinoVanga", source)
        self.assertIn('if __name__ == "__main__":', source)


if __name__ == "__main__":
    unittest.main()
