# P6 — Source Context Proxy Materializer

`Proxy Hypothesis Registry` и `TemporalProxyAvailabilityAuditor` уже задают контракт, но до этого source-context значения приходилось собирать вручную.

Этот materializer формирует готовые строки:

```text
target × preregistered source_context feature
```

в формате, который принимает P6 temporal auditor.

## Основной принцип

Materializer не решает, полезен ли feature, и не публикует его в CatBoost.

Он отвечает только на вопросы:

1. какое значение было воспроизводимо доступно;
2. было ли оно известно к target cutoff;
3. какой provenance подтверждает значение;
4. если значения не было — почему оно missing.

## Вход

Preregistered plan:

```json
{
  "candidate_pre_release_features": [
    {
      "feature_name": "planned_runtime_minutes",
      "source_layer": "source_context",
      "temporal_contract": "planned_before_release"
    }
  ]
}
```

Target manifest:

```json
{
  "targets": [
    {
      "target_id": "tt1234567",
      "project_id": "film-project-1234567",
      "target_year": 2025,
      "cutoff_at": "2025-03-01T00:00:00Z",
      "release_at": "2025-06-01T00:00:00Z"
    }
  ]
}
```

Если `project_id` не указан, используется `target_id`.

## Выход

Пример доступного значения:

```json
{
  "target_id": "tt1234567",
  "feature_name": "planned_runtime_minutes",
  "source_layer": "source_context",
  "temporal_contract": "planned_before_release",
  "available": true,
  "value": 125.0,
  "provenance_id": "source-context:...",
  "source_timestamp": "2025-02-15T00:00:00+00:00"
}
```

Пример missing:

```json
{
  "target_id": "tt1234567",
  "feature_name": "source_worldbuilding_entity_count",
  "source_layer": "source_context",
  "temporal_contract": "known_at_lte_cutoff",
  "available": false,
  "value": null,
  "missing_reason": "complexity_metric_missing"
}
```

Missing не превращается в `0`.

## Temporal semantics

Materializer использует только facts, видимые на `cutoff_at`.

Для source links учитывается первый `known_at` logical связи. Повторное provenance одной связи не увеличивает aggregate.

Для project format/runtime используется `format_known_at`.

Для complexity используется `known_at` выбранного snapshot.

`source_timestamp` aggregate feature равен самой поздней temporal границе среди фактов, реально участвовавших в вычислении.

После materialization тот же timestamp ещё раз проверяет `TemporalProxyAvailabilityAuditor`.

## Source Complexity

Materializer никогда не выбирает complexity protocol автоматически.

Если plan содержит complexity feature, CLI требует одновременно:

```text
--complexity-method
--complexity-version
```

Например:

```bash
--complexity-method story-structure \
--complexity-version 1
```

Измерения версии `2` при таком запуске не участвуют.

Это защищает от незаметного смешивания разных extractor/annotation protocols.

## P6 aliases

Поддержаны явные алиасы:

| P6 feature | Source Context feature |
|---|---|
| `planned_runtime_minutes` | `source_planned_runtime_minutes` |
| `planned_episode_count` | `source_planned_episode_count` |
| `planned_episode_runtime_minutes` | `source_planned_episode_runtime_minutes` |
| `planned_total_runtime_minutes` | `source_planned_total_runtime_minutes` |
| `source_worldbuilding_entity_count` | `source_complexity_worldbuilding_entity_count_mean` |
| `source_complexity_coverage` | `source_complexity_work_coverage_ratio` |

Также materializer принимает существующие feature names из:

- `SourceContextStore.features_as_of()`;
- `SourceFormatPressureContext.features_as_of()`;
- `SourceComplexityContext.features_as_of()`.

Неизвестный feature приводит к fail-closed ошибке.

## Missing policy

Примеры причин:

- `source_project_not_found`;
- `source_links_missing`;
- `format_not_known_by_cutoff`;
- `planned_runtime_missing`;
- `source_publication_date_missing`;
- `complexity_metric_missing`;
- `temporal_proof_missing`.

Поздний факт не становится fake zero.

## Mixed-source plans

Текущий adapter намеренно принимает только:

```text
source_layer = source_context
```

Если в одном plan одновременно есть `imdb_history` или `production_context`, запуск завершается ошибкой.

Причина: пока отдельные adapters не объединены composer-ом, частичный materialization нельзя выдавать за готовый audit dataset.

Следующие P6 инкременты:

1. IMDb-history materializer;
2. Production Context materializer;
3. composer нескольких source layers.

## `published_at_lte_cutoff`

Этот materializer его не имитирует через `known_at`.

`published_at_lte_cutoff` должен обслуживаться отдельным public-signal adapter, где можно доказать именно время публикации.

## CLI

```bash
python scripts/proxy_source_context_materialize.py \
  temp/plan.json \
  temp/targets.json \
  --source-db source_context.duckdb \
  --complexity-method story-structure \
  --complexity-version 1 \
  --output temp/source-materialization.json
```

Результат можно передать в:

```bash
python scripts/proxy_ablation.py audit \
  temp/plan.json \
  temp/source-materialization.json
```

## Что materializer не делает

Он не:

- читает StoryDiff/Expert Corpus;
- использует post-release film facts;
- выбирает complexity method по результату;
- заменяет missing нулём;
- принимает/rejects hypothesis;
- обучает или публикует CatBoost.