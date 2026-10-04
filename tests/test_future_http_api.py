from __future__ import annotations

import unittest
from unittest.mock import patch

import api


class _FakeRegionalService:
    def catalog_as_of(
        self,
        cutoff,
        *,
        territory,
        from_at=None,
        to_at=None,
        include_conflicts=True,
    ):
        return {
            "version": 2,
            "cutoff_at": "2026-10-04T12:00:00+00:00",
            "territory": "iso3166:de",
            "territory_policy": "explicit_iso3166_no_worldwide_fallback",
            "from_at": "2026-10-04T12:00:00+00:00",
            "to_at": None,
            "items": [
                {
                    "project_id": "film-a",
                    "canonical_title": "Future Film",
                    "regional_release_resolution": "resolved",
                    "release_at": "2027-05-20T00:00:00+00:00",
                }
            ],
            "network_required_for_inference": False,
            "catalog_fingerprint_sha256": "a" * 64,
        }


class _FakeRegionalBuilder:
    def build(self, project_id, cutoff, *, territory, **kwargs):
        return {
            "version": 2,
            "project_id": project_id,
            "cutoff_at": "2026-10-04T12:00:00+00:00",
            "prediction_ready": True,
            "blockers": [],
            "warnings": [],
            "target_territory": "iso3166:de",
            "territory_policy": "explicit_iso3166_no_worldwide_fallback",
            "request": {
                "title": "Future Film",
                "director": "Director",
                "directors": ["Director"],
                "writer": "Writer",
                "year": 2027,
                "runtime": 120,
                "genres": ["Drama"],
                "actors": [],
                "source": {},
                "imdb_id": "tt1234567",
            },
            "regional_release_snapshot": {
                "project_id": project_id,
                "canonical_territory": "iso3166:de",
                "resolved": True,
                "conflict": False,
            },
            "network_required": False,
            "payload_fingerprint_sha256": "b" * 64,
        }


class FutureHttpApiTests(unittest.TestCase):
    def setUp(self):
        self.client = api.app.test_client()

    def test_catalog_requires_explicit_iso_territory(self):
        missing = self.client.get("/future/catalog?cutoff=2026-10-04T12:00:00Z")
        worldwide = self.client.get(
            "/future/catalog?cutoff=2026-10-04T12:00:00Z&territory=worldwide"
        )
        self.assertEqual(missing.status_code, 400)
        self.assertEqual(worldwide.status_code, 400)
        self.assertFalse(missing.get_json()["ok"])
        self.assertIn("ISO 3166", worldwide.get_json()["error"])

    def test_catalog_returns_regional_contract_without_loading_model(self):
        with (
            patch(
                "src.future_http_api.FutureRegionalReleaseService",
                return_value=_FakeRegionalService(),
            ),
            patch.object(api, "_ensure_engine", side_effect=AssertionError("model must not load")),
        ):
            response = self.client.get(
                "/future/catalog?cutoff=2026-10-04T12:00:00Z&territory=DE&include_conflicts=0"
            )

        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["territory"], "iso3166:de")
        self.assertEqual(body["items"][0]["regional_release_resolution"], "resolved")
        self.assertFalse(body["network_required_for_inference"])

    def test_prediction_payload_requires_project_cutoff_and_territory(self):
        response = self.client.post(
            "/future/prediction-payload",
            json={"project_id": "film-a", "cutoff": "2026-10-04T12:00:00Z"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("territory", response.get_json()["error"])

    def test_prediction_payload_returns_ready_request_and_provenance(self):
        with (
            patch(
                "src.future_http_api.RegionalTemporalFuturePredictionPayloadBuilder",
                return_value=_FakeRegionalBuilder(),
            ),
            patch.object(api, "_ensure_engine", side_effect=AssertionError("model must not load")),
        ):
            response = self.client.post(
                "/future/prediction-payload",
                json={
                    "project_id": "film-a",
                    "cutoff": "2026-10-04T12:00:00Z",
                    "territory": "DE",
                    "runtime_override": 120,
                    "genres_override": ["Drama"],
                },
            )

        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertTrue(body["ok"])
        self.assertTrue(body["prediction_ready"])
        self.assertEqual(body["target_territory"], "iso3166:de")
        self.assertEqual(body["request"]["year"], 2027)
        self.assertEqual(body["regional_release_snapshot"]["canonical_territory"], "iso3166:de")

    def test_prediction_payload_can_return_blocked_state_as_200(self):
        blocked = _FakeRegionalBuilder()

        def build(*args, **kwargs):
            result = blocked.build(*args, **kwargs)
            result["prediction_ready"] = False
            result["blockers"] = ["regional_release_date_conflict"]
            result["request"] = None
            result["regional_release_snapshot"]["conflict"] = True
            return result

        blocked.build = build
        with patch(
            "src.future_http_api.RegionalTemporalFuturePredictionPayloadBuilder",
            return_value=blocked,
        ):
            response = self.client.post(
                "/future/prediction-payload",
                json={
                    "project_id": "film-a",
                    "cutoff": "2026-10-04T12:00:00Z",
                    "territory": "DE",
                },
            )

        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertFalse(body["prediction_ready"])
        self.assertIsNone(body["request"])
        self.assertIn("regional_release_date_conflict", body["blockers"])

    def test_boolean_query_validation_is_strict(self):
        response = self.client.get(
            "/future/catalog?cutoff=2026-10-04T12:00:00Z&territory=DE&include_conflicts=maybe"
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("boolean", response.get_json()["error"])


if __name__ == "__main__":
    unittest.main()
