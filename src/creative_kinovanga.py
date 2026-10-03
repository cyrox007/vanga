from __future__ import annotations

from typing import List, Optional, Union

from catboost import Pool

from src.cast_pair_features import CAST_PAIR_FEATURE_NAMES, fetch_cast_pair_context
from src.creative_team_features import (
    CREATIVE_TEAM_FEATURE_NAMES,
    fetch_person_creative_context,
)
from src.director_actor_features import (
    ACTOR_SLOTS,
    DIRECTOR_ACTOR_PAIR_FEATURE_NAMES,
    fetch_director_actor_pair_context,
)
from src.director_team_features import (
    DIRECTOR_TEAM_FEATURE_NAMES,
    fetch_director_team_context,
)
from src.dual_role_features import DUAL_ROLE_FEATURE_NAMES, fetch_dual_role_context
from src.full_cast_features import FULL_CAST_FEATURE_NAMES, fetch_full_cast_context
from src.pair_features import (
    DIRECTOR_WRITER_PAIR_FEATURE_NAMES,
    fetch_director_writer_pair_context,
)
from src.team_collaboration_features import (
    TEAM_COLLABORATION_FEATURE_NAMES,
    fetch_team_collaboration_context,
)
from src.trend_features import (
    CREATIVE_TREND_FEATURE_NAMES,
    fetch_person_recent_trend,
)
from src.kinovanga import KinoVanga as BaseKinoVanga


