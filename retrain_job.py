from __future__ import annotations

import argparse
import fcntl
import os
import subprocess
import sys
from pathlib import Path

from settings import config


LOCK_HELD_ENV = "VANGA_ORCHESTRATION_LOCK_HELD"
LOCK_PATH_ENV = "VANGA_ORCHESTRATION_LOCK"


def _lock_path() -> Path:
    raw = os.getenv(LOCK_PATH_ENV)
    if raw:
        return Path(raw)
    return Path(config.ABSPATH) / "data" / "runtime" / "orchestration.lock"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Согласованный периодический update + retrain Vanga"
    )
    parser.add_argument(
        "--wait-lock",
        action="store_true",
        help="ждать освобождения orchestration lock вместо немедленного отказа",
    )
    return parser


def _run(script: str, *, env: dict[str, str]) -> None:
    command = [sys.executable, str(Path(config.ABSPATH) / script)]
    print(f"[Vanga retrain] Запуск: {' '.join(command)}", flush=True)
    subprocess.run(
        command,
        cwd=config.ABSPATH,
        env=env,
        check=True,
    )


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    lock_path = _lock_path()
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    with lock_path.open("a+", encoding="utf-8") as handle:
        flags = fcntl.LOCK_EX
        if not args.wait_lock:
            flags |= fcntl.LOCK_NB
        try:
            fcntl.flock(handle.fileno(), flags)
        except BlockingIOError:
            print(
                f"[Vanga retrain] ОШИБКА: уже выполняется updater/retrain; lock={lock_path}",
                file=sys.stderr,
                flush=True,
            )
            return 75

        child_env = dict(os.environ)
        child_env[LOCK_HELD_ENV] = "1"
        child_env[LOCK_PATH_ENV] = str(lock_path)
        print(
            f"[Vanga retrain] Orchestration lock получен: {lock_path}",
            flush=True,
        )
        try:
            _run("ds_update.py", env=child_env)
            _run("traning.py", env=child_env)
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            print("[Vanga retrain] Orchestration lock освобождён", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
