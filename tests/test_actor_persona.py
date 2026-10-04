from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from src.actor_persona import ActorPersonaStore


class ActorPersonaTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "actor_persona.duckdb"
        self.store = ActorPersonaStore(self.path)
        self.store.add_source(
            {
                "source_id": "manual",
                "provider": "manual",
                "retrieved_at": "2026-01-01T00:00:00Z",
                "usage_basis": "structured_annotation",
            }
        )
        self.store.upsert_character(
            {"character_id": "char-1", "canonical_name": "Agent X"}
        )

    def tearDown(self) -> None:
        self.store.close()
        self.tmp.cleanup()

    def _appearance(self, **overrides):
        payload = {
            "actor_id": "nm1",
            "work_id": "tt1",
            "character_id": "char-1",
            "character_name": "Agent X",
            "work_release_at": "2020-01-01T00:00:00Z",
            "known_at": "2019-01-01T00:00:00Z",
            "role_function": "antagonist",
            "meta_role_type": "ordinary",
            "genres": ["Action", "Sci-Fi"],
            "archetypes": ["relentless pursuer"],
            "iconic": True,
            "source_id": "manual",
            "confidence": 1.0,
        }
        payload.update(overrides)
        return payload

    def test_future_role_does_not_leak_into_snapshot(self) -> None:
        self.store.add_role_appearance(self._appearance())
        self.store.add_role_appearance(
            self._appearance(
                appearance_id="future",
                work_id="tt2",
                work_release_at="2028-01-01T00:00:00Z",
                known_at="2027-01-01T00:00:00Z",
                genres=["Comedy"],
            )
        )
        snapshot = self.store.persona_snapshot_as_of("nm1", "2026-01-01T00:00:00Z")
        self.assertEqual(snapshot["works_count"], 1)
        self.assertNotIn("comedy", snapshot["genres"])

    def test_character_reuse_and_meta_role_are_explicit(self) -> None:
        self.store.add_role_appearance(self._appearance())
        features = self.store.candidate_features_as_of(
            {
                "actor_id": "nm1",
                "character_id": "char-1",
                "genres": ["Action"],
                "archetypes": ["relentless pursuer"],
                "meta_role_type": "iconic_character_cameo",
            },
            "2026-01-01T00:00:00Z",
        )
        self.assertEqual(features["iconic_character_reuse"], 1.0)
        self.assertEqual(features["self_portrayal"], 0.0)
        self.assertGreater(features["role_persona_match"], 0.0)
        self.assertFalse(features["network_required_for_inference"])

    def test_self_portrayal_and_inversion(self) -> None:
        for idx in range(3):
            self.store.add_role_appearance(
                self._appearance(
                    appearance_id=f"a-{idx}",
                    work_id=f"tt-{idx}",
                    work_release_at=f"202{idx}-01-01T00:00:00Z",
                    known_at=f"201{9+idx}-01-01T00:00:00Z",
                )
            )
        features = self.store.candidate_features_as_of(
            {
                "actor_id": "nm1",
                "genres": ["Comedy"],
                "archetypes": ["lovable fool"],
                "meta_role_type": "self_portrayal",
            },
            "2026-01-01T00:00:00Z",
        )
        self.assertEqual(features["self_portrayal"], 1.0)
        self.assertEqual(features["role_persona_match"], 0.0)
        self.assertEqual(features["role_persona_inversion"], 1.0)

    def test_ensemble_features(self) -> None:
        self.store.add_role_appearance(self._appearance())
        result = self.store.ensemble_features_as_of(
            [
                {"actor_id": "nm1", "genres": ["Action"], "archetypes": [], "meta_role_type": "meta_ensemble_casting"},
                {"actor_id": "nm2", "genres": [], "archetypes": [], "meta_role_type": "ordinary"},
            ],
            "2026-01-01T00:00:00Z",
        )
        self.assertEqual(result["cast_size"], 2)
        self.assertEqual(result["meta_cast_density"], 0.5)
        self.assertEqual(result["known_history_ratio"], 0.5)


if __name__ == "__main__":
    unittest.main()
