from __future__ import annotations

import unittest
from unittest.mock import patch

import api


class _FakeCatalog:
    def search_movies(self, query, *, limit=8, year=None):
        return [{"imdb_id": "tt0816692", "title": "Interstellar"}]

    def search_people(self, query, *, role, limit=8):
        return [{"imdb_id": "nm0634240", "name": "Christopher Nolan", "role": role}]

    def current_ratings(self, imdb_ids):
        return [
            {
                "imdb_id": "tt0816692",
                "title": "Interstellar",
                "year": 2014,
                "rating": 8.7,
                "num_votes": 2200000,
            }
        ]


class _FakeEngine:
    catalog = _FakeCatalog()

    def predict(self, **kwargs):
        return {
            "rating": 7.31,
            "base": 6.11,
            "explanation": "Рейтинг сформирован за счёт: director_avg_rating: +0.67",
            "contributions": {
                "director_avg_rating": 0.67,
                "genres_combined": -0.43,
            },
            "uncertainty": {
                "lower": 6.2,
                "upper": 8.4,
                "margin": 1.1,
                "coverage": 0.8,
                "method": "temporal_holdout_absolute_error",
            },
            "quality": {
                "mae": 1.02,
                "rmse": 1.34,
                "r2": 0.28,
                "test_year_from": 2024,
                "test_year_to": 2025,
                "test_rows": 13993,
            },
        }


class ApiPredictionOutputTests(unittest.TestCase):
    def setUp(self):
        self.client = api.app.test_client()

    def test_predict_exposes_base_and_contributions(self):
        payload = {
            "title": "Inception",
            "director": "Christopher Nolan",
            "genres": ["Action", "Sci-Fi"],
            "actors": ["Leonardo DiCaprio"],
            "year": 2010,
            "runtime": 148,
        }

        with (
            patch.object(api, "_ensure_engine", return_value=_FakeEngine()),
            patch.object(api, "_generation", "generation-test"),
        ):
            response = self.client.post("/predict", json=payload)

        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["rating"], 7.31)
        self.assertEqual(body["base"], 6.11)
        self.assertEqual(body["generation"], "generation-test")
        self.assertEqual(body["uncertainty"]["coverage"], 0.8)
        self.assertEqual(body["quality"]["mae"], 1.02)
        self.assertIn("director_avg_rating", body["contributions"])

    def test_catalog_ratings_endpoint_returns_current_values(self):
        with (
            patch.object(api, "_ensure_engine", return_value=_FakeEngine()),
            patch.object(api, "_generation", "generation-test"),
        ):
            response = self.client.post(
                "/catalog/ratings",
                json={"imdb_ids": ["tt0816692"]},
            )

        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["items"][0]["rating"], 8.7)
        self.assertEqual(body["items"][0]["num_votes"], 2200000)

    def test_search_endpoints_proxy_catalog(self):
        with (
            patch.object(api, "_ensure_engine", return_value=_FakeEngine()),
            patch.object(api, "_generation", "generation-test"),
        ):
            movies = self.client.get("/search/movies?q=Interstellar")
            people = self.client.get("/search/people?q=Nolan&role=director")

        self.assertEqual(movies.status_code, 200)
        self.assertEqual(movies.get_json()["items"][0]["imdb_id"], "tt0816692")
        self.assertEqual(people.status_code, 200)
        self.assertEqual(people.get_json()["items"][0]["imdb_id"], "nm0634240")


if __name__ == "__main__":
    unittest.main()
