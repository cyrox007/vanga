from __future__ import annotations

import unittest
from unittest.mock import patch

import api


class _FakeCatalog:
    def search_movies(self, query, *, limit=8, year=None):
        return [{"imdb_id": "tt0816692", "title": "Interstellar"}]

    def search_people(self, query, *, role, limit=8):
        return [{"imdb_id": "nm0634240", "name": "Christopher Nolan", "role": role}]


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
        self.assertIn("director_avg_rating", body["contributions"])

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
