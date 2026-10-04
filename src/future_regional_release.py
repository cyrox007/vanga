from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from settings import config
from src.future_prediction_payload import _fingerprint
from src.future_releases import FutureReleaseStore, _dt
from src.future_temporal_facts import TemporalFuturePredictionPayloadBuilder
from src.future_territories import FutureTerritoryStore, _canonical_iso


class FutureRegionalReleaseError(ValueError):
    pass


def _parse_cutoff(value: Any) -> datetime:
    parsed = _dt(value, field_name="cutoff")
    if parsed is None:
        raise FutureRegionalReleaseError("cutoff обязателен")
    return parsed.astimezone(timezone.utc)


class FutureRegionalReleaseService:
    """Cache-only представление P9 release data для явной ISO-территории.

    Сервис не подменяет отсутствующую региональную дату значением ``worldwide``.
    Исходные release observations остаются неизменными; Wikidata territory QID
    канонизируются только через temporal-safe mapping из ``FutureTerritoryStore``.
    """

    def __init__(self, future_db_path: str | Path | None = None) -> None:
        self.future_db_path = Path(future_db_path or config.FUTURE_RELEASE_DB_PATH)

    def snapshot_as_of(
        self,
        project_id: str,
        cutoff: Any,
        *,
        territory: str,
    ) -> dict[str, Any]:
        cutoff_dt = _parse_cutoff(cutoff)
        canonical = _canonical_iso(territory)
        with FutureReleaseStore(self.future_db_path) as registry:
            # Metadata/team/status не зависят от release territory. Специальный
            # ключ гарантированно не превращается в скрытый worldwide fallback.
            base = registry.snapshot_as_of(
                project_id,
                cutoff_dt,
                territory="__regional_metadata_only__",
            )
            regional = FutureTerritoryStore(registry).release_snapshot_as_of(
                project_id,
                cutoff_dt,
                canonical_territory=canonical,
            )

        release_window = regional.get("release_window")
        release_at = None
        if release_window and release_window.get("precision") == "exact":
            release_at = release_window.get("release_start_at")

        if regional.get("conflict"):
            resolution = "conflict"
        elif release_window is not None:
            resolution = "resolved"
        elif regional.get("unresolved_observations"):
            resolution = "mapping_incomplete"
        else:
            resolution = "missing"

        result = dict(base)
        result.update(
            {
                "territory": canonical,
                "target_territory": canonical,
                "release_date_conflict": bool(regional.get("conflict")),
                "release_candidates": list(regional.get("candidates") or []),
                "release_window": release_window,
                "release_at": release_at,
                "regional_release_resolution": resolution,
                "unresolved_release_observations": list(
                    regional.get("unresolved_observations") or []
                ),
                "regional_release_snapshot": regional,
                "territory_policy": "explicit_iso3166_no_worldwide_fallback",
                "network_required_for_inference": False,
            }
        )
        return result

    def catalog_as_of(
        self,
        cutoff: Any,
        *,
        territory: str,
        from_at: Any | None = None,
        to_at: Any | None = None,
        include_conflicts: bool = True,
    ) -> dict[str, Any]:
        cutoff_dt = _parse_cutoff(cutoff)
        canonical = _canonical_iso(territory)
        from_dt = _dt(from_at, field_name="from_at", required=False) or cutoff_dt
        to_dt = _dt(to_at, field_name="to_at", required=False)

        with FutureReleaseStore(self.future_db_path) as registry:
            project_ids = [
                str(row[0])
                for row in registry.conn.execute(
                    "SELECT project_id FROM future_release_projects ORDER BY project_id"
                ).fetchall()
            ]

        items: list[dict[str, Any]] = []
        for project_id in project_ids:
            snapshot = self.snapshot_as_of(
                project_id,
                cutoff_dt,
                territory=canonical,
            )
            candidates = snapshot["release_candidates"]
            if not candidates:
                continue
            if snapshot["release_date_conflict"] and not include_conflicts:
                continue
            overlaps = False
            for candidate in candidates:
                start = _dt(
                    candidate["release_start_at"], field_name="release_start_at"
                )
                end = _dt(candidate["release_end_at"], field_name="release_end_at")
                if end is None or start is None:
                    continue
                if end < from_dt:
                    continue
                if to_dt is not None and start > to_dt:
                    continue
                overlaps = True
                break
            if overlaps:
                items.append(snapshot)

        payload = {
            "version": 2,
            "cutoff_at": cutoff_dt.isoformat(),
            "territory": canonical,
            "territory_policy": "explicit_iso3166_no_worldwide_fallback",
            "from_at": from_dt.isoformat(),
            "to_at": to_dt.isoformat() if to_dt else None,
            "items": items,
            "network_required_for_inference": False,
        }
        payload["catalog_fingerprint_sha256"] = _fingerprint(payload)
        return payload


