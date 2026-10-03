from __future__ import annotations

import statistics
from typing import Any

from src.source_context import SourceContextStore, _parse_datetime


class SourceFormatPressureContext:
    """Прозрачные pre-release format-pressure proxies.

    Эти признаки НЕ являются настоящим ``adaptation_compression_ratio``: без
    структурированного покрытия source arcs нельзя честно измерить степень
    сжатия сюжета. Слой использует только publication/series/format/runtime facts.
    """

    def __init__(self, store: SourceContextStore) -> None:
        self.store = store
        self.conn = store.conn

    def features_as_of(self, project_id: str, cutoff) -> dict[str, float]:
        self.store._require_project(project_id)
        cutoff_dt = _parse_datetime(cutoff, field_name="cutoff")
        project = self.conn.execute(
            """
            SELECT release_at, planned_runtime_minutes,
                   planned_episode_count, planned_episode_runtime_minutes,
                   format_known_at
            FROM source_context_projects
            WHERE project_id = ?
            """,
            [project_id],
        ).fetchone()
        format_visible = bool(project and project[4] <= cutoff_dt)
        links = self.store.links_as_of(project_id, cutoff_dt)

        work_by_id: dict[str, dict[str, Any]] = {}
        primary_ids: set[str] = set()
        relation_types: set[str] = set()
        source_types: set[str] = set()
        for item in links:
            work_by_id.setdefault(item["work_id"], item)
            if item["is_primary"]:
                primary_ids.add(item["work_id"])
            relation_types.add(item["relation_type"])
            source_types.add(item["source_type"])

        works = list(work_by_id.values())
        work_count = len(works)
        release_at = project[0] if project else None

        release_ages: list[float] = []
        publication_after_release = 0
        if format_visible and release_at is not None:
            for item in works:
                publication_at = item["first_publication_at"]
                if publication_at is None:
                    continue
                if publication_at > release_at:
                    publication_after_release += 1
                    continue
                release_ages.append(
                    (release_at - publication_at).total_seconds() / 86400.0 / 365.25
                )

        series_progress: list[float] = []
        series_ids: set[str] = set()
        for item in works:
            series_id = item["series_id"]
            if series_id:
                series_ids.add(str(series_id))
            position = item["series_position"]
            size = item["series_size"]
            if position is not None and size is not None and float(size) > 0:
                series_progress.append(float(position) / float(size))

        total_runtime = 0.0
        episode_count = 0.0
        runtime_known = False
        if format_visible:
            if project[1] is not None:
                total_runtime = float(project[1])
                runtime_known = total_runtime > 0
            elif project[2] is not None and project[3] is not None:
                episode_count = float(project[2])
                total_runtime = episode_count * float(project[3])
                runtime_known = total_runtime > 0
            elif project[2] is not None:
                episode_count = float(project[2])
            if project[2] is not None:
                episode_count = float(project[2])

        runtime_per_work_known = runtime_known and work_count > 0
        runtime_per_primary_known = runtime_known and len(primary_ids) > 0
        episodes_per_work_known = episode_count > 0 and work_count > 0

        return {
            "source_age_at_release_known_ratio": (
                float(len(release_ages)) / float(work_count) if work_count else 0.0
            ),
            "source_age_at_release_years_mean": (
                float(statistics.fmean(release_ages)) if release_ages else 0.0
            ),
            "source_age_at_release_years_min": float(min(release_ages, default=0.0)),
            "source_age_at_release_years_max": float(max(release_ages, default=0.0)),
            "source_publication_after_release_count": float(publication_after_release),
            "source_series_count": float(len(series_ids)),
            "source_series_progress_known_ratio": (
                float(len(series_progress)) / float(work_count) if work_count else 0.0
            ),
            "source_series_progress_ratio_mean": (
                float(statistics.fmean(series_progress)) if series_progress else 0.0
            ),
            "source_series_progress_ratio_max": float(max(series_progress, default=0.0)),
            "source_type_diversity": float(len(source_types)),
            "source_relation_diversity": float(len(relation_types)),
            "source_format_pressure_runtime_known": 1.0 if runtime_known else 0.0,
            "source_runtime_per_linked_work_known": 1.0 if runtime_per_work_known else 0.0,
            "source_runtime_per_linked_work_minutes": (
                total_runtime / float(work_count) if runtime_per_work_known else 0.0
            ),
            "source_runtime_per_primary_work_known": 1.0 if runtime_per_primary_known else 0.0,
            "source_runtime_per_primary_work_minutes": (
                total_runtime / float(len(primary_ids)) if runtime_per_primary_known else 0.0
            ),
            "source_episodes_per_linked_work_known": 1.0 if episodes_per_work_known else 0.0,
            "source_episodes_per_linked_work": (
                episode_count / float(work_count) if episodes_per_work_known else 0.0
            ),
        }
