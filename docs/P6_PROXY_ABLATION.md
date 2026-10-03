# P6 — Temporal Proxy Audit и Generic Ablation Gate

## Зачем нужен второй P6 слой

`Proxy Hypothesis Registry` проверяет, что **идея** признака имеет допустимый pre-release temporal contract. Этого недостаточно: конкретная материализация может всё равно случайно взять факт, появившийся после cutoff, same-year историю или пропустить missing row.

Поэтому перед любым proxy ablation обязательны два независимых шага:

1. **Temporal Proxy Availability Audit** конкретных materialized rows;
2. **Generic Ablation Gate** результатов baseline/candidate на одном dataset и holdout.

Оба слоя research-only и не публикуют модель.

---

## 1. Materialization protocol

Входной JSON содержит `targets` и `observations`.

### Target

Для каждого фильма обязательны:

```json
{
  "target_id": "tt1234567",
  "target_year": 2025,
  "cutoff_at": "2025-02-01T00:00:00+00:00",
  "release_at": "2025-06-01T00:00:00+00:00"
}
```

`cutoff_at` должен быть **строго раньше** `release_at`.

Это предотвращает ситуацию, когда формально pre-release feature материализуется уже в день/после релиза.

### Available observation

Для датированного source/prod/public факта:

```json
{
  "target_id": "tt1234567",
  "feature_name": "source_worldbuilding_entity_count",
  "source_layer": "source_context",
  "temporal_contract": "known_at_lte_cutoff",
  "available": true,
  "value": 42,
  "provenance_id": "source-snapshot:abc",
  "source_timestamp": "2025-01-10T00:00:00+00:00"
}
```

Auditor требует:

```text
source_timestamp <= cutoff_at
```

Для `history_before_target_year` вместо timestamp используется:

```json
{
  "history_year": 2024
}
```

и проверяется:

```text
history_year < target_year
```

Same-year запись не считается прошлой историей.

### Explicit missing observation

Если proxy неизвестен на cutoff, строка всё равно обязательна:

```json
{
  "target_id": "tt1234567",
  "feature_name": "source_worldbuilding_entity_count",
  "source_layer": "source_context",
  "temporal_contract": "known_at_lte_cutoff",
  "available": false,
  "value": null,
  "missing_reason": "source complexity snapshot ещё не опубликован"
}
```

Пропущенная строка **не означает ноль**. Для каждой пары `target × preregistered feature` должна существовать либо available-row, либо explicit missing-row.

Так coverage становится измеримым и не смешивается с качеством фильма.

---

## Temporal audit output

Отчёт содержит:

- fingerprint preregistered plan;
- fingerprint нормализованной materialization;
- coverage каждого proxy;
- список violations;
- `passed`;
- собственный audit fingerprint.

Типовые нарушения:

- `cutoff_not_pre_release`;
- `timestamp_after_cutoff`;
- `historical_leakage`;
- `feature_not_preregistered`;
- `source_layer_mismatch`;
- `temporal_contract_mismatch`;
- `observation_missing`;
- `missing_row_has_value`;
- `available_row_missing_value`.

### CLI

```bash
python scripts/proxy_ablation.py audit \
  temp/worldbuilding-compression-ablation.json \
  temp/worldbuilding-materialization.json \
  --output temp/worldbuilding-audit.json
```

- exit `0` — audit пройден;
- exit `2` — leakage/invalid materialization/другая ошибка.

---

## 2. Generic Ablation Gate

Тяжёлый CatBoost training остаётся в существующем training pipeline. Новый P6 gate не создаёт второй feature/training pipeline.

Он принимает:

1. preregistered plan;
2. успешный temporal audit report;
3. baseline training result;
4. candidate training result.

### Обязательные bindings

Candidate должен хранить:

```text
plan_fingerprint_sha256
proxy_audit_fingerprint_sha256
dataset_fingerprint_sha256
```

Baseline и candidate обязаны иметь один `dataset_fingerprint_sha256`.

Также должны совпадать:

- train year range;
- test year range;
- train rows;
- test rows;
- total rows.

То есть сравнение невозможно, если между двумя запусками обновилась IMDb БД, изменился stable target cutoff или потерялись строки.

### Чистота feature ablation

Gate сравнивает `feature_names`.

Candidate обязан:

- сохранить **все** baseline features;
- добавить **ровно** preregistered proxy features;
- добавить указанные coverage features;
- не добавлять никакие посторонние признаки.

Если одновременно с proxy появился `unregistered_magic_proxy`, эксперимент отвергается независимо от MAE.

Это позволяет интерпретировать результат как ablation конкретной гипотезы.

### Acceptance rule

Если в plan:

```text
require_improvement = true
```

то candidate проходит только при:

```text
candidate_mae < baseline_mae
```

Равный MAE не является улучшением.

Если improvement не обязателен, используется preregistered `max_mae_regression`.

Gate никогда не публикует модель:

```text
published = false
research_only = true
```

### CLI

```bash
python scripts/proxy_ablation.py gate \
  temp/worldbuilding-compression-ablation.json \
  temp/worldbuilding-audit.json \
  temp/baseline-result.json \
  temp/candidate-result.json \
  --output temp/worldbuilding-gate.json
```

- exit `0` — preregistered gate пройден;
- exit `3` — эксперимент корректный, но quality rule не пройден;
- exit `2` — fingerprints/holdout/features/temporal audit невалидны.

---

## Что этот слой доказывает и чего не доказывает

Успешный gate означает только:

1. candidate proxy был материализован без обнаруженной temporal leakage;
2. baseline/candidate сравнивались на одном dataset/holdout;
3. feature delta соответствует preregistered hypothesis;
4. preregistered MAE rule выполнен.

Это **не доказывает причинность** и не делает retrospective expert observation входом модели.

После gate proxy может перейти к следующему этапу проверки: повторный ablation на дополнительных temporal windows / external transfer и только затем рассматриваться для production schema.

## Следующий P6-инкремент

Следующий слой должен добавить source-specific materializers/adapters:

- IMDb historical proxies;
- Source Context `as-of` proxies;
- Production Context `as-of` proxies.

Их задача — автоматически производить описанный выше materialization JSON из существующих DuckDB-хранилищ, не меняя audit/gate protocol.