class RegionalTemporalFuturePredictionPayloadBuilder(
    TemporalFuturePredictionPayloadBuilder
):
    """Temporal future payload с обязательной canonical ISO release territory.

    Старый builder сохраняется для обратной совместимости. Этот класс предназначен
    для публичного/регионального сценария и никогда не делает implicit worldwide
    fallback.
    """

    def build(
        self,
        project_id: str,
        cutoff: Any,
        *,
        territory: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        regional_service = FutureRegionalReleaseService(self.future_db_path)
        regional = regional_service.snapshot_as_of(
            project_id,
            cutoff,
            territory=territory,
        )
        canonical = regional["target_territory"]

        raw_territory = "__regional_release_unresolved__"
        release_window = regional.get("release_window")
        if release_window:
            raw_candidates = sorted(
                {
                    str(item.get("source_territory") or "").casefold()
                    for item in release_window.get("evidence") or []
                    if item.get("source_territory")
                }
            )
            # Если есть уже canonical ISO observation (например TMDb), используем
            # её как техническую опору для старого builder. Иначе берём первый
            # доказанный raw territory; итоговая policy всё равно задаётся regional.
            if canonical in raw_candidates:
                raw_territory = canonical
            elif raw_candidates:
                raw_territory = raw_candidates[0]

        result = super().build(
            project_id,
            cutoff,
            territory=raw_territory,
            **kwargs,
        )

        blockers = [
            item
            for item in list(result.get("blockers") or [])
            if item not in {"release_date_conflict", "exact_release_date_missing"}
        ]
        warnings = list(result.get("warnings") or [])

        resolution = regional["regional_release_resolution"]
        if resolution == "conflict":
            blockers.append("regional_release_date_conflict")
        elif resolution == "mapping_incomplete":
            blockers.append("regional_release_date_missing")
            warnings.append("regional_release_mapping_incomplete")
        elif resolution == "missing":
            blockers.append("regional_release_date_missing")
        elif regional.get("release_at") is None:
            blockers.append("regional_exact_release_date_missing")

        if regional.get("unresolved_release_observations"):
            warnings.append("regional_release_mapping_incomplete")

        blockers = list(dict.fromkeys(blockers))
        warnings = list(dict.fromkeys(warnings))
        result["blockers"] = blockers
        result["warnings"] = warnings
        result["prediction_ready"] = not blockers
        result["target_territory"] = canonical
        result["territory_policy"] = "explicit_iso3166_no_worldwide_fallback"
        result["regional_release_snapshot"] = regional["regional_release_snapshot"]
        result["future_release_snapshot"] = regional
        result.setdefault("input_sources", {})["release_date"] = "p9_regional_release"
        result.setdefault("temporal_contract", {})[
            "release_territory_explicit"
        ] = True

        if not blockers and result.get("request") is not None:
            release_at = datetime.fromisoformat(
                str(regional["release_at"]).replace("Z", "+00:00")
            )
            result["request"]["year"] = int(release_at.year)
        else:
            result["request"] = None

        # Parent builder вычисляет fingerprint до регионального post-processing.
        # Пересчитываем его по окончательному payload.
        result.pop("payload_fingerprint_sha256", None)
        result["payload_fingerprint_sha256"] = _fingerprint(result)
        return result
