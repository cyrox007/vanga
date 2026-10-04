from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from settings import config
from src.future_release_import import FutureReleaseBatchImporter
from src.wikidata_future_enrichment import (
    MAX_PROJECTS_PER_BATCH,
    WikidataFutureEnricher,
)
from src.wikidata_future_releases import WikidataFutureReleaseCollector


REFRESH_PIPELINE_VERSION = 1


class FutureReleaseRefreshError(RuntimeError):
    """Ошибка многошагового P9 refresh с сохранённым отчётом о частичном результате."""

    def __init__(self, message: str, *, report: dict[str, Any]) -> None:
        super().__init__(message)
        self.report = report


def _canonical_json(payload: Any) -> str:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _fingerprint(payload: Any) -> str:
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def _dt(value: Any, *, field_name: str) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value or "").strip().replace("Z", "+00:00"))
        except (TypeError, ValueError) as exc:
            raise FutureReleaseRefreshError(
                f"{field_name} должен быть ISO-8601 datetime",
                report={
                    "version": REFRESH_PIPELINE_VERSION,
                    "refresh_complete": False,
                    "failed_stage": "input_validation",
                },
            ) from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _chunks(items: list[str], size: int) -> list[list[str]]:
    return [items[index : index + size] for index in range(0, len(items), size)]


