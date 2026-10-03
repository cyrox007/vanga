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

    def test_script_keeps_heavy_retrain_opt_in(self):
        source = SCRIPT.read_text(encoding="utf-8")

        self.assertIn("--full-retrain", source)
        self.assertIn("FULL_RETRAIN=0", source)
        self.assertIn("VANGA_RETRAIN_SERVICE", source)
        self.assertIn("systemctl start", source)
        self.assertNotIn(
            'run_vanga_env "${VANGA_DIR}/.venv/bin/python" "${VANGA_DIR}/traning.py"\n',
            source,
        )

    def test_script_uses_fast_forward_only_git_update(self):
        source = SCRIPT.read_text(encoding="utf-8")

        self.assertIn('git -C "${VANGA_DIR}" fetch --prune origin', source)
        self.assertIn('git -C "${VANGA_DIR}" merge --ff-only', source)
        self.assertIn("незакоммиченные изменения", source)

    def test_script_restarts_inference_after_database_swap(self):
        source = SCRIPT.read_text(encoding="utf-8")

        db_update = source.index("ds_update.py")
        restart = source.index('systemctl restart "${VANGA_SERVICE}"', db_update)
        smoke = source.index("--smoke", restart)

        self.assertLess(db_update, restart)
        self.assertLess(restart, smoke)

    def test_health_check_is_loopback_by_default(self):
        source = SCRIPT.read_text(encoding="utf-8")

        self.assertIn(
            "http://127.0.0.1:9100/health",
            source,
        )
        self.assertIn("Порт 9100 не должен публиковаться наружу", source)

    def test_full_retrain_refuses_direct_unsafe_fallback(self):
        source = SCRIPT.read_text(encoding="utf-8")

        self.assertIn(
            "Полный retrain напрямую не запускаю",
            source,
        )
        self.assertIn(
            "нужен systemd memory/cpu guard",
            source,
        )


if __name__ == "__main__":
    unittest.main()
