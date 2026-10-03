from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import duckdb

from src.adaptation_analysis import (
    AdaptationAnalysisStore,
    AdaptationAnalyzer,
    AdaptationCase,
    AdaptationSource,
    AdaptationValidationError,
    load_case_seed_from_enrichment,
)


class AdaptationAnalysisTests(unittest.TestCase):
    def sample_payload(self) -> dict:
        return {
            "case_id": "tt1234567:default",
            "imdb_id": "tt1234567",
            "film_title": "Demo Adaptation",
            "film_year": 2024,
            "source_work_id": "Q100",
            "source_title": "Demo Novel",
            "source_type": "novel",
            "adaptation_format": "film",
            "sources": [
                {
                    "source_id": "film-ru",
                    "kind": "film_summary",
                    "language": "ru",
                    "title": "Пересказ фильма",
                    "text": "Краткий пересказ фильма.",
                    "copyright_mode": "summary",
                },
                {
                    "source_id": "source-en",
                    "kind": "source_summary",
                    "language": "en",
                    "title": "Source summary",
                    "text": "A short summary of the source work.",
                    "copyright_mode": "summary",
                },
                {
                    "source_id": "critic-red",
                    "kind": "expert_review",
                    "language": "ru",
                    "title": "Экспертный разбор",
                    "creator": "critic",
                    "url": "https://example.test/review",
                    "locator": "12:30-13:10",
                    "copyright_mode": "reference",
                },
            ],
            "annotations": [
                {
                    "annotation_id": "obs-lore",
                    "layer": "observation",
                    "dimension": "worldbuilding",
                    "change_type": "removed",
                    "claim": "Из адаптации удалён важный блок устройства мира.",
                    "severity": 0.8,
                    "confidence": 0.95,
                    "polarity": 0,
                    "source_id": "film-ru",
                    "tags": ["lore_cut"],
                },
                {
                    "annotation_id": "consequence-motivation",
                    "layer": "structural_consequence",
                    "dimension": "motivation",
                    "change_type": "removed",
                    "claim": "После удаления контекста мотивация героя объяснена хуже.",
                    "severity": 0.7,
                    "confidence": 0.8,
                    "polarity": -0.5,
                    "parent_id": "obs-lore",
                    "tags": ["motivation_loss"],
                },
                {
                    "annotation_id": "expert-harm",
                    "layer": "expert_interpretation",
                    "dimension": "worldbuilding",
                    "change_type": "removed",
                    "claim": "Эксперт считает сокращение мира неудачным.",
                    "severity": 0.75,
                    "confidence": 0.9,
                    "polarity": -0.9,
                    "source_id": "critic-red",
                    "evidence_locator": "12:30-13:10",
                    "parent_id": "obs-lore",
                    "tags": ["expert_negative"],
                },
            ],
        }

    def test_case_keeps_fact_consequence_and_opinion_separate(self):
        case = AdaptationCase.from_dict(self.sample_payload())

        layers = {item.layer for item in case.annotations}
        self.assertEqual(
            layers,
            {
                "observation",
                "structural_consequence",
                "expert_interpretation",
            },
        )
        self.assertEqual(case.source_work_id, "Q100")

    def test_expert_reference_does_not_store_full_review_text(self):
        with self.assertRaises(AdaptationValidationError):
            AdaptationSource.from_dict(
                {
                    "source_id": "critic",
                    "kind": "expert_review",
                    "url": "https://example.test/review",
                    "text": "Полный текст чужого обзора",
                    "copyright_mode": "reference",
                }
            )

    def test_features_are_retrospective_and_include_structural_support(self):
        case = AdaptationCase.from_dict(self.sample_payload())

        features = AdaptationAnalyzer.features(case)

        self.assertTrue(all(key.startswith("retro_adapt_") for key in features))
        self.assertEqual(features["retro_adapt_annotation_count"], 3)
        self.assertEqual(features["retro_adapt_structural_count"], 2)
        self.assertEqual(features["retro_adapt_expert_count"], 1)
        self.assertAlmostEqual(
            features["retro_adapt_worldbuilding_severity"],
            0.8,
            places=4,
        )
        self.assertAlmostEqual(
            features["retro_adapt_worldbuilding_expert_polarity"],
            -0.9,
            places=4,
        )
        self.assertEqual(
            features["retro_adapt_expert_structural_support_ratio"],
            1.0,
        )
        self.assertEqual(features["retro_adapt_causal_chain_depth"], 1)

    def test_store_replaces_case_and_persists_feature_snapshot(self):
        case = AdaptationCase.from_dict(self.sample_payload())
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "adaptation.duckdb"
            store = AdaptationAnalysisStore(db_path)
            try:
                generated = store.replace_case(case)
                loaded = store.load_features(case.case_id)

                self.assertEqual(
                    loaded["retro_adapt_annotation_count"],
                    float(generated["retro_adapt_annotation_count"]),
                )
                source = store.conn.execute(
                    """
                    SELECT creator, text_value, copyright_mode
                    FROM adaptation_sources
                    WHERE case_id = ? AND source_id = 'critic-red'
                    """,
                    [case.case_id],
                ).fetchone()
                self.assertEqual(source[0], "critic")
                self.assertIsNone(source[1])
                self.assertEqual(source[2], "reference")
            finally:
                store.close()

    def test_unknown_parent_is_rejected(self):
        payload = self.sample_payload()
        payload["annotations"][0]["parent_id"] = "missing"

        with self.assertRaises(AdaptationValidationError):
            AdaptationCase.from_dict(payload)

    def test_bootstrap_reuses_existing_ru_en_film_plots_and_based_on(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "enrichment.duckdb"
            conn = duckdb.connect(str(db_path))
            try:
                conn.execute(
                    """
                    CREATE TABLE film_enrichment (
                        imdb_id VARCHAR,
                        imdb_title VARCHAR,
                        imdb_year INTEGER,
                        wikidata_json VARCHAR,
                        enwiki_title VARCHAR,
                        enwiki_revision BIGINT,
                        enwiki_plot VARCHAR,
                        ruwiki_title VARCHAR,
                        ruwiki_revision BIGINT,
                        ruwiki_plot VARCHAR
                    )
                    """
                )
                conn.execute(
                    """
                    INSERT INTO film_enrichment VALUES (
                        'tt1234567',
                        'Demo Adaptation',
                        2024,
                        ?,
                        'Demo film',
                        101,
                        'English plot summary',
                        'Демо-фильм',
                        202,
                        'Русский пересказ фильма'
                    )
                    """,
                    [json.dumps({"based_on": ["Q100"]})],
                )
            finally:
                conn.close()

            payload = load_case_seed_from_enrichment(
                "tt1234567",
                enrichment_db_path=db_path,
            )

            self.assertEqual(payload["source_work_id"], "Q100")
            self.assertEqual(len(payload["sources"]), 2)
            self.assertEqual(
                {item["language"] for item in payload["sources"]},
                {"ru", "en"},
            )
            self.assertEqual(payload["annotations"], [])


if __name__ == "__main__":
    unittest.main()
