from __future__ import annotations

import unittest
from unittest.mock import patch

from src.prediction_context import build_prediction_people_context
from src.pre_release_profile import build_resolved_pre_release_profile


class _FakeEngine:
    def _get_people_info(self, names, *, before_year, role):
        prefix = {"director": "d", "writer": "w", "actor": "a"}[role]
        return {
            name: {
                "nconst": f"nm_{prefix}_{index}",
                "avg_rating": 7.0 + index / 10,
                "works_count": index + 1,
                "prior_count": index + 1,
                "known": True,
            }
            for index, name in enumerate(names)
        }


class PredictionProfileParityTests(unittest.TestCase):
    def test_context_uses_same_resolved_names_as_rating_pipeline(self):
        context = build_prediction_people_context(
            _FakeEngine(),
            year=2027,
            requested_directors=["Кристофер Нолан", "Второй режиссёр"],
            requested_writer="Джонатан Нолан",
            requested_actors=["Актёр один", "Актёр два", "Актёр три", "Актёр четыре"],
            input_resolution={
                "resolved_directors": ["Christopher Nolan", "Second Director"],
                "resolved_actors": ["Actor One", "Actor Two", "Actor Three", "Actor Four"],
                "writer": {"canonical": "Jonathan Nolan", "imdb_id": "nm_writer"},
            },
        )

        self.assertEqual(
            context["resolved_directors"],
            ["Christopher Nolan", "Second Director"],
        )
        self.assertEqual(context["resolved_writer"], "Jonathan Nolan")
        self.assertEqual(len(context["cast"]), 4)
        self.assertEqual(context["directors"][0]["imdb_id"], "nm_d_0")
        self.assertEqual(context["cast"][3]["imdb_id"], "nm_a_3")
        self.assertEqual(
            context["directors"][0]["requested_input"],
            "Кристофер Нолан",
        )

    def test_profile_uses_full_resolved_team_and_keeps_legacy_top3_field(self):
        context = {
            "version": 1,
            "source": "rating_feature_resolution",
            "directors": [
                {
                    "canonical_name": "Director One",
                    "imdb_id": "nm_d1",
                    "prior_count": 3,
                    "avg_rating": 7.5,
                },
                {
                    "canonical_name": "Director Two",
                    "imdb_id": "nm_d2",
                    "prior_count": 2,
                    "avg_rating": 7.0,
                },
            ],
            "writer": {
                "canonical_name": "Writer One",
                "imdb_id": "nm_w1",
                "prior_count": 4,
                "avg_rating": 7.8,
            },
            "cast": [
                {
                    "canonical_name": f"Actor {index}",
                    "imdb_id": f"nm_a{index}",
                    "prior_count": index,
                    "avg_rating": 7.0,
                }
                for index in range(1, 5)
            ],
        }
        legacy = {
            "method": "pre_release_profile_v2",
            "potential": {"level": "promising"},
            "data_coverage": {},
            "synopsis": {},
            "dimensions": [],
            "likely_strengths": [],
            "likely_weaknesses": [],
            "validated_target": False,
        }
        with patch(
            "src.pre_release_profile.build_pre_release_profile",
            return_value=legacy,
        ):
            profile = build_resolved_pre_release_profile(
                conn=object(),
                year=2027,
                runtime=130,
                requested_directors=["Режиссёр один", "Режиссёр два"],
                requested_writer="Сценарист",
                requested_actors=["А1", "А2", "А3", "А4"],
                model_people_context=context,
                synopsis="Достаточный синопсис для диагностического слоя.",
                rating=7.2,
                uncertainty={"lower": 6.0, "upper": 8.1},
                contributions={},
            )

        coverage = profile["data_coverage"]
        self.assertEqual(profile["method"], "pre_release_profile_v3")
        self.assertTrue(profile["input_parity"]["uses_prediction_resolution"])
        self.assertEqual(profile["input_parity"]["resolved_directors"], ["Director One", "Director Two"])
        self.assertEqual(len(coverage["director_team"]), 2)
        self.assertEqual(len(coverage["full_cast"]), 4)
        self.assertEqual(len(coverage["actors"]), 3)
        self.assertEqual(coverage["contract"], "resolved_full_team_v3")
        self.assertEqual(coverage["full_cast_known_ratio"], 1.0)
        self.assertEqual(coverage["director_team_known_ratio"], 1.0)
        self.assertEqual(coverage["provided_people_count"], 7)


if __name__ == "__main__":
    unittest.main()
