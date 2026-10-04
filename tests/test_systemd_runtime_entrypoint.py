from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_vanga_systemd_uses_python_module_for_gunicorn() -> None:
    service = (ROOT / "deploy" / "systemd" / "vanga.service").read_text(
        encoding="utf-8"
    )
    assert ".venv/bin/python -m gunicorn" in service
    assert ".venv/bin/gunicorn " not in service
