from __future__ import annotations

import unittest
from pathlib import Path
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
    conn = object()
    model_path = Path("/tmp/models/releases/generation-test/model.cbm")
    db_path = Path("/tmp/imdb.duckdb")
    metadata = {
        "schema_version": 15,
        "feature_names": ["startYear", "writer_avg_rating", "writer_id"],
        "categorical_features": ["writer_id"],
        "test_abs_error_quantiles": {"q80": 1.1},
        "quality_gate": {"passed": True, "reason": "within_mae_gate"},
        "model_size_bytes": 8393184,
        "training_mode": "temporal_validation_then_stable_refit",
        "published_model_fit": "all_stable_targets",
        "published_metrics_source": "separate_temporal_validation_model",
        "refit_rows": 300000,
        "refit_year_from": 1900,
        "refit_year_to": 2025,
    }

    def quality_summary(self):
        return {
            "mae": 1.02,
            "rmse": 1.34,
            "r2": 0.28,
            "test_year_from": 2024,
            "test_year_to": 2025,
            "test_rows": 13993,
            "train_year_from": 1900,
            "train_year_to": 2023,
            "train_rows": 298298,
        }

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
            "input_resolution": {
                "resolved_directors": ["Christopher Nolan"],
                "resolved_actors": ["Leonardo DiCaprio"],
                "writer": None,
                "title": None,
            },
        }


FAKE_PEOPLE = {
    "version": 1,
    "source": "rating_feature_resolution",
    "directors": [
        {
            "requested_input": "Christopher Nolan",
            "canonical_name": "Christopher Nolan",
            "imdb_id": "nm0634240",
            "resolved": True,
            "known": True,
            "works_count": 12,
            "prior_count": 12,
            "avg_rating": 7.8,
        }
    ],
    "writer": {
        "requested_input": "Jonathan Nolan",
        "canonical_name": "Jonathan Nolan",
        "imdb_id": "nm0634300",
        "resolved": True,
        "known": True,
        "works_count": 8,
        "prior_count": 8,
        "avg_rating": 8.0,
    },
    "cast": [
        {
            "requested_input": "Leonardo DiCaprio",
            "canonical_name": "Leonardo DiCaprio",
            "imdb_id": "nm0000138",
            "resolved": True,
            "known": True,
            "works_count": 20,
            "prior_count": 20,
            "avg_rating": 7.5,
        }
    ],
}

FAKE_RUNTIME = {
    "version": 1,
    "model": {
        "generation": "generation-test",
        "schema_version": 15,
        "published_metrics_source": "separate_temporal_validation_model",
    },
    "database": {
        "physical_fingerprint_sha256": "d" * 64,
        "logical_fingerprint_sha256": "e" * 64,
        "freshness_manifest_verified": True,
    },
    "runtime_fingerprint_sha256": "f" * 64,
}


class ApiPredictionOutputTests(unittest.TestCase):
    def setUp(self):
        self.client = api.app.test_client()

    def test_predict_exposes_base_contributions_profile_and_runtime(self):
        payload = {
            "title": "Inception",
            "director": "Christopher Nolan",
            "writer": "Jonathan Nolan",
            "genres": ["Action", "Sci-Fi"],
            "actors": ["Leonardo DiCaprio"],
            "year": 2010,
            "runtime": 148,
            "synopsis": "Герой входит в чужие сны и должен выполнить почти невозможное задание.",
            "source": {"type": "original", "format": "feature_film"},
        }
        fake_profile = {
            "method": "pre_release_profile_v3",
            "potential": {"level": "high_upside"},
            "data_coverage": {"percent": 82, "level": "high"},
            "likely_strengths": [{"key": "direction"}],
            "likely_weaknesses": [{"key": "genre"}],
        }

        with (
            patch.object(api, "_ensure_engine", return_value=_FakeEngine()),
            patch.object(api, "_generation", "generation-test"),
            patch.object(
                api,
                "build_prediction_people_context",
                return_value=FAKE_PEOPLE,
            ),
            patch.object(
                api,
                "build_resolved_pre_release_profile",
                return_value=fake_profile,
            ),
            patch.object(api, "build_runtime_identity", return_value=FAKE_RUNTIME),
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
        self.assertEqual(body["resolved_people"]["directors"][0]["imdb_id"], "nm0634240")
        self.assertEqual(body["runtime"]["runtime_fingerprint_sha256"], "f" * 64)
        self.assertEqual(body["pre_release_profile"]["potential"]["level"], "high_upside")
        self.assertEqual(body["pre_release_profile"]["data_coverage"]["percent"], 82)
        self.assertEqual(body["pre_release_profile"]["source"]["type"], "original")

    def test_predict_validates_synopsis_and_source(self):
        base = {
            "title": "Inception",
            "director": "Christopher Nolan",
            "writer": "Jonathan Nolan",
            "genres": ["Action", "Sci-Fi"],
            "actors": [],
            "year": 2010,
            "runtime": 148,
        }
        self.assertEqual(
            self.client.post("/predict", json=dict(base, synopsis="x" * 5001)).status_code,
            400,
        )
        self.assertEqual(
            self.client.post("/predict", json=dict(base, source=["book"])).status_code,
            400,
        )

    def test_model_info_exposes_reproducibility_metadata(self):
        with (
            patch.object(api, "_ensure_engine", return_value=_FakeEngine()),
            patch.object(api, "_generation", "generation-test"),
            patch.object(api, "build_runtime_identity", return_value=FAKE_RUNTIME),
        ):
            response = self.client.get("/model-info")

        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["generation"], "generation-test")
        self.assertEqual(body["schema_version"], 15)
        self.assertIn("writer_avg_rating", body["feature_names"])
        self.assertEqual(body["quality"]["mae"], 1.02)
        self.assertTrue(body["uncertainty_available"])
        self.assertTrue(body["quality_gate"]["passed"])
        self.assertEqual(body["model_size_bytes"], 8393184)
        self.assertEqual(body["training_mode"], "temporal_validation_then_stable_refit")
        self.assertEqual(body["published_model_fit"], "all_stable_targets")
        self.assertEqual(
            body["published_metrics_source"],
            "separate_temporal_validation_model",
        )
        self.assertEqual(body["runtime"]["model"]["generation"], "generation-test")

    def test_catalog_ratings_endpoint_returns_current_values(self):
        with (
            patch.object(api, "_ensure_engine", return_value=_FakeEngine()),
            patch.object(api, "_generation", "generation-test"),
        ):
            response = self.client.post(
                "/catalog/ratings", json={"imdb_ids": ["tt0816692"]}
            )
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertEqual(body["items"][0]["rating"], 8.7)
        self.assertEqual(body["items"][0]["num_votes"], 2200000)

    def test_search_endpoints_proxy_catalog(self):
        with (
            patch.object(api, "_ensure_engine", return_value=_FakeEngine()),
            patch.object(api, "_generation", "generation-test"),
        ):
            movies = self.client.get("/search/movies?q=Interstellar")
            people = self.client.get("/search/people?q=Nolan&role=director")
            writers = self.client.get("/search/people?q=Nolan&role=writer")
        self.assertEqual(movies.status_code, 200)
        self.assertEqual(movies.get_json()["items"][0]["imdb_id"], "tt0816692")
        self.assertEqual(people.status_code, 200)
        self.assertEqual(people.get_json()["items"][0]["imdb_id"], "nm0634240")
        self.assertEqual(writers.status_code, 200)


if __name__ == "__main__":
    unittest.main()
