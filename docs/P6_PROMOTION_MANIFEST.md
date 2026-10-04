# P6 Promotion Manifest

`Promotion manifest` — последний governance-мост между исследовательским P6 и будущей model schema.

Он **не** включает признаки в CatBoost автоматически и **не** разрешает публикацию модели.

## Предусловия

Manifest можно создать только для `candidate schema` со статусом `combined_validated` и сохранённым passing combined gate result.

Проверяются:

- immutable fingerprint candidate schema;
- combined gate result и его fingerprint;
- dataset fingerprint;
- baseline model schema;
- полный ordered список base features;
- отсутствие пересечения новых proxy features с base feature order;
- версия target model schema должна быть новее baseline.

## Что фиксирует manifest

Manifest сохраняет:

- candidate schema id/version/fingerprint;
- combined gate result id/fingerprint;
- dataset fingerprint;
- base model schema;
- target model schema version/label;
- ordered `base_feature_names`;
- ordered `added_feature_names`;
- итоговый `full_feature_order`;
- `feature_order_fingerprint_sha256`;
- версии materializer/auditor/candidate-schema gate;
- source layers и исходные hypothesis ids.

`full_feature_order` является частью immutable-контракта: будущий training PR не должен тихо менять порядок или состав признаков.

## Жёсткие ограничения

Даже frozen promotion manifest содержит:

- `automatic_training_code_change=false`;
- `automatic_catboost_inclusion=false`;
- `publication_allowed=false`;
- `production_quality_gate_required=true`;
- `final_refit_required=true`.

Следующий ML-инкремент обязан отдельно:

1. подключить materialization в training pipeline;
2. воспроизвести exact feature order из manifest;
3. выполнить full temporal validation;
4. пройти production quality gate;
5. только после этого сделать final refit на всей стабильной истории.

## CLI

```bash
python scripts/proxy_promotion_manifest.py --db var/proxy_hypotheses.duckdb freeze promotion.json
python scripts/proxy_promotion_manifest.py --db var/proxy_hypotheses.duckdb show promotion-1
python scripts/proxy_promotion_manifest.py --db var/proxy_hypotheses.duckdb list
```

Пример `promotion.json`:

```json
{
  "promotion_id": "promotion-1",
  "schema_id": "proxy-schema-1",
  "target_model_schema_version": 16,
  "target_model_schema_label": "v16",
  "base_feature_names": [
    "genres_combined",
    "director_id",
    "writer_id"
  ]
}
```

Документация и CLI намеренно остаются research-only: создание manifest не является деплоем и не меняет production-модель.