class KinoVanga(BaseKinoVanga):
    """KinoVanga с P2 Creative Team-признаками schema v7-v15."""

    @staticmethod
    def _clean_director_names(
        director: Optional[str],
        directors: Optional[List[str]],
    ) -> list[str]:
        result: list[str] = []
        for value in directors or ([] if director is None else [director]):
            clean = str(value or "").strip()
            if clean and clean not in result:
                result.append(clean)
        if not result and director:
            result.append(str(director).strip())
        return result

    @staticmethod
    def _clean_actor_names(actors: Optional[List[str]]) -> list[str]:
        result: list[str] = []
        for value in actors or []:
            clean = str(value or "").strip()
            if clean and clean not in result:
                result.append(clean)
        return result

    def _prepare_features(
        self,
        year: int,
        runtime: int,
        genres: Union[str, List[str]],
        director: Optional[str] = None,
        writer: Optional[str] = None,
        actors: Optional[List[str]] = None,
        num_votes: Optional[int] = None,
        title: Optional[str] = None,
        directors: Optional[List[str]] = None,
    ):
        director_names = self._clean_director_names(director, directors)
        primary_director = director_names[0] if director_names else director
        actor_names_all = self._clean_actor_names(actors)

        X = super()._prepare_features(
            year=year,
            runtime=runtime,
            genres=genres,
            director=primary_director,
            writer=writer,
            actors=actor_names_all,
            num_votes=num_votes,
            title=title,
        )

        feature_names = list(self.metadata.get("feature_names") or [])
        feature_set = set(feature_names)
        extended_features = (
            set(CREATIVE_TEAM_FEATURE_NAMES)
            .union(DIRECTOR_WRITER_PAIR_FEATURE_NAMES)
            .union(DIRECTOR_ACTOR_PAIR_FEATURE_NAMES)
            .union(CREATIVE_TREND_FEATURE_NAMES)
            .union(DIRECTOR_TEAM_FEATURE_NAMES)
            .union(FULL_CAST_FEATURE_NAMES)
            .union(CAST_PAIR_FEATURE_NAMES)
            .union(DUAL_ROLE_FEATURE_NAMES)
            .union(TEAM_COLLABORATION_FEATURE_NAMES)
        )
        if not feature_set.intersection(extended_features):
            return X

        director_people = (
            self._get_people_info(
                director_names,
                before_year=int(year),
                role="director",
            )
            if director_names
            else {}
        )
        writer_people = (
            self._get_people_info(
                [writer],
                before_year=int(year),
                role="writer",
            )
            if writer
            else {}
        )

        director_info = director_people.get(primary_director, {}) if primary_director else {}
        writer_info = writer_people.get(writer, {}) if writer else {}
        director_id = director_info.get("nconst") or "Unknown"
        writer_id = writer_info.get("nconst") or "Unknown"
        director_ids = [
            director_people.get(name, {}).get("nconst") or "Unknown"
            for name in director_names
        ]

        actor_feature_set = set(DIRECTOR_ACTOR_PAIR_FEATURE_NAMES).union(
            FULL_CAST_FEATURE_NAMES,
            CAST_PAIR_FEATURE_NAMES,
            TEAM_COLLABORATION_FEATURE_NAMES,
        )
        actor_people_all = (
            self._get_people_info(
                actor_names_all,
                before_year=int(year),
                role="actor",
            )
            if actor_names_all and feature_set.intersection(actor_feature_set)
            else {}
        )
        actor_ids = [
            actor_people_all.get(name, {}).get("nconst") or "Unknown"
            for name in actor_names_all
        ]

        values: dict[str, float] = {}
        if feature_set.intersection(CREATIVE_TEAM_FEATURE_NAMES):
            director_context = fetch_person_creative_context(
                self.conn,
                nconst=director_id,
                before_year=int(year),
                role="director",
                genres=genres,
            )
            writer_context = fetch_person_creative_context(
                self.conn,
                nconst=writer_id,
                before_year=int(year),
                role="writer",
                genres=genres,
            )
            values.update(
                {
                    "director_genre_avg_rating": director_context["genre_avg_rating"],
                    "director_genre_prior_count": director_context["genre_prior_count"],
                    "director_recent_avg_rating": director_context["recent_avg_rating"],
                    "writer_genre_avg_rating": writer_context["genre_avg_rating"],
                    "writer_genre_prior_count": writer_context["genre_prior_count"],
                    "writer_recent_avg_rating": writer_context["recent_avg_rating"],
                    "director_is_writer": (
                        1.0
                        if director_id != "Unknown"
                        and writer_id != "Unknown"
                        and director_id == writer_id
                        else 0.0
                    ),
                }
            )

        if feature_set.intersection(DIRECTOR_WRITER_PAIR_FEATURE_NAMES):
            values.update(
                fetch_director_writer_pair_context(
                    self.conn,
                    director_nconst=director_id,
                    writer_nconst=writer_id,
                    before_year=int(year),
                )
            )

        if feature_set.intersection(DIRECTOR_ACTOR_PAIR_FEATURE_NAMES):
            actor_names = actor_names_all[: len(ACTOR_SLOTS)]
            for slot in ACTOR_SLOTS:
                actor_name = actor_names[slot - 1] if slot - 1 < len(actor_names) else None
                actor_info = actor_people_all.get(actor_name, {}) if actor_name else {}
                actor_id = actor_info.get("nconst") or "Unknown"
                pair_context = fetch_director_actor_pair_context(
                    self.conn,
                    director_nconst=director_id,
                    actor_nconst=actor_id,
                    before_year=int(year),
                )
                values.update(
                    {
                        f"director_actor_{slot}_pair_avg_rating": pair_context["avg_rating"],
                        f"director_actor_{slot}_pair_count": pair_context["count"],
                        f"director_actor_{slot}_pair_known": pair_context["known"],
                    }
                )

        if feature_set.intersection(CREATIVE_TREND_FEATURE_NAMES):
            director_trend = fetch_person_recent_trend(
                self.conn,
                nconst=director_id,
                before_year=int(year),
                role="director",
            )
            writer_trend = fetch_person_recent_trend(
                self.conn,
                nconst=writer_id,
                before_year=int(year),
                role="writer",
            )
            values.update(
                {
                    "director_recent_trend": director_trend["trend"],
                    "director_recent_trend_known": director_trend["known"],
                    "writer_recent_trend": writer_trend["trend"],
                    "writer_recent_trend_known": writer_trend["known"],
                }
            )

        if feature_set.intersection(DIRECTOR_TEAM_FEATURE_NAMES):
            values.update(
                fetch_director_team_context(
                    self.conn,
                    director_nconsts=director_ids,
                    before_year=int(year),
                )
            )

        if feature_set.intersection(FULL_CAST_FEATURE_NAMES):
            values.update(
                fetch_full_cast_context(
                    self.conn,
                    actor_nconsts=actor_ids,
                    before_year=int(year),
                    genres=genres,
                )
            )

        if feature_set.intersection(CAST_PAIR_FEATURE_NAMES):
            values.update(
                fetch_cast_pair_context(
                    self.conn,
                    actor_nconsts=actor_ids,
                    before_year=int(year),
                )
            )

        if feature_set.intersection(DUAL_ROLE_FEATURE_NAMES):
            values.update(
                fetch_dual_role_context(
                    self.conn,
                    director_nconsts=director_ids,
                    writer_nconst=writer_id,
                    before_year=int(year),
                )
            )

        if feature_set.intersection(TEAM_COLLABORATION_FEATURE_NAMES):
            values.update(
                fetch_team_collaboration_context(
                    self.conn,
                    director_nconsts=director_ids,
                    writer_nconst=writer_id,
                    actor_nconsts=actor_ids,
                    before_year=int(year),
                )
            )

        for name, value in values.items():
            if name in feature_set:
                X[0, feature_names.index(name)] = float(value)
        return X

    def predict(
        self,
        year,
        runtime,
        genres,
        director=None,
        writer=None,
        actors=None,
        num_votes=None,
        title=None,
        explain=False,
        directors=None,
    ) -> float | dict:
        """Предсказывает рейтинг с multi-director, Full Cast и P2 history."""
        if title:
            from src.logger import setup_logger
            setup_logger(__name__).info(f"Предсказание для фильма: {title} ({year})")

        requested_directors = self._clean_director_names(director, directors)
        primary_director = requested_directors[0] if requested_directors else director
        requested_actors = self._clean_actor_names(actors)

        writer_features_enabled = {
            "writer_id",
            "writer_avg_rating",
        }.issubset(set(self.metadata.get("feature_names", [])))

        resolved_input = self.input_resolver.resolve_inputs(
            title=title,
            director=primary_director,
            writer=writer if writer_features_enabled else None,
            actors=requested_actors,
            year=int(year),
        )
        resolved_title = resolved_input["title"]
        resolved_writer = resolved_input["writer"]
        resolved_actors = list(resolved_input["actors"])
        actor_matches = list((resolved_input.get("matches") or {}).get("actors") or [])
        while len(actor_matches) < len(requested_actors):
            actor_matches.append(None)

        for index in range(3, len(requested_actors)):
            raw_actor = requested_actors[index]
            if not self.input_resolver.needs_resolution(raw_actor):
                continue
            one = self.input_resolver.resolve_inputs(
                title=None,
                director=None,
                writer=None,
                actors=[raw_actor],
                year=int(year),
            )
            one_actors = one.get("actors") or []
            one_matches = (one.get("matches") or {}).get("actors") or []
            if one_actors:
                resolved_actors[index] = one_actors[0]
            if one_matches:
                actor_matches[index] = one_matches[0]

        resolved_directors: list[str] = []
        director_matches: list[dict | None] = []
        for index, name in enumerate(requested_directors):
            if index == 0:
                canonical = resolved_input["director"] or name
                match = (resolved_input.get("matches") or {}).get("director")
            else:
                one = self.input_resolver.resolve_inputs(
                    title=None,
                    director=name,
                    writer=None,
                    actors=[],
                    year=int(year),
                )
                canonical = one["director"] or name
                match = (one.get("matches") or {}).get("director")
            if canonical and canonical not in resolved_directors:
                resolved_directors.append(canonical)
                director_matches.append(match)

        if not resolved_directors and primary_director:
            resolved_directors = [primary_director]
            director_matches = [None]
        resolved_primary = resolved_directors[0] if resolved_directors else None

        X = self._prepare_features(
            year=year,
            runtime=runtime,
            genres=genres,
            director=resolved_primary,
            writer=resolved_writer,
            actors=resolved_actors,
            num_votes=num_votes,
            title=resolved_title,
            directors=resolved_directors,
        )

        expected_features = len(self.metadata["feature_names"])
        if X.shape[1] != expected_features:
            raise ValueError(
                f"Ожидалось {expected_features} признаков, получено {X.shape[1]}. "
                f"Проверьте корректность входных данных."
            )

        rating = float(self.model.predict(X)[0])
        rating = max(0, min(10, rating))
        rounded_rating = round(rating, 2)
        if not explain:
            return rounded_rating

        test_pool = Pool(
            data=X,
            cat_features=self.metadata.get("cat_features_idx", []),
            feature_names=self.metadata["feature_names"],
        )
        shap_values = self.model.get_feature_importance(data=test_pool, type="ShapValues")[0]
        base_value = shap_values[-1]
        contributions = dict(zip(self.metadata["feature_names"], shap_values[:-1]))

        matches = dict(resolved_input.get("matches") or {})
        matches["director"] = director_matches[0] if director_matches else None
        matches["directors"] = director_matches
        matches["resolved_directors"] = resolved_directors
        matches["actors"] = actor_matches
        matches["resolved_actors"] = resolved_actors
        matches["resolved_cast_size"] = len(resolved_actors)

        return {
            "rating": rounded_rating,
            "base": base_value,
            "contributions": contributions,
            "explanation": self._format_explanation(contributions),
            "input_resolution": matches,
            "uncertainty": self.uncertainty_for_rating(rounded_rating),
            "quality": self.quality_summary(),
        }
