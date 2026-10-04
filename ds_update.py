from __future__ import annotations

import os
from pathlib import Path

import duckdb

from settings import config
from src.create_db import (
    create_derived_tables,
    create_duckdb_table_direct,
    create_indexes,
)
from src.data_freshness import build_freshness_report, write_freshness_manifest
from src.data_loader import download_imdb_dataset
from src.database import cleanup_temp
from src.logger import setup_logger
from src.rating_history import RatingHistoryStore


logger = setup_logger(__name__)

DATASETS = [
    "title.basics",
    "title.ratings",
    "title.principals",
    "title.crew",
    "name.basics",
]


def _validate_database(path: Path) -> None:
    conn = duckdb.connect(str(path), read_only=True)
    try:
        required = {
            "title_basics": 1,
            "title_ratings": 1,
            "title_principals": 1,
            "title_crew": 1,
            "title_writers": 1,
            "name_basics": 1,
        }
        for table_name, minimum in required.items():
            count = conn.execute(
                f"SELECT COUNT(*) FROM {table_name}"
            ).fetchone()[0]
            if count < minimum:
                raise RuntimeError(
                    f"Проверка новой IMDb БД не пройдена: "
                    f"{table_name} содержит {count} строк"
                )
            logger.info(f"Проверка {table_name}: {count} строк")
    finally:
        conn.close()


def _freshness_report(target: Path) -> dict:
    data_dir = Path(config.ABSPATH) / "data" / "imdb"
    return build_freshness_report(target, data_dir=data_dir)


def _freshness_manifest_path() -> Path:
    override = str(os.getenv("VANGA_FRESHNESS_MANIFEST_PATH") or "").strip()
    if override:
        return Path(override)
    return Path(config.ABSPATH) / "data" / "imdb" / "freshness-manifest.json"


def _write_freshness_manifest(target: Path) -> dict:
    report = _freshness_report(target)
    manifest = write_freshness_manifest(
        report,
        _freshness_manifest_path(),
    )
    if report.get("ready_for_full_training"):
        logger.info(
            "IMDb Data Freshness: готово к full training; stable_history_through=%s; fingerprint=%s",
            report.get("stable_history_through_year"),
            report.get("logical_fingerprint_sha256"),
        )
    else:
        logger.warning(
            "IMDb Data Freshness: full training заблокирован: %s",
            "; ".join(report.get("blocking_reasons") or ["неизвестная причина"]),
        )
    logger.info("IMDb freshness manifest записан: %s", manifest)
    return report


def _database_is_older_than_downloaded_datasets(target: Path) -> bool:
    """Проверяет repairable stale-state после неудачной предыдущей materialization.

    HTTP metadata/archive могут уже быть опубликованы локально, хотя rebuild
    DuckDB завершился ошибкой. В таком случае следующий download честно вернёт
    ``changed=False``. Сама БД при этом старше скачанных архивов — это достаточная
    причина повторить materialization независимо от результата текущей загрузки.
    """
    report = _freshness_report(target)
    reasons = set(report.get("blocking_reasons") or [])
    return "database_older_than_downloaded_datasets" in reasons


def _capture_rating_history(target: Path, freshness_report: dict) -> None:
    """Best-effort P7 capture: ошибка истории не ломает публикацию свежей IMDb БД."""
    if str(os.getenv("VANGA_SKIP_RATING_HISTORY") or "").strip().casefold() in {
        "1",
        "true",
        "yes",
        "on",
    }:
        logger.info(
            "IMDb Rating History: snapshot пропущен для staging runtime "
            "(VANGA_SKIP_RATING_HISTORY=1)"
        )
        return

    try:
        with RatingHistoryStore() as history:
            result = history.capture_daily(
                target,
                source_fingerprint_sha256=freshness_report.get(
                    "logical_fingerprint_sha256"
                ),
            )
        if result.get("skipped_no_watchlist"):
            logger.info(
                "IMDb Rating History: snapshot пропущен — watchlist пока пуст"
            )
        elif result.get("idempotent"):
            logger.info(
                "IMDb Rating History: snapshot за %s уже существует; captured=%s",
                result.get("snapshot_day"),
                result.get("captured_count"),
            )
        else:
            logger.info(
                "IMDb Rating History: snapshot %s записан; tracked=%s; captured=%s; missing=%s",
                result.get("snapshot_day"),
                result.get("tracked_count"),
                result.get("captured_count"),
                result.get("missing_count"),
            )
    except Exception as exc:
        # История рейтинга — накопительный слой. Она не должна откатывать уже
        # успешно собранную и проверенную IMDb БД; проблема остаётся видна в логах.
        logger.exception("IMDb Rating History: snapshot не записан: %s", exc)


def _build_staged_database(target: Path) -> None:
    staged = target.with_name(target.name + ".next")
    staged_wal = Path(str(staged) + ".wal")
    staged.unlink(missing_ok=True)
    staged_wal.unlink(missing_ok=True)

    original_path = config.IMDB_DB_PATH
    config.IMDB_DB_PATH = str(staged)
    try:
        # Порядок важен: следующие таблицы ссылаются на предыдущие.
        create_duckdb_table_direct("title.basics")
        create_duckdb_table_direct("title.ratings")
        create_duckdb_table_direct("title.principals")
        create_duckdb_table_direct("title.crew")
        create_derived_tables()
        create_duckdb_table_direct("name.basics")
        create_indexes()

        _validate_database(staged)

        target.parent.mkdir(parents=True, exist_ok=True)
        os.replace(staged, target)
        logger.info(f"Новая IMDb БД атомарно опубликована: {target}")
    except Exception:
        staged.unlink(missing_ok=True)
        staged_wal.unlink(missing_ok=True)
        raise
    finally:
        config.IMDB_DB_PATH = original_path


def main() -> int:
    changed = False

    for dataset in DATASETS:
        if download_imdb_dataset(dataset):
            changed = True

    target = Path(config.IMDB_DB_PATH)
    if target.exists() and not changed:
        try:
            _validate_database(target)
        except Exception as exc:
            logger.warning(
                "IMDb datasets не изменились, но локальная схема устарела "
                "или повреждена (%s) — пересобираем БД",
                exc,
            )
        else:
            if _database_is_older_than_downloaded_datasets(target):
                logger.warning(
                    "IMDb datasets не изменились на этом запуске, но БД старше "
                    "уже скачанных архивов — повторяем прерванную materialization"
                )
            else:
                logger.info(
                    "IMDb datasets не изменились, схема и materialization актуальны — "
                    "пересборка БД не требуется"
                )
                report = _write_freshness_manifest(target)
                _capture_rating_history(target, report)
                return 0

    logger.info("Собираем новую IMDb БД в staging-файле")
    _build_staged_database(target)
    cleanup_temp()
    report = _write_freshness_manifest(target)
    _capture_rating_history(target, report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
