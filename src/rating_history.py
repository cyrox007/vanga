from __future__ import annotations

import hashlib
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from uuid import uuid4

import duckdb

from settings import config


RATING_HISTORY_VERSION = 1


class RatingHistoryError(ValueError):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_datetime(value: Any, *, field_name: str) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        raw = str(value or "").strip().replace("Z", "+00:00")
        if not raw:
            raise RatingHistoryError(f"{field_name} обязателен")
        try:
            parsed = datetime.fromisoformat(raw)
        except ValueError as exc:
            raise RatingHistoryError(f"{field_name} должен быть ISO-8601 datetime") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _clean(value: Any, *, field_name: str, limit: int = 500, required: bool = False) -> str:
    text = " ".join(str(value or "").strip().split())
    if required and not text:
        raise RatingHistoryError(f"{field_name} не должен быть пустым")
    if len(text) > limit:
        raise RatingHistoryError(f"{field_name} длиннее допустимых {limit} символов")
    return text


def _imdb_id(value: Any) -> str:
    text = _clean(value, field_name="imdb_id", limit=32, required=True)
    if not text.startswith("tt") or not text[2:].isdigit():
        raise RatingHistoryError(f"Некорректный IMDb id: {text!r}")
    return text


def _sha256_or_none(value: Any) -> str | None:
    if value in {None, ""}:
        return None
    text = _clean(value, field_name="source_fingerprint_sha256", limit=64, required=True).lower()
    if len(text) != 64 or any(ch not in "0123456789abcdef" for ch in text):
        raise RatingHistoryError("source_fingerprint_sha256 должен быть SHA-256 hex")
    return text


def _chunked(values: list[str], size: int = 1000) -> Iterable[list[str]]:
    for offset in range(0, len(values), size):
        yield values[offset : offset + size]


