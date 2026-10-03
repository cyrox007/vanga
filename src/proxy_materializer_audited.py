from __future__ import annotations

from typing import Any

from src.proxy_hypotheses import ProxyHypothesisValidationError
from src.proxy_materializers import ProxySourceMaterializer, _missing, _parse_timestamp
from src.source_context import SourceContextStore
from src.source_team_history import SourceTeamHistory


class AuditedProxySourceMaterializer(ProxySourceMaterializer):
    """Публичный P6 facade с дополнительными anti-leakage guards.

    Low-level `ProxySourceMaterializer` умеет извлекать значения из stores.
    Этот facade добавляет ограничения, которые зависят именно от preregistered
    P6 temporal contract и поэтому не должны менять P3 extractors глобально.
    """

    def _imdb_history_observation(
        self,
        source_store: SourceContextStore,
        target: dict[str, Any],
        spec: dict[str, Any],
    ) -> dict[str, Any]:
        feature_name = str(spec["feature_name"])
        target_year = int(target["target_year"])

        if feature_name.startswith("source_"):
            cutoff = _parse_timestamp(target["cutoff_at"], field_name="cutoff_at")
            project_id = self._resolve_project_id(
                source_store.conn,
                table="source_context_projects",
                target_id=target["target_id"],
                explicit_project_id=target.get("source_project_id"),
            )
            if project_id:
                history = SourceTeamHistory(source_store, self.imdb_db_path)
                values = history.features_as_of(project_id, cutoff)
                if feature_name in values:
                    prior = history._prior_adaptations(project_id, cutoff)
                    same_year = sorted(
                        {
                            str(item["imdb_id"])
                            for item in prior.values()
                            if item.get("release_at") is not None
                            and int(item["release_at"].year) >= target_year
                        }
                    )
                    if same_year:
                        return _missing(
                            target_id=target["target_id"],
                            feature_name=feature_name,
                            source_layer=str(spec["source_layer"]),
                            temporal_contract=str(spec["temporal_contract"]),
                            reason=(
                                "adaptation-team extractor видит same-year prior projects "
                                f"({', '.join(same_year)}); history_before_target_year "
                                "нельзя доказать без отдельной strict-year materialization"
                            ),
                        )

        result = super()._imdb_history_observation(source_store, target, spec)
        if result.get("available") and str(spec.get("temporal_contract")) != "history_before_target_year":
            raise ProxyHypothesisValidationError(
                "IMDb historical proxy materialized без history_before_target_year contract"
            )
        return result
