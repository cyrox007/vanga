from __future__ import annotations

from typing import List, Optional, Union

from src.creative_team_features import (
    CREATIVE_TEAM_FEATURE_NAMES,
    fetch_person_creative_context,
)
from src.director_actor_features import (
    ACTOR_SLOTS,
    DIRECTOR_ACTOR_PAIR_FEATURE_NAMES,
    fetch_director_actor_pair_context,
)
from src.pair_features import (
    DIRECTOR_WRITER_PAIR_FEATURE_NAMES,
    fetch_director_writer_pair_context,
)
from src.trend_features import (
    CREATIVE_TREND_FEATURE_NAMES,
    fetch_person_recent_trend,
)
from src.kinovanga import KinoVanga as BaseKinoVanga


class KinoVanga(BaseKinoVanga):
    """KinoVanga с P2 Creative Team-признаками schema v7-v10.

    Старые модели остаются совместимыми: дополнительные запросы выполняются
    только для feature names, которые реально присутствуют в metadata активной
    модели.
    """

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
    ):
        X = super()._prepare_features(
            year=year,
            runtime=runtime,
            genres=genres,
            director=director,
            writer=writer,
            actors=actors,
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
        )
        if not feature_set.intersection(extended_features):
            return X

        director_people = (
            self._get_people_info(
                [director],
                before_year=int(year),
                role="director",
            )
            if director
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

        director_info = director_people.get(director, {}) if director else {}
        writer_info = writer_people.get(writer, {}) if writer else {}
        director_id = director_info.get("nconst") or "Unknown"
        writer_id = writer_info.get("nconst") or "Unknown"

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
            actor_names = [
                str(name).strip()
                for name in (actors or [])[: len(ACTOR_SLOTS)]
                if str(name).strip()
            ]
            actor_people = (
                self._get_people_info(
                    actor_names,
                    before_year=int(year),
                    role="actor",
                )
                if actor_names
                else {}
            )

            for slot in ACTOR_SLOTS:
                actor_name = (
                    actor_names[slot - 1]
                    if slot - 1 < len(actor_names)
                    else None
                )
                actor_info = actor_people.get(actor_name, {}) if actor_name else {}
                actor_id = actor_info.get("nconst") or "Unknown"
                pair_context = fetch_director_actor_pair_context(
                    self.conn,
                    director_nconst=director_id,
                    actor_nconst=actor_id,
                    before_year=int(year),
                )
                values.update(
                    {
                        f"director_actor_{slot}_pair_avg_rating": pair_context[
                            "avg_rating"
                        ],
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

        for name, value in values.items():
            if name in feature_set:
                X[0, feature_names.index(name)] = float(value)
        return X
