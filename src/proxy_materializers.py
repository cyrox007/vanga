from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import duckdb

from settings import config
from src.production_context import ProductionContextStore
from src.proxy_ablation import verify_plan_fingerprint
from src.proxy_hypotheses import ProxyHypothesisValidationError
from src.source_context import SourceContextStore
from src.source_team_history import SourceTeamHistory


MATERIALIZER_VERSION = 1


def _clean(value: Any, *, field_name: str, limit: int = 500, required: bool = False) -> str:
    text = " ".join(str(value or "").strip().split())
    if required and not text:
        raise ProxyHypothesisValidationError(f"{field_name} не должен быть пустым")
    if len(text) > limit:
        raise ProxyHypothesisValidationError(
            f"{field_name} длиннее допустимых {limit} символов"
        )
    return text


def _parse_timestamp(value: Any, *, field_name: str) -> datetime:
    text = _clean(value, field_name=field_name, limit=80, required=True)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ProxyHypothesisValidationError(
            f"{field_name} должен быть ISO-8601 timestamp"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ProxyHypothesisValidationError(
            f"{field_name} должен содержать timezone offset"
        )
    return parsed.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _max_dt(values: list[datetime | None]) -> datetime | None:
    actual = [value for value in values if value is not None]
    return max(actual) if actual else None


def _missing(
    *,
    target_id: str,
    feature_name: str,
    source_layer: str,
    temporal_contract: str,
    reason: str,
) -> dict[str, Any]:
    return {
        "target_id": target_id,
        "feature_name": feature_name,
        "source_layer": source_layer,
        "temporal_contract": temporal_contract,
        "available": False,
        "value": None,
        "missing_reason": reason,
    }


def _available_timestamped(
    *,
    target_id: str,
    feature_name: str,
    source_layer: str,
    temporal_contract: str,
    value: float,
    provenance_id: str,
    source_timestamp: datetime,
) -> dict[str, Any]:
    return {
        "target_id": target_id,
        "feature_name": feature_name,
        "source_layer": source_layer,
        "temporal_contract": temporal_contract,
        "available": True,
        "value": float(value),
        "provenance_id": provenance_id,
        "source_timestamp": _iso(source_timestamp),
    }


def _available_history(
    *,
    target_id: str,
    feature_name: str,
    source_layer: str,
    temporal_contract: str,
    value: float,
    provenance_id: str,
    history_year: int,
) -> dict[str, Any]:
    return {
        "target_id": target_id,
        "feature_name": feature_name,
        "source_layer": source_layer,
        "temporal_contract": temporal_contract,
        "available": True,
        "value": float(value),
        "provenance_id": provenance_id,
        "history_year": int(history_year),
    }


class ProxySourceMaterializer:
    """Материализует только поддержанные pre-release proxy из локальных stores.

    Слой не обучает модель и не принимает hypothesis. Его единственная задача —
    превратить зарегистрированные feature specs в audit-compatible rows с явным
    available/missing, provenance и temporal timestamp/history year.
    """

    def __init__(
        self,
        *,
        source_db_path: str | Path | None = None,
        production_db_path: str | Path | None = None,
        imdb_db_path: str | Path | None = None,
    ) -> None:
        self.source_db_path = Path(source_db_path or config.SOURCE_CONTEXT_DB_PATH)
        self.production_db_path = Path(
            production_db_path or config.PRODUCTION_CONTEXT_DB_PATH
        )
        self.imdb_db_path = Path(imdb_db_path or config.IMDB_DB_PATH)

    @staticmethod
    def _feature_specs(plan: dict[str, Any]) -> list[dict[str, Any]]:
        raw = plan.get("candidate_pre_release_features") or []
        if not isinstance(raw, list) or not raw:
            raise ProxyHypothesisValidationError(
                "Ablation plan не содержит candidate_pre_release_features"
            )
        result: list[dict[str, Any]] = []
        seen: set[str] = set()
        for item in raw:
            if not isinstance(item, dict):
                raise ProxyHypothesisValidationError(
                    "candidate_pre_release_features[] должен содержать JSON-объекты"
                )
            feature_name = _clean(
                item.get("feature_name"),
                field_name="feature_name",
                limit=180,
                required=True,
            )
            if feature_name in seen:
                raise ProxyHypothesisValidationError(
                    f"Дублирующийся feature_name: {feature_name}"
                )
            seen.add(feature_name)
            result.append(dict(item))
        return result

    @staticmethod
    def _normalize_targets(payload: dict[str, Any] | list[dict[str, Any]]) -> list[dict[str, Any]]:
        raw_targets = payload if isinstance(payload, list) else payload.get("targets")
        if not isinstance(raw_targets, list) or not raw_targets:
            raise ProxyHypothesisValidationError("targets должен быть непустым массивом")
        result: list[dict[str, Any]] = []
        seen: set[str] = set()
        for raw in raw_targets:
            if not isinstance(raw, dict):
                raise ProxyHypothesisValidationError("targets[] должен содержать JSON-объекты")
            target_id = _clean(
                raw.get("target_id"), field_name="target_id", limit=180, required=True
            )
            if target_id in seen:
                raise ProxyHypothesisValidationError(f"Дублирующийся target_id: {target_id}")
            seen.add(target_id)
            try:
                target_year = int(raw.get("target_year"))
            except (TypeError, ValueError) as exc:
                raise ProxyHypothesisValidationError(
                    f"target_year для {target_id} должен быть целым"
                ) from exc
            cutoff = _parse_timestamp(raw.get("cutoff_at"), field_name=f"{target_id}.cutoff_at")
            release = _parse_timestamp(raw.get("release_at"), field_name=f"{target_id}.release_at")
            if cutoff >= release:
                raise ProxyHypothesisValidationError(
                    f"{target_id}: cutoff_at должен быть строго раньше release_at"
                )
            result.append(
                {
                    "target_id": target_id,
                    "target_year": target_year,
                    "cutoff_at": _iso(cutoff),
                    "release_at": _iso(release),
                    "source_project_id": _clean(
                        raw.get("source_project_id"),
                        field_name="source_project_id",
                        limit=180,
                    ) or None,
                    "production_project_id": _clean(
                        raw.get("production_project_id"),
                        field_name="production_project_id",
                        limit=180,
                    ) or None,
                }
            )
        return result

    @staticmethod
    def _resolve_project_id(
        conn: duckdb.DuckDBPyConnection,
        *,
        table: str,
        target_id: str,
        explicit_project_id: str | None,
    ) -> str | None:
        if explicit_project_id:
            row = conn.execute(
                f"SELECT project_id FROM {table} WHERE project_id = ? LIMIT 1",
                [explicit_project_id],
            ).fetchone()
            return str(row[0]) if row else None
        rows = conn.execute(
            f"SELECT project_id FROM {table} WHERE imdb_id = ? ORDER BY project_id LIMIT 2",
            [target_id],
        ).fetchall()
        if len(rows) > 1:
            raise ProxyHypothesisValidationError(
                f"Для {target_id} найдено несколько project_id в {table}; укажите project_id явно"
            )
        return str(rows[0][0]) if rows else None

    def materialize(
        self,
        plan: dict[str, Any],
        targets_payload: dict[str, Any] | list[dict[str, Any]],
    ) -> dict[str, Any]:
        plan_fingerprint = verify_plan_fingerprint(plan)
        specs = self._feature_specs(plan)
        targets = self._normalize_targets(targets_payload)
        observations: list[dict[str, Any]] = []

        source_store = SourceContextStore(self.source_db_path)
        production_store = ProductionContextStore(self.production_db_path)
        try:
            for target in targets:
                for spec in specs:
                    layer = str(spec.get("source_layer") or "").strip()
                    if layer == "source_context":
                        observations.append(
                            self._source_context_observation(source_store, target, spec)
                        )
                    elif layer == "production_context":
                        observations.append(
                            self._production_context_observation(production_store, target, spec)
                        )
                    elif layer == "imdb_history":
                        observations.append(
                            self._imdb_history_observation(source_store, target, spec)
                        )
                    else:
                        raise ProxyHypothesisValidationError(
                            f"Materializer пока не поддерживает source_layer={layer!r}; "
                            "pre_release_public_signal требует отдельного датированного adapter"
                        )
        finally:
            production_store.close()
            source_store.close()

        public_targets = [
            {
                "target_id": item["target_id"],
                "target_year": item["target_year"],
                "cutoff_at": item["cutoff_at"],
                "release_at": item["release_at"],
            }
            for item in targets
        ]
        return {
            "version": MATERIALIZER_VERSION,
            "plan_fingerprint_sha256": plan_fingerprint,
            "targets": public_targets,
            "observations": observations,
            "materializer": {
                "version": MATERIALIZER_VERSION,
                "supported_source_layers": [
                    "imdb_history",
                    "source_context",
                    "production_context",
                ],
                "pre_release_public_signal_supported": False,
            },
        }

    def _source_context_observation(
        self,
        store: SourceContextStore,
        target: dict[str, Any],
        spec: dict[str, Any],
    ) -> dict[str, Any]:
        target_id = target["target_id"]
        feature_name = str(spec["feature_name"])
        source_layer = str(spec["source_layer"])
        contract = str(spec["temporal_contract"])
        cutoff = _parse_timestamp(target["cutoff_at"], field_name="cutoff_at")
        project_id = self._resolve_project_id(
            store.conn,
            table="source_context_projects",
            target_id=target_id,
            explicit_project_id=target.get("source_project_id"),
        )
        if not project_id:
            return _missing(
                target_id=target_id,
                feature_name=feature_name,
                source_layer=source_layer,
                temporal_contract=contract,
                reason="source_context project не найден",
            )
        features = store.features_as_of(project_id, cutoff)
        if feature_name not in features:
            raise ProxyHypothesisValidationError(
                f"source_context materializer не поддерживает feature={feature_name!r}"
            )

        project = store.conn.execute(
            """
            SELECT adaptation_format, planned_runtime_minutes,
                   planned_episode_count, planned_episode_runtime_minutes,
                   format_known_at
            FROM source_context_projects WHERE project_id = ?
            """,
            [project_id],
        ).fetchone()
        links = store.links_as_of(project_id, cutoff)
        link_known = _max_dt([item.get("known_at") for item in links])

        format_features = {
            "source_format_known",
            "source_planned_runtime_minutes",
            "source_planned_episode_count",
            "source_planned_episode_runtime_minutes",
            "source_planned_total_runtime_minutes",
        }
        is_format = feature_name in format_features or feature_name.startswith(
            "source_adaptation_format_"
        )
        if is_format:
            format_known = project[4] if project else None
            if format_known is None or format_known > cutoff:
                return _missing(
                    target_id=target_id,
                    feature_name=feature_name,
                    source_layer=source_layer,
                    temporal_contract=contract,
                    reason="format/runtime ещё не известен на cutoff",
                )
            raw_required = {
                "source_planned_runtime_minutes": project[1],
                "source_planned_episode_count": project[2],
                "source_planned_episode_runtime_minutes": project[3],
            }
            if feature_name in raw_required and raw_required[feature_name] is None:
                return _missing(
                    target_id=target_id,
                    feature_name=feature_name,
                    source_layer=source_layer,
                    temporal_contract=contract,
                    reason=f"{feature_name} не задан в source_context",
                )
            if feature_name == "source_planned_total_runtime_minutes":
                if project[1] is None and (project[2] is None or project[3] is None):
                    return _missing(
                        target_id=target_id,
                        feature_name=feature_name,
                        source_layer=source_layer,
                        temporal_contract=contract,
                        reason="нет planned runtime или episode_count×episode_runtime",
                    )
            return _available_timestamped(
                target_id=target_id,
                feature_name=feature_name,
                source_layer=source_layer,
                temporal_contract=contract,
                value=features[feature_name],
                provenance_id=f"source_context:{project_id}:format",
                source_timestamp=format_known,
            )

        if not links or link_known is None:
            return _missing(
                target_id=target_id,
                feature_name=feature_name,
                source_layer=source_layer,
                temporal_contract=contract,
                reason="ни один source link не известен на cutoff",
            )

        if feature_name in {
            "source_age_years_mean",
            "source_age_years_min",
            "source_age_years_max",
        } and features.get("source_age_known_ratio", 0.0) <= 0.0:
            return _missing(
                target_id=target_id,
                feature_name=feature_name,
                source_layer=source_layer,
                temporal_contract=contract,
                reason="publication date первоисточника неизвестна",
            )
        if feature_name in {"source_series_size_mean", "source_series_size_max"} and features.get(
            "source_series_size_known_ratio", 0.0
        ) <= 0.0:
            return _missing(
                target_id=target_id,
                feature_name=feature_name,
                source_layer=source_layer,
                temporal_contract=contract,
                reason="series_size первоисточника неизвестен",
            )
        if feature_name == "source_series_position_mean" and not any(
            item.get("series_position") is not None for item in links
        ):
            return _missing(
                target_id=target_id,
                feature_name=feature_name,
                source_layer=source_layer,
                temporal_contract=contract,
                reason="series_position первоисточника неизвестен",
            )

        timestamp = link_known
        if feature_name == "source_creator_count":
            work_ids = [item["work_id"] for item in links]
            creator_row = store.conn.execute(
                """
                SELECT MAX(known_at)
                FROM source_work_creators
                WHERE work_id IN (SELECT * FROM UNNEST(?)) AND known_at <= ?
                """,
                [work_ids, cutoff],
            ).fetchone()
            timestamp = _max_dt([timestamp, creator_row[0] if creator_row else None]) or timestamp

        if contract == "published_at_lte_cutoff":
            publication_dates = [item.get("first_publication_at") for item in links]
            if any(value is None for value in publication_dates):
                return _missing(
                    target_id=target_id,
                    feature_name=feature_name,
                    source_layer=source_layer,
                    temporal_contract=contract,
                    reason="published_at contract нельзя доказать: publication date неизвестна",
                )
            timestamp = _max_dt([timestamp, *publication_dates]) or timestamp

        return _available_timestamped(
            target_id=target_id,
            feature_name=feature_name,
            source_layer=source_layer,
            temporal_contract=contract,
            value=features[feature_name],
            provenance_id=f"source_context:{project_id}:as_of:{_iso(cutoff)}",
            source_timestamp=timestamp,
        )

    def _production_context_observation(
        self,
        store: ProductionContextStore,
        target: dict[str, Any],
        spec: dict[str, Any],
    ) -> dict[str, Any]:
        target_id = target["target_id"]
        feature_name = str(spec["feature_name"])
        source_layer = str(spec["source_layer"])
        contract = str(spec["temporal_contract"])
        cutoff = _parse_timestamp(target["cutoff_at"], field_name="cutoff_at")
        project_id = self._resolve_project_id(
            store.conn,
            table="production_projects",
            target_id=target_id,
            explicit_project_id=target.get("production_project_id"),
        )
        if not project_id:
            return _missing(
                target_id=target_id,
                feature_name=feature_name,
                source_layer=source_layer,
                temporal_contract=contract,
                reason="production_context project не найден",
            )
        features = store.features_as_of(project_id, cutoff)
        if feature_name not in features:
            raise ProxyHypothesisValidationError(
                f"production_context materializer не поддерживает feature={feature_name!r}"
            )

        project = store.conn.execute(
            """
            SELECT franchise_id, shared_universe_id, installment_index, identity_known_at
            FROM production_projects WHERE project_id = ?
            """,
            [project_id],
        ).fetchone()
        if feature_name in {
            "production_franchise_known",
            "production_shared_universe_known",
            "production_installment_index",
        }:
            known_at = project[3] if project else None
            if known_at is None or known_at > cutoff:
                return _missing(
                    target_id=target_id,
                    feature_name=feature_name,
                    source_layer=source_layer,
                    temporal_contract=contract,
                    reason="production identity ещё не известна на cutoff",
                )
            if feature_name == "production_installment_index" and project[2] is None:
                return _missing(
                    target_id=target_id,
                    feature_name=feature_name,
                    source_layer=source_layer,
                    temporal_contract=contract,
                    reason="installment_index неизвестен",
                )
            return _available_timestamped(
                target_id=target_id,
                feature_name=feature_name,
                source_layer=source_layer,
                temporal_contract=contract,
                value=features[feature_name],
                provenance_id=f"production_context:{project_id}:identity",
                source_timestamp=known_at,
            )

        role_map = {
            f"production_{role}_count": role
            for role in (
                "studio",
                "production_company",
                "production_label",
                "producer",
                "creative_lead",
            )
        }
        if feature_name in role_map:
            rows = store.conn.execute(
                """
                SELECT known_at, source_id FROM project_entity_links
                WHERE project_id = ? AND role = ? AND known_at <= ?
                """,
                [project_id, role_map[feature_name], cutoff],
            ).fetchall()
            if not rows:
                return _missing(
                    target_id=target_id,
                    feature_name=feature_name,
                    source_layer=source_layer,
                    temporal_contract=contract,
                    reason=f"нет известных {role_map[feature_name]} links на cutoff",
                )
            timestamp = max(row[0] for row in rows)
            source_ids = ",".join(sorted({str(row[1]) for row in rows}))
            return _available_timestamped(
                target_id=target_id,
                feature_name=feature_name,
                source_layer=source_layer,
                temporal_contract=contract,
                value=features[feature_name],
                provenance_id=f"production_context:{project_id}:entities:{source_ids}",
                source_timestamp=timestamp,
            )

        if feature_name == "production_change_count" or (
            feature_name.startswith("production_") and feature_name.endswith("_count")
            and feature_name.removeprefix("production_").removesuffix("_count")
            in {
                "director_change",
                "writer_change",
                "creative_lead_change",
                "release_date_change",
                "rewrite",
                "reshoot",
                "recut",
                "format_change",
                "scope_change",
                "production_label_change",
            }
        ):
            rows = store.conn.execute(
                """
                SELECT known_at, source_id FROM production_events
                WHERE project_id = ? AND known_at <= ?
                """,
                [project_id, cutoff],
            ).fetchall()
            if not rows:
                return _missing(
                    target_id=target_id,
                    feature_name=feature_name,
                    source_layer=source_layer,
                    temporal_contract=contract,
                    reason="нет известных production events на cutoff",
                )
            timestamp = max(row[0] for row in rows)
            source_ids = ",".join(sorted({str(row[1]) for row in rows}))
            return _available_timestamped(
                target_id=target_id,
                feature_name=feature_name,
                source_layer=source_layer,
                temporal_contract=contract,
                value=features[feature_name],
                provenance_id=f"production_context:{project_id}:events:{source_ids}",
                source_timestamp=timestamp,
            )

        if feature_name == "production_external_consultancy_present" or feature_name == "production_consultancy_count" or feature_name.startswith(
            "production_consultancy_"
        ):
            rows = store.conn.execute(
                """
                SELECT known_at, source_id FROM consultancy_engagements
                WHERE project_id = ? AND known_at <= ?
                """,
                [project_id, cutoff],
            ).fetchall()
            if not rows:
                return _missing(
                    target_id=target_id,
                    feature_name=feature_name,
                    source_layer=source_layer,
                    temporal_contract=contract,
                    reason="нет известных consultancy engagements на cutoff",
                )
            timestamp = max(row[0] for row in rows)
            source_ids = ",".join(sorted({str(row[1]) for row in rows}))
            return _available_timestamped(
                target_id=target_id,
                feature_name=feature_name,
                source_layer=source_layer,
                temporal_contract=contract,
                value=features[feature_name],
                provenance_id=f"production_context:{project_id}:consultancy:{source_ids}",
                source_timestamp=timestamp,
            )

        raise ProxyHypothesisValidationError(
            f"Не определён provenance contract для production feature={feature_name!r}"
        )

    def _imdb_history_observation(
        self,
        source_store: SourceContextStore,
        target: dict[str, Any],
        spec: dict[str, Any],
    ) -> dict[str, Any]:
        target_id = target["target_id"]
        feature_name = str(spec["feature_name"])
        source_layer = str(spec["source_layer"])
        contract = str(spec["temporal_contract"])
        target_year = int(target["target_year"])
        cutoff = _parse_timestamp(target["cutoff_at"], field_name="cutoff_at")

        # Adaptation-specific creative-team history уже имеет отдельный P3 extractor.
        # Для P6 он считается historical proxy: prior project release < target release
        # и source links prior projects должны быть известны на cutoff.
        project_id = self._resolve_project_id(
            source_store.conn,
            table="source_context_projects",
            target_id=target_id,
            explicit_project_id=target.get("source_project_id"),
        )
        if project_id:
            history = SourceTeamHistory(source_store, self.imdb_db_path)
            values = history.features_as_of(project_id, cutoff)
            if feature_name in values:
                prior = history._prior_adaptations(project_id, cutoff)
                years = [
                    int(item["release_at"].year)
                    for item in prior.values()
                    if item.get("release_at") is not None
                    and int(item["release_at"].year) < target_year
                ]
                if not years:
                    return _missing(
                        target_id=target_id,
                        feature_name=feature_name,
                        source_layer=source_layer,
                        temporal_contract=contract,
                        reason="нет prior adaptation project для доказуемого history_year",
                    )
                return _available_history(
                    target_id=target_id,
                    feature_name=feature_name,
                    source_layer=source_layer,
                    temporal_contract=contract,
                    value=values[feature_name],
                    provenance_id=f"source_team_history:{project_id}:as_of:{_iso(cutoff)}",
                    history_year=max(years),
                )

        if not self.imdb_db_path.is_file():
            return _missing(
                target_id=target_id,
                feature_name=feature_name,
                source_layer=source_layer,
                temporal_contract=contract,
                reason=f"IMDb БД не найдена: {self.imdb_db_path}",
            )
        supported = {
            "director_prior_count": ("director", "count"),
            "director_avg_rating": ("director", "rating"),
            "writer_prior_count": ("writer", "count"),
            "writer_avg_rating": ("writer", "rating"),
            "actor_1_prior_count": ("actor_1", "count"),
            "actor_1_avg_rating": ("actor_1", "rating"),
            "actor_2_prior_count": ("actor_2", "count"),
            "actor_2_avg_rating": ("actor_2", "rating"),
            "actor_3_prior_count": ("actor_3", "count"),
            "actor_3_avg_rating": ("actor_3", "rating"),
        }
        if feature_name not in supported:
            raise ProxyHypothesisValidationError(
                f"imdb_history materializer не поддерживает feature={feature_name!r}"
            )
        role, metric = supported[feature_name]
        conn = duckdb.connect(str(self.imdb_db_path), read_only=True)
        try:
            person_id = self._target_person_id(conn, target_id, role)
            if not person_id:
                return _missing(
                    target_id=target_id,
                    feature_name=feature_name,
                    source_layer=source_layer,
                    temporal_contract=contract,
                    reason=f"не удалось разрешить {role} для target",
                )
            rows = self._person_prior_rows(conn, person_id, role, target_year)
        finally:
            conn.close()
        if not rows:
            return _missing(
                target_id=target_id,
                feature_name=feature_name,
                source_layer=source_layer,
                temporal_contract=contract,
                reason=f"у {role} нет prior movie history с годом < target_year",
            )
        history_year = max(int(row[0]) for row in rows)
        ratings = [float(row[1]) for row in rows if row[1] is not None]
        value = float(len({row[2] for row in rows})) if metric == "count" else (
            sum(ratings) / len(ratings) if ratings else None
        )
        if value is None:
            return _missing(
                target_id=target_id,
                feature_name=feature_name,
                source_layer=source_layer,
                temporal_contract=contract,
                reason="prior history есть, но historical rating отсутствует",
            )
        return _available_history(
            target_id=target_id,
            feature_name=feature_name,
            source_layer=source_layer,
            temporal_contract=contract,
            value=value,
            provenance_id=f"imdb_history:{person_id}:before:{target_year}",
            history_year=history_year,
        )

    @staticmethod
    def _target_person_id(
        conn: duckdb.DuckDBPyConnection,
        target_id: str,
        role: str,
    ) -> str | None:
        if role == "director":
            row = conn.execute(
                """
                SELECT nconst FROM title_principals
                WHERE tconst = ? AND category = 'director'
                ORDER BY ordering LIMIT 1
                """,
                [target_id],
            ).fetchone()
            return str(row[0]) if row else None
        if role == "writer":
            row = conn.execute(
                "SELECT nconst FROM title_writers WHERE tconst = ? ORDER BY nconst LIMIT 1",
                [target_id],
            ).fetchone()
            return str(row[0]) if row else None
        if role.startswith("actor_"):
            slot = int(role.split("_")[1])
            row = conn.execute(
                """
                SELECT nconst FROM (
                    SELECT nconst, ROW_NUMBER() OVER (ORDER BY ordering) AS rn
                    FROM title_principals
                    WHERE tconst = ? AND category IN ('actor', 'actress')
                ) WHERE rn = ?
                """,
                [target_id, slot],
            ).fetchone()
            return str(row[0]) if row else None
        return None

    @staticmethod
    def _person_prior_rows(
        conn: duckdb.DuckDBPyConnection,
        person_id: str,
        role: str,
        target_year: int,
    ) -> list[tuple]:
        if role == "writer":
            return conn.execute(
                """
                SELECT DISTINCT TRY_CAST(b.startYear AS INTEGER),
                       TRY_CAST(r.averageRating AS DOUBLE), b.tconst
                FROM title_writers w
                JOIN title_basics b ON b.tconst = w.tconst
                LEFT JOIN title_ratings r ON r.tconst = b.tconst
                WHERE w.nconst = ? AND b.titleType = 'movie'
                  AND TRY_CAST(b.startYear AS INTEGER) < ?
                """,
                [person_id, target_year],
            ).fetchall()
        categories = ["director"] if role == "director" else ["actor", "actress"]
        return conn.execute(
            """
            SELECT DISTINCT TRY_CAST(b.startYear AS INTEGER),
                   TRY_CAST(r.averageRating AS DOUBLE), b.tconst
            FROM title_principals p
            JOIN title_basics b ON b.tconst = p.tconst
            LEFT JOIN title_ratings r ON r.tconst = b.tconst
            WHERE p.nconst = ? AND p.category IN (SELECT * FROM UNNEST(?))
              AND b.titleType = 'movie'
              AND TRY_CAST(b.startYear AS INTEGER) < ?
            """,
            [person_id, categories, target_year],
        ).fetchall()
