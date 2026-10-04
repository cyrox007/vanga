from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from src.rating_history import RatingHistoryError, RatingHistoryStore, _imdb_id, _parse_datetime


MILESTONE_VERSION = 1
DEFAULT_MILESTONES = {
    "rating_early": {"offset_days": 0, "max_lag_days": 7},
    "rating_30d": {"offset_days": 30, "max_lag_days": 7},
    "rating_180d": {"offset_days": 180, "max_lag_days": 14},
    "rating_long_term": {"offset_days": 365, "max_lag_days": 30},
}


class RatingMilestoneExtractor:
    """Строит post-release targets только из реально наблюдавшихся P7 points.

    Для каждого milestone берётся первая точка НЕ РАНЬШЕ target date. Слишком
    поздняя точка не маскируется как валидный target: она остаётся diagnostic
    candidate с ``ready=false``.
    """

    def __init__(self, store: RatingHistoryStore) -> None:
        self.store = store
        self.conn = store.conn

    @staticmethod
    def _normalize_policy(policy: dict[str, Any] | None) -> dict[str, dict[str, int]]:
        raw = policy or DEFAULT_MILESTONES
        if not isinstance(raw, dict) or not raw:
            raise RatingHistoryError("milestone policy должен быть непустым объектом")
        result: dict[str, dict[str, int]] = {}
        for name, spec in raw.items():
            if not isinstance(spec, dict):
                raise RatingHistoryError(f"milestone {name} должен быть объектом")
            try:
                offset = int(spec["offset_days"])
                max_lag = int(spec["max_lag_days"])
            except (KeyError, TypeError, ValueError) as exc:
                raise RatingHistoryError(
                    f"milestone {name} требует целые offset_days/max_lag_days"
                ) from exc
            if offset < 0 or max_lag < 0:
                raise RatingHistoryError(
                    f"milestone {name}: offset_days/max_lag_days должны быть >= 0"
                )
            result[str(name)] = {
                "offset_days": offset,
                "max_lag_days": max_lag,
            }
        return result

    def _first_point_between(
        self,
        imdb_id: str,
        target_at: datetime,
        as_of: datetime,
    ) -> tuple | None:
        return self.conn.execute(
            """
            SELECT s.snapshot_id, s.observed_at, s.source_fingerprint_sha256,
                   p.average_rating, p.num_votes
            FROM rating_points p
            JOIN rating_snapshots s USING(snapshot_id)
            WHERE p.imdb_id = ?
              AND s.observed_at >= ?
              AND s.observed_at <= ?
            ORDER BY s.observed_at ASC
            LIMIT 1
            """,
            [imdb_id, target_at, as_of],
        ).fetchone()

    def extract(
        self,
        imdb_id: str,
        release_at: datetime | str,
        *,
        as_of: datetime | str | None = None,
        policy: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        imdb_id = _imdb_id(imdb_id)
        release = _parse_datetime(release_at, field_name="release_at")
        cutoff = _parse_datetime(
            as_of or datetime.now(timezone.utc),
            field_name="as_of",
        )
        if cutoff < release:
            raise RatingHistoryError("as_of не может быть раньше release_at")
        milestones = self._normalize_policy(policy)

        output: dict[str, dict[str, Any]] = {}
        due_count = 0
        ready_count = 0
        for name, spec in milestones.items():
            target_at = release + timedelta(days=spec["offset_days"])
            due = cutoff >= target_at
            item: dict[str, Any] = {
                "name": name,
                "offset_days": spec["offset_days"],
                "max_lag_days": spec["max_lag_days"],
                "target_at": target_at.isoformat(),
                "as_of": cutoff.isoformat(),
                "due": due,
                "ready": False,
                "usable_as_target": False,
                "average_rating": None,
                "num_votes": None,
                "observed_at": None,
                "lag_days": None,
                "missing_reason": None,
            }
            if not due:
                item["missing_reason"] = "not_due_yet"
                output[name] = item
                continue

            due_count += 1
            row = self._first_point_between(imdb_id, target_at, cutoff)
            if row is None:
                item["missing_reason"] = "no_observation_after_target"
                output[name] = item
                continue

            observed = row[1]
            if observed.tzinfo is None:
                observed = observed.replace(tzinfo=timezone.utc)
            observed = observed.astimezone(timezone.utc)
            lag_days = (observed - target_at).total_seconds() / 86400.0
            item["candidate_snapshot_id"] = str(row[0])
            item["candidate_observed_at"] = observed.isoformat()
            item["candidate_lag_days"] = round(lag_days, 6)
            if lag_days > spec["max_lag_days"]:
                item["missing_reason"] = "observation_too_late"
                output[name] = item
                continue

            item.update(
                {
                    "ready": True,
                    "usable_as_target": True,
                    "average_rating": float(row[3]),
                    "num_votes": int(row[4]),
                    "snapshot_id": str(row[0]),
                    "observed_at": observed.isoformat(),
                    "lag_days": round(lag_days, 6),
                    "source_fingerprint_sha256": (
                        str(row[2]) if row[2] is not None else None
                    ),
                    "missing_reason": None,
                }
            )
            ready_count += 1
            output[name] = item

        def delta(left: str, right: str) -> float | None:
            a = output.get(left) or {}
            b = output.get(right) or {}
            if not a.get("ready") or not b.get("ready"):
                return None
            return round(float(b["average_rating"]) - float(a["average_rating"]), 6)

        return {
            "version": MILESTONE_VERSION,
            "imdb_id": imdb_id,
            "release_at": release.isoformat(),
            "as_of": cutoff.isoformat(),
            "milestones": output,
            "readiness": {
                "milestone_count": len(milestones),
                "due_count": due_count,
                "ready_count": ready_count,
                "due_ready_ratio": (
                    round(ready_count / due_count, 6) if due_count else None
                ),
                "all_due_ready": ready_count == due_count if due_count else True,
                "all_milestones_ready": ready_count == len(milestones),
            },
            "rating_deltas": {
                "early_to_30d": delta("rating_early", "rating_30d"),
                "30d_to_180d": delta("rating_30d", "rating_180d"),
                "180d_to_long_term": delta("rating_180d", "rating_long_term"),
                "early_to_long_term": delta("rating_early", "rating_long_term"),
            },
            "research_only": True,
            "pre_release_feature": False,
        }
