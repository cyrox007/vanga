from __future__ import annotations

from pathlib import Path
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "deploy" / "update-vanga.sh"


class SafeServerUpdateTests(unittest.TestCase):
    def test_script_has_valid_bash_syntax(self):
        result = subprocess.run(
            ["bash", "-n", str(SCRIPT)],
            check=False,
            capture_output=True,
            text=True,
        )

        self.assertEqual(result.returncode, 0, result.stderr)

    def test_script_keeps_heavy_retrain_opt_in_and_resource_limited(self):
        source = SCRIPT.read_text(encoding="utf-8")

        self.assertIn("--full-retrain", source)
        self.assertIn("FULL_RETRAIN=0", source)
        self.assertIn("vanga-full-update-", source)
        self.assertIn("run_limited_training", source)
        self.assertIn("systemd-run", source)
        self.assertIn("--property=CPUQuota=50%", source)
        self.assertIn("--property=MemoryHigh=900M", source)
        self.assertIn("--property=MemoryMax=1100M", source)
        self.assertIn("--property=MemorySwapMax=2G", source)

    def test_script_uses_fast_forward_only_git_update(self):
        source = SCRIPT.read_text(encoding="utf-8")

        self.assertIn('git -C "${VANGA_DIR}" fetch --prune origin', source)
        self.assertIn('git -C "${VANGA_DIR}" merge --ff-only', source)
        self.assertIn("незакоммиченные изменения", source)
        self.assertIn('PREVIOUS_COMMIT="$(run_vanga git -C', source)

    def test_candidate_smoke_happens_before_runtime_switch_and_restart(self):
        source = SCRIPT.read_text(encoding="utf-8")

        db_stage = source.index("VANGA_SKIP_RATING_HISTORY=1")
        smoke = source.index('"vanga-smoke-update-${BASHPID}"')
        runtime_switch = source.index("RUNTIME_SWITCHED=1", smoke)
        restart = source.index('systemctl restart "${VANGA_SERVICE}"', runtime_switch)

        self.assertLess(db_stage, smoke)
        self.assertLess(smoke, runtime_switch)
        self.assertLess(runtime_switch, restart)

    def test_active_venv_is_not_mutated_by_pip_before_smoke(self):
        source = SCRIPT.read_text(encoding="utf-8")

        self.assertIn('VENV_CANDIDATE="${VANGA_DIR}/.venv.candidate-', source)
        self.assertIn('"${VENV_CANDIDATE}/bin/pip" install', source)
        self.assertNotIn('"${ACTIVE_VENV}/bin/pip" install', source)
        self.assertIn('mv -- "${ACTIVE_VENV}" "${VENV_ROLLBACK}"', source)
        self.assertIn('mv -- "${VENV_CANDIDATE}" "${ACTIVE_VENV}"', source)

    def test_rollback_covers_code_venv_database_and_model_pointer(self):
        source = SCRIPT.read_text(encoding="utf-8")

        self.assertIn("rollback_update()", source)
        self.assertIn('reset --hard "${PREVIOUS_COMMIT}"', source)
        self.assertIn("VENV_ROLLBACK", source)
        self.assertIn("DB_ROLLBACK", source)
        self.assertIn("POINTER_ROLLBACK", source)
        self.assertIn('cp -a -- "${POINTER_ROLLBACK}" "${POINTER_PATH}"', source)
        self.assertIn("trap on_exit EXIT", source)

    def test_candidate_database_is_not_published_before_smoke(self):
        source = SCRIPT.read_text(encoding="utf-8")

        candidate_build = source.index('VANGA_IMDB_DB="${DB_CANDIDATE}"')
        smoke = source.index('"vanga-smoke-update-${BASHPID}"')
        publish = source.index('mv -f -- "${DB_CANDIDATE}" "${ACTIVE_DB}"')

        self.assertLess(candidate_build, smoke)
        self.assertLess(smoke, publish)
        self.assertIn('ln -- "${ACTIVE_DB}" "${DB_ROLLBACK}"', source)

    def test_health_check_is_loopback_by_default(self):
        source = SCRIPT.read_text(encoding="utf-8")

        self.assertIn(
            "http://127.0.0.1:9100/health",
            source,
        )
        self.assertIn("Порт 9100 не должен публиковаться наружу", source)


if __name__ == "__main__":
    unittest.main()
