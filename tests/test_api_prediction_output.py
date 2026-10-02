from __future__ import annotations

import unittest
from unittest.mock import patch

import api


class _FakeEngine:
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

        with patch.object(api, "_ensure_engine", return_value=_FakeEngine()):
            response = self.client.post("/predict", json=payload)

        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["rating"], 7.31)
        self.assertEqual(body["base"], 6.11)
        self.assertIn("director_avg_rating", body["contributions"])


if __name__ == "__main__":
    unittest.main()