class RatingHistoryStore:
    """Append-only point-in-time история IMDb rating для отслеживаемых фильмов.

    Официальный IMDb TSV содержит только текущее состояние. Этот store не пытается
    задним числом реконструировать прошлое: он начинает накапливать собственные
    наблюдения с точным ``observed_at`` и никогда не перезаписывает snapshot дня.
    """

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path or config.RATING_HISTORY_DB_PATH)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = duckdb.connect(str(self.path))
        self.conn.execute("SET threads = 1")
        self._ensure_schema()

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "RatingHistoryStore":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def _ensure_schema(self) -> None:
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS rating_watchlist (
                imdb_id VARCHAR PRIMARY KEY,
                active BOOLEAN NOT NULL,
                source VARCHAR NOT NULL,
                note VARCHAR,
                added_at TIMESTAMPTZ NOT NULL,
                updated_at TIMESTAMPTZ NOT NULL
            )
            """
        )
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS rating_snapshots (
                snapshot_id VARCHAR PRIMARY KEY,
                snapshot_day DATE NOT NULL UNIQUE,
                observed_at TIMESTAMPTZ NOT NULL,
                source_fingerprint_sha256 VARCHAR,
                tracked_count INTEGER NOT NULL,
                captured_count INTEGER NOT NULL,
                missing_count INTEGER NOT NULL,
                created_at TIMESTAMPTZ NOT NULL
            )
            """
        )
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS rating_points (
                snapshot_id VARCHAR NOT NULL,
                imdb_id VARCHAR NOT NULL,
                average_rating DOUBLE NOT NULL,
                num_votes BIGINT NOT NULL,
                PRIMARY KEY(snapshot_id, imdb_id)
            )
            """
        )
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_rating_points_imdb ON rating_points(imdb_id)"
        )
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_rating_snapshots_observed ON rating_snapshots(observed_at)"
        )

    def watch(
        self,
        imdb_ids: Iterable[str],
        *,
        source: str = "manual",
        note: str | None = None,
    ) -> dict[str, Any]:
        source = _clean(source, field_name="source", limit=120, required=True)
        note = _clean(note, field_name="note", limit=1000) or None
        ids = sorted({_imdb_id(value) for value in imdb_ids})
        if not ids:
            raise RatingHistoryError("Нужно передать хотя бы один imdb_id")
        now = _now()
        for imdb_id in ids:
            self.conn.execute(
                """
                INSERT INTO rating_watchlist(imdb_id, active, source, note, added_at, updated_at)
                VALUES (?, TRUE, ?, ?, ?, ?)
                ON CONFLICT(imdb_id) DO UPDATE SET
                    active=TRUE,
                    source=excluded.source,
                    note=COALESCE(excluded.note, rating_watchlist.note),
                    updated_at=excluded.updated_at
                """,
                [imdb_id, source, note, now, now],
            )
        return {"watched": ids, "count": len(ids), "active": True}

    def unwatch(self, imdb_ids: Iterable[str]) -> dict[str, Any]:
        ids = sorted({_imdb_id(value) for value in imdb_ids})
        if not ids:
            raise RatingHistoryError("Нужно передать хотя бы один imdb_id")
        now = _now()
        changed = 0
        for imdb_id in ids:
            before = self.conn.execute(
                "SELECT active FROM rating_watchlist WHERE imdb_id = ?", [imdb_id]
            ).fetchone()
            if before is None:
                continue
            self.conn.execute(
                "UPDATE rating_watchlist SET active=FALSE, updated_at=? WHERE imdb_id=?",
                [now, imdb_id],
            )
            changed += 1
        return {"unwatched": ids, "changed": changed}

    def watchlist(self, *, active_only: bool = True) -> list[dict[str, Any]]:
        where = "WHERE active=TRUE" if active_only else ""
        rows = self.conn.execute(
            f"""
            SELECT imdb_id, active, source, note, added_at, updated_at
            FROM rating_watchlist {where}
            ORDER BY imdb_id
            """
        ).fetchall()
        return [
            {
                "imdb_id": str(row[0]),
                "active": bool(row[1]),
                "source": str(row[2]),
                "note": row[3],
                "added_at": row[4].isoformat(),
                "updated_at": row[5].isoformat(),
            }
            for row in rows
        ]

    def capture_daily(
        self,
        imdb_db_path: str | Path | None = None,
        *,
        observed_at: datetime | str | None = None,
        source_fingerprint_sha256: str | None = None,
    ) -> dict[str, Any]:
        observed = _parse_datetime(observed_at or _now(), field_name="observed_at")
        snapshot_day: date = observed.date()
        source_fp = _sha256_or_none(source_fingerprint_sha256)

        existing = self.conn.execute(
            """
            SELECT snapshot_id, observed_at, source_fingerprint_sha256,
                   tracked_count, captured_count, missing_count
            FROM rating_snapshots WHERE snapshot_day = ?
            """,
            [snapshot_day],
        ).fetchone()
        if existing is not None:
            existing_fp = str(existing[2]) if existing[2] is not None else None
            if source_fp is not None and existing_fp is not None and source_fp != existing_fp:
                raise RatingHistoryError(
                    f"Snapshot за {snapshot_day.isoformat()} уже существует с другим source fingerprint; "
                    "append-only история не перезаписывается"
                )
            return {
                "version": RATING_HISTORY_VERSION,
                "snapshot_id": str(existing[0]),
                "snapshot_day": snapshot_day.isoformat(),
                "observed_at": existing[1].isoformat(),
                "source_fingerprint_sha256": existing_fp,
                "tracked_count": int(existing[3]),
                "captured_count": int(existing[4]),
                "missing_count": int(existing[5]),
                "idempotent": True,
                "append_only": True,
            }

        ids = [item["imdb_id"] for item in self.watchlist(active_only=True)]
        if not ids:
            return {
                "version": RATING_HISTORY_VERSION,
                "snapshot_day": snapshot_day.isoformat(),
                "observed_at": observed.isoformat(),
                "tracked_count": 0,
                "captured_count": 0,
                "missing_count": 0,
                "skipped_no_watchlist": True,
                "append_only": True,
            }

        source_path = Path(imdb_db_path or config.IMDB_DB_PATH)
        if not source_path.exists():
            raise RatingHistoryError(f"IMDb DuckDB не найдена: {source_path}")
        source = duckdb.connect(str(source_path), read_only=True)
        points: list[tuple[str, float, int]] = []
        try:
            tables = {str(row[0]) for row in source.execute("SHOW TABLES").fetchall()}
            if "title_ratings" not in tables:
                raise RatingHistoryError("IMDb DuckDB не содержит title_ratings")
            for chunk in _chunked(ids):
                rows = source.execute(
                    """
                    SELECT tconst, averageRating, numVotes
                    FROM title_ratings
                    WHERE tconst IN (SELECT * FROM UNNEST(?))
                    ORDER BY tconst
                    """,
                    [chunk],
                ).fetchall()
                for imdb_id, rating, votes in rows:
                    rating = float(rating)
                    votes = int(votes)
                    if not 0.0 <= rating <= 10.0:
                        raise RatingHistoryError(
                            f"IMDb rating вне диапазона 0..10 для {imdb_id}: {rating}"
                        )
                    if votes < 0:
                        raise RatingHistoryError(
                            f"IMDb numVotes отрицательный для {imdb_id}: {votes}"
                        )
                    points.append((str(imdb_id), rating, votes))
        finally:
            source.close()

        points.sort(key=lambda item: item[0])
        snapshot_id = f"rating-{snapshot_day.isoformat()}-{uuid4().hex[:12]}"
        tracked_count = len(ids)
        captured_count = len(points)
        missing_count = tracked_count - captured_count
        created_at = _now()

        self.conn.execute("BEGIN TRANSACTION")
        try:
            self.conn.execute(
                """
                INSERT INTO rating_snapshots(
                    snapshot_id, snapshot_day, observed_at, source_fingerprint_sha256,
                    tracked_count, captured_count, missing_count, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    snapshot_id,
                    snapshot_day,
                    observed,
                    source_fp,
                    tracked_count,
                    captured_count,
                    missing_count,
                    created_at,
                ],
            )
            if points:
                self.conn.executemany(
                    """
                    INSERT INTO rating_points(snapshot_id, imdb_id, average_rating, num_votes)
                    VALUES (?, ?, ?, ?)
                    """,
                    [(snapshot_id, imdb_id, rating, votes) for imdb_id, rating, votes in points],
                )
            self.conn.execute("COMMIT")
        except Exception:
            self.conn.execute("ROLLBACK")
            raise

        return {
            "version": RATING_HISTORY_VERSION,
            "snapshot_id": snapshot_id,
            "snapshot_day": snapshot_day.isoformat(),
            "observed_at": observed.isoformat(),
            "source_fingerprint_sha256": source_fp,
            "tracked_count": tracked_count,
            "captured_count": captured_count,
            "missing_count": missing_count,
            "idempotent": False,
            "append_only": True,
        }

    def history(self, imdb_id: str, *, limit: int | None = None) -> list[dict[str, Any]]:
        imdb_id = _imdb_id(imdb_id)
        sql = """
            SELECT s.snapshot_id, s.observed_at, s.source_fingerprint_sha256,
                   p.average_rating, p.num_votes
            FROM rating_points p
            JOIN rating_snapshots s USING(snapshot_id)
            WHERE p.imdb_id = ?
            ORDER BY s.observed_at
        """
        params: list[Any] = [imdb_id]
        if limit is not None:
            limit = int(limit)
            if limit < 1:
                raise RatingHistoryError("limit должен быть >= 1")
            sql += " LIMIT ?"
            params.append(limit)
        rows = self.conn.execute(sql, params).fetchall()
        return [
            {
                "imdb_id": imdb_id,
                "snapshot_id": str(row[0]),
                "observed_at": row[1].isoformat(),
                "source_fingerprint_sha256": str(row[2]) if row[2] is not None else None,
                "average_rating": float(row[3]),
                "num_votes": int(row[4]),
            }
            for row in rows
        ]

    def rating_as_of(self, imdb_id: str, cutoff: datetime | str) -> dict[str, Any] | None:
        imdb_id = _imdb_id(imdb_id)
        cutoff_dt = _parse_datetime(cutoff, field_name="cutoff")
        row = self.conn.execute(
            """
            SELECT s.snapshot_id, s.observed_at, s.source_fingerprint_sha256,
                   p.average_rating, p.num_votes
            FROM rating_points p
            JOIN rating_snapshots s USING(snapshot_id)
            WHERE p.imdb_id = ? AND s.observed_at <= ?
            ORDER BY s.observed_at DESC
            LIMIT 1
            """,
            [imdb_id, cutoff_dt],
        ).fetchone()
        if row is None:
            return None
        return {
            "imdb_id": imdb_id,
            "snapshot_id": str(row[0]),
            "observed_at": row[1].isoformat(),
            "source_fingerprint_sha256": str(row[2]) if row[2] is not None else None,
            "average_rating": float(row[3]),
            "num_votes": int(row[4]),
            "point_in_time": True,
        }

    def status(self) -> dict[str, Any]:
        watch = self.conn.execute(
            "SELECT COUNT(*), SUM(CASE WHEN active THEN 1 ELSE 0 END) FROM rating_watchlist"
        ).fetchone()
        snapshots = self.conn.execute(
            "SELECT COUNT(*), MIN(observed_at), MAX(observed_at) FROM rating_snapshots"
        ).fetchone()
        points = self.conn.execute("SELECT COUNT(*) FROM rating_points").fetchone()[0]
        return {
            "version": RATING_HISTORY_VERSION,
            "watchlist_total": int(watch[0] or 0),
            "watchlist_active": int(watch[1] or 0),
            "snapshot_count": int(snapshots[0] or 0),
            "point_count": int(points or 0),
            "first_observed_at": snapshots[1].isoformat() if snapshots[1] else None,
            "last_observed_at": snapshots[2].isoformat() if snapshots[2] else None,
            "append_only": True,
        }