class FutureReleaseRefreshPipeline:
    """Один операционный P9 pipeline: discovery -> import -> enrichment -> import.

    Каждая collector-выгрузка импортируется своей атомарной транзакцией. Это
    намеренно не одна глобальная транзакция: сеть не держит DuckDB transaction,
    а уже подтверждённый discovery-batch не теряется, если enrichment временно
    недоступен. При частичном сбое исключение содержит машинно-читаемый report.
    """

    def __init__(
        self,
        future_db_path: str | Path | None = None,
        *,
        discovery_collector: Any | None = None,
        enrichment_collector: Any | None = None,
    ) -> None:
        self.future_db_path = Path(
            future_db_path or config.FUTURE_RELEASE_DB_PATH
        )
        self.discovery = discovery_collector or WikidataFutureReleaseCollector()
        self.enrichment = enrichment_collector or WikidataFutureEnricher()

    @staticmethod
    def _project_ids(discovery_result: dict[str, Any]) -> list[str]:
        batch = discovery_result.get("batch")
        bundle = batch.get("bundle") if isinstance(batch, dict) else None
        projects = bundle.get("projects") if isinstance(bundle, dict) else None
        if not isinstance(projects, list):
            raise ValueError("Discovery result не содержит batch.bundle.projects[]")
        return sorted(
            {
                str(item.get("project_id") or "").strip()
                for item in projects
                if isinstance(item, dict) and str(item.get("project_id") or "").strip()
            }
        )

    @staticmethod
    def _collector_summary(result: dict[str, Any]) -> dict[str, Any]:
        batch = result.get("batch") if isinstance(result, dict) else None
        return {
            "collector": result.get("collector"),
            "collector_version": result.get("collector_version"),
            "raw_fingerprint_sha256": result.get("raw_fingerprint_sha256"),
            "raw_cache_path": result.get("raw_cache_path"),
            "warning_count": int(result.get("warning_count") or 0),
            "warnings": list(result.get("warnings") or []),
            "batch_id": batch.get("batch_id") if isinstance(batch, dict) else None,
            "provider": batch.get("provider") if isinstance(batch, dict) else None,
            "source_fingerprint_sha256": (
                batch.get("source_fingerprint_sha256")
                if isinstance(batch, dict)
                else None
            ),
        }

    @staticmethod
    def _finalize(report: dict[str, Any]) -> dict[str, Any]:
        stable = {
            "version": report["version"],
            "from_at": report["from_at"],
            "to_at": report["to_at"],
            "retrieved_at": report["retrieved_at"],
            "discovery_limit": report["discovery_limit"],
            "enrichment_chunk_size": report["enrichment_chunk_size"],
            "project_ids": report.get("project_ids") or [],
            "discovery_source_fingerprint_sha256": (
                (report.get("discovery") or {}).get("source_fingerprint_sha256")
            ),
            "enrichment_source_fingerprints_sha256": [
                item.get("source_fingerprint_sha256")
                for item in report.get("enrichment_batches") or []
            ],
            "refresh_complete": bool(report.get("refresh_complete")),
            "failed_stage": report.get("failed_stage"),
        }
        report["refresh_fingerprint_sha256"] = _fingerprint(stable)
        return report

    def run(
        self,
        from_at: Any,
        to_at: Any,
        *,
        discovery_limit: int = 1000,
        enrichment_chunk_size: int = 100,
        retrieved_at: Any | None = None,
    ) -> dict[str, Any]:
        start = _dt(from_at, field_name="from_at")
        end = _dt(to_at, field_name="to_at")
        observed = _dt(
            retrieved_at or datetime.now(timezone.utc),
            field_name="retrieved_at",
        )
        if start >= end:
            raise FutureReleaseRefreshError(
                "from_at должен быть раньше to_at",
                report={
                    "version": REFRESH_PIPELINE_VERSION,
                    "refresh_complete": False,
                    "failed_stage": "input_validation",
                },
            )
        try:
            discovery_limit = max(1, min(int(discovery_limit), 5000))
            enrichment_chunk_size = max(
                1,
                min(int(enrichment_chunk_size), MAX_PROJECTS_PER_BATCH),
            )
        except (TypeError, ValueError) as exc:
            raise FutureReleaseRefreshError(
                "discovery_limit/enrichment_chunk_size должны быть целыми числами",
                report={
                    "version": REFRESH_PIPELINE_VERSION,
                    "refresh_complete": False,
                    "failed_stage": "input_validation",
                },
            ) from exc

        report: dict[str, Any] = {
            "version": REFRESH_PIPELINE_VERSION,
            "from_at": start.isoformat(),
            "to_at": end.isoformat(),
            "retrieved_at": observed.isoformat(),
            "future_db_path": str(self.future_db_path),
            "discovery_limit": discovery_limit,
            "enrichment_chunk_size": enrichment_chunk_size,
            "refresh_complete": False,
            "failed_stage": None,
            "project_ids": [],
            "discovery": None,
            "discovery_import": None,
            "enrichment_batches": [],
            "enrichment_imports": [],
            "warning_count": 0,
            "network_required_for_inference": False,
        }

        try:
            discovery_result = self.discovery.collect(
                start,
                end,
                limit=discovery_limit,
                retrieved_at=observed,
            )
        except Exception as exc:
            report["failed_stage"] = "discovery_collect"
            self._finalize(report)
            raise FutureReleaseRefreshError(
                f"P9 discovery collector завершился ошибкой: {exc}",
                report=report,
            ) from exc

        report["discovery"] = self._collector_summary(discovery_result)
        report["warning_count"] += report["discovery"]["warning_count"]
        try:
            project_ids = self._project_ids(discovery_result)
            report["project_ids"] = project_ids
            with FutureReleaseBatchImporter(self.future_db_path) as importer:
                report["discovery_import"] = importer.import_batch(
                    discovery_result["batch"]
                )
        except Exception as exc:
            report["failed_stage"] = "discovery_import"
            self._finalize(report)
            raise FutureReleaseRefreshError(
                f"P9 discovery batch не импортирован: {exc}",
                report=report,
            ) from exc

        if not project_ids:
            report["refresh_complete"] = True
            report["enrichment_skipped_reason"] = "discovery_batch_has_no_projects"
            return self._finalize(report)

        for batch_index, project_chunk in enumerate(
            _chunks(project_ids, enrichment_chunk_size),
            start=1,
        ):
            try:
                enrichment_result = self.enrichment.collect_from_registry(
                    self.future_db_path,
                    project_ids=project_chunk,
                    limit=len(project_chunk),
                    retrieved_at=observed,
                )
            except Exception as exc:
                report["failed_stage"] = "enrichment_collect"
                report["failed_enrichment_batch"] = batch_index
                report["failed_project_ids"] = project_chunk
                self._finalize(report)
                raise FutureReleaseRefreshError(
                    f"P9 enrichment batch {batch_index} завершился ошибкой: {exc}",
                    report=report,
                ) from exc

            summary = self._collector_summary(enrichment_result)
            summary["batch_index"] = batch_index
            summary["project_ids"] = project_chunk
            report["enrichment_batches"].append(summary)
            report["warning_count"] += summary["warning_count"]
            try:
                with FutureReleaseBatchImporter(self.future_db_path) as importer:
                    imported = importer.import_batch(enrichment_result["batch"])
            except Exception as exc:
                report["failed_stage"] = "enrichment_import"
                report["failed_enrichment_batch"] = batch_index
                report["failed_project_ids"] = project_chunk
                self._finalize(report)
                raise FutureReleaseRefreshError(
                    f"P9 enrichment batch {batch_index} не импортирован: {exc}",
                    report=report,
                ) from exc
            report["enrichment_imports"].append(
                {
                    "batch_index": batch_index,
                    "project_ids": project_chunk,
                    **imported,
                }
            )

        report["refresh_complete"] = True
        report["enriched_project_count"] = len(project_ids)
        report["enrichment_batch_count"] = len(report["enrichment_batches"])
        return self._finalize(report)
