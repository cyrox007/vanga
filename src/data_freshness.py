from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any

import duckdb

from settings import config


REQUIRED_TABLES = (
    "title_basics",
    "title_ratings",
    "title_principals",
    "title_crew",
    "title_writers",
    "name_basics",
)

REQUIRED_DATASETS = (
    "title.basics",
    "title.ratings",
    "title.principals",
    "title.crew",
    "name.basics",
)


@dataclass(frozen=True)
class FreshnessPolicy:
    source_max_age_days: int = 14
    high_vote_threshold: int = 1000
    recent_year_span: int = 3


def _utc(value: datetime | None = None) -> datetime:
    current = value or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc)


def _ratio(numerator: int, denominator: int) -> float:
    return float(numerator) / float(denominator) if denominator else 0.0


def _parse_http_datetime(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = parsedate_to_datetime(text)
    except (TypeError, ValueError, OverflowError):
        return None
    return _utc(parsed)


def _table_names(conn: duckdb.DuckDBPyConnection) -> set[str]:
    return {
        str(row[0])
        for row in conn.execute("SHOW TABLES").fetchall()
    }


def _table_counts(conn: duckdb.DuckDBPyConnection, tables: set[str]) -> dict[str, int]:
    result: dict[str, int] = {}
    for table in REQUIRED_TABLES:
        if table in tables:
            result[table] = int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
    return result


def _year_coverage(
    conn: duckdb.DuckDBPyConnection,
    years: list[int],
    *,
    high_vote_threshold: int,
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for year in years:
        movies = int(
            conn.execute(
                "SELECT COUNT(*) FROM title_basics WHERE startYear = ?",
                [year],
            ).fetchone()[0]
        )
        rated = int(
            conn.execute(
                """
                SELECT COUNT(*)
                FROM title_basics b
                JOIN title_ratings r USING (tconst)
                WHERE b.startYear = ?
                """,
                [year],
            ).fetchone()[0]
        )
        high_vote = int(
            conn.execute(
                """
                SELECT COUNT(*)
                FROM title_basics b
                JOIN title_ratings r USING (tconst)
                WHERE b.startYear = ? AND r.numVotes >= ?
                """,
                [year, high_vote_threshold],
            ).fetchone()[0]
        )
        director = int(
            conn.execute(
                """
                SELECT COUNT(DISTINCT b.tconst)
                FROM title_basics b
                JOIN title_principals p USING (tconst)
                WHERE b.startYear = ? AND p.category = 'director'
                """,
                [year],
            ).fetchone()[0]
        )
        writer = int(
            conn.execute(
                """
                SELECT COUNT(DISTINCT b.tconst)
                FROM title_basics b
                JOIN title_writers w USING (tconst)
                WHERE b.startYear = ?
                """,
                [year],
            ).fetchone()[0]
        )
        cast = int(
            conn.execute(
                """
                SELECT COUNT(DISTINCT b.tconst)
                FROM title_basics b
                JOIN title_principals p USING (tconst)
                WHERE b.startYear = ? AND p.category IN ('actor', 'actress')
                """,
                [year],
            ).fetchone()[0]
        )
        result[str(year)] = {
            "movies": movies,
            "rated_movies": rated,
            "rating_coverage": _ratio(rated, movies),
            "high_vote_movies": high_vote,
            "high_vote_threshold": high_vote_threshold,
            "director_movies": director,
            "director_coverage": _ratio(director, movies),
            "writer_movies": writer,
            "writer_coverage": _ratio(writer, movies),
            "cast_movies": cast,
            "cast_coverage": _ratio(cast, movies),
        }
    return result


def _dataset_manifest(data_dir: Path, as_of: datetime) -> tuple[dict[str, Any], list[str]]:
    datasets: dict[str, Any] = {}
    problems: list[str] = []
    for name in REQUIRED_DATASETS:
        archive = data_dir / f"{name}.tsv.gz"
        meta = archive.with_suffix(archive.suffix + ".meta.json")
        metadata: dict[str, Any] = {}
        if meta.exists():
            try:
                raw = json.loads(meta.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    metadata = raw
            except (OSError, ValueError, json.JSONDecodeError):
                problems.append(f"metadata_invalid:{name}")
        else:
            problems.append(f"metadata_missing:{name}")

        if not archive.exists():
            problems.append(f"dataset_missing:{name}")
            datasets[name] = {
                "exists": False,
                "metadata": metadata,
            }
            continue

        source_time = _parse_http_datetime(metadata.get("last_modified"))
        source_time_kind = "http_last_modified" if source_time is not None else "file_mtime"
        if source_time is None:
            source_time = datetime.fromtimestamp(archive.stat().st_mtime, tz=timezone.utc)
        age_days = max(0.0, (as_of - source_time).total_seconds() / 86400.0)
        datasets[name] = {
            "exists": True,
            "size_bytes": archive.stat().st_size,
            "file_mtime": datetime.fromtimestamp(
                archive.stat().st_mtime, tz=timezone.utc
            ).isoformat(),
            "source_time": source_time.isoformat(),
            "source_time_kind": source_time_kind,
            "source_age_days": age_days,
            "etag": metadata.get("etag"),
            "last_modified": metadata.get("last_modified"),
            "content_length": metadata.get("content_length"),
            "url": metadata.get("url"),
        }
    return datasets, problems


def _logical_fingerprint(payload: dict[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def build_freshness_report(
    db_path: str | Path | None = None,
    *,
    data_dir: str | Path | None = None,
    as_of: datetime | None = None,
    policy: FreshnessPolicy | None = None,
) -> dict[str, Any]:
    current = _utc(as_of)
    policy = policy or FreshnessPolicy()
    db = Path(db_path or config.IMDB_DB_PATH)
    imdb_dir = Path(data_dir or (Path(config.ABSPATH) / "data" / "imdb"))
    blocking: list[str] = []
    warnings: list[str] = []

    if not db.exists():
        return {
            "ok": False,
            "ready_for_full_training": False,
            "as_of": current.isoformat(),
            "database": str(db),
            "blocking_reasons": ["database_missing"],
            "warnings": [],
        }

    conn = duckdb.connect(str(db), read_only=True)
    try:
        tables = _table_names(conn)
        missing_tables = sorted(set(REQUIRED_TABLES) - tables)
        if missing_tables:
            blocking.extend(f"table_missing:{name}" for name in missing_tables)
            return {
                "ok": False,
                "ready_for_full_training": False,
                "as_of": current.isoformat(),
                "database": str(db),
                "missing_tables": missing_tables,
                "blocking_reasons": blocking,
                "warnings": warnings,
            }

        table_counts = _table_counts(conn, tables)
        min_year, max_year = conn.execute(
            "SELECT MIN(startYear), MAX(startYear) FROM title_basics"
        ).fetchone()
        min_year = int(min_year) if min_year is not None else None
        max_year = int(max_year) if max_year is not None else None
        recent_years = list(
            range(current.year - policy.recent_year_span + 1, current.year + 1)
        )
        coverage = _year_coverage(
            conn,
            recent_years,
            high_vote_threshold=policy.high_vote_threshold,
        )
    finally:
        conn.close()

    datasets, manifest_problems = _dataset_manifest(imdb_dir, current)
    blocking.extend(manifest_problems)

    if max_year is None:
        blocking.append("database_has_no_years")
    elif max_year < current.year:
        blocking.append(
            f"latest_title_year_too_old:{max_year}<current_year:{current.year}"
        )

    for year in recent_years:
        if coverage[str(year)]["movies"] == 0:
            blocking.append(f"recent_year_missing:{year}")

    previous_year = current.year - 1
    previous = coverage.get(str(previous_year), {})
    if previous and previous.get("rated_movies", 0) == 0:
        blocking.append(f"previous_year_has_no_ratings:{previous_year}")
    if previous and previous.get("director_movies", 0) == 0:
        blocking.append(f"previous_year_has_no_directors:{previous_year}")
    if previous and previous.get("writer_movies", 0) == 0:
        blocking.append(f"previous_year_has_no_writers:{previous_year}")
    if previous and previous.get("cast_movies", 0) == 0:
        blocking.append(f"previous_year_has_no_cast:{previous_year}")

    freshest_source_mtime: datetime | None = None
    for name, item in datasets.items():
        if not item.get("exists"):
            continue
        age = float(item.get("source_age_days", 1e9))
        if age > policy.source_max_age_days:
            blocking.append(
                f"dataset_stale:{name}:{age:.1f}d>{policy.source_max_age_days}d"
            )
        try:
            mtime = datetime.fromisoformat(str(item["file_mtime"]))
        except (KeyError, ValueError):
            continue
        freshest_source_mtime = (
            mtime
            if freshest_source_mtime is None
            else max(freshest_source_mtime, mtime)
        )

    db_mtime = datetime.fromtimestamp(db.stat().st_mtime, tz=timezone.utc)
    if freshest_source_mtime is not None and db_mtime < freshest_source_mtime:
        blocking.append("database_older_than_downloaded_datasets")

    current_stats = coverage.get(str(current.year), {})
    if current_stats.get("movies", 0) > 0:
        warnings.append(
            "current_year_is_provisional: IMDb core tables do not provide a reliable "
            "per-title release-age criterion; do not auto-promote current-year targets "
            "to stable training data from year alone"
        )

    source_signature = {
        name: {
            key: value
            for key, value in item.items()
            if key in (
                "exists",
                "size_bytes",
                "etag",
                "last_modified",
                "content_length",
                "source_time",
            )
        }
        for name, item in sorted(datasets.items())
    }
    logical_payload = {
        "table_counts": table_counts,
        "min_year": min_year,
        "max_year": max_year,
        "recent_year_coverage": coverage,
        "source_signature": source_signature,
    }
    fingerprint = _logical_fingerprint(logical_payload)

    ready = not blocking
    return {
        "ok": True,
        "ready_for_full_training": ready,
        "as_of": current.isoformat(),
        "database": str(db),
        "database_size_bytes": db.stat().st_size,
        "database_mtime": db_mtime.isoformat(),
        "data_dir": str(imdb_dir),
        "min_title_year": min_year,
        "max_title_year": max_year,
        "recent_years": recent_years,
        "recent_year_coverage": coverage,
        "table_counts": table_counts,
        "datasets": datasets,
        "policy": {
            "source_max_age_days": policy.source_max_age_days,
            "high_vote_threshold": policy.high_vote_threshold,
            "recent_year_span": policy.recent_year_span,
        },
        "stable_history_through_year": current.year - 1 if ready else None,
        "recommended_training_target_max_year": current.year - 1 if ready else None,
        "current_year_high_vote_candidate_count": int(
            current_stats.get("high_vote_movies", 0)
        ),
        "current_year_status": "provisional",
        "logical_fingerprint_sha256": fingerprint,
        "blocking_reasons": sorted(set(blocking)),
        "warnings": warnings,
    }


def write_freshness_manifest(report: dict[str, Any], path: str | Path) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_suffix(target.suffix + ".tmp")
    temp.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temp.replace(target)
    return target


def require_fresh_imdb_data(
    db_path: str | Path | None = None,
    *,
    data_dir: str | Path | None = None,
    as_of: datetime | None = None,
    policy: FreshnessPolicy | None = None,
) -> dict[str, Any]:
    report = build_freshness_report(
        db_path,
        data_dir=data_dir,
        as_of=as_of,
        policy=policy,
    )
    if not report.get("ready_for_full_training"):
        reasons = report.get("blocking_reasons") or ["unknown_freshness_failure"]
        raise RuntimeError(
            "IMDb Data Freshness guard заблокировал full training: "
            + "; ".join(str(item) for item in reasons)
        )
    return report
