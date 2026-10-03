from __future__ import annotations

from typing import List, Optional, Union

from src.creative_team_features import (
    CREATIVE_TEAM_FEATURE_NAMES,
    fetch_person_creative_context,
)
from src.kinovanga import KinoVanga as BaseKinoVanga


class KinoVanga(BaseKinoVanga):
    """KinoVanga с P2 Creative Team-признаками schema v7.

    Старые модели остаются совместимыми: если их metadata не содержит новых
    feature names, подготовка признаков полностью делегируется базовому классу.
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
        if not feature_set.intersection(CREATIVE_TEAM_FEATURE_NAMES):
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

        values = {
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

        for name, value in values.items():
            if name in feature_set:
                X[0, feature_names.index(name)] = float(value)
        return X
