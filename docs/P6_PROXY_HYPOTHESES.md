# P6 — Pre-release Proxy Hypothesis Registry

## Назначение

Retrospective Analyzer может обнаружить полезную закономерность уже после выхода фильма, но такой результат нельзя напрямую передать в pre-release Vanga.

P6 вводит промежуточный обязательный слой:

```text
retrospective structural finding
        ↓ evidence/reference only
proxy hypothesis
        ↓ только заранее доступные candidate features
preregistered temporal ablation
        ↓
accepted/rejected после проверки на данных
```

Registry физически хранится отдельно в `proxy_hypotheses.duckdb` и **не подключается к CatBoost автоматически**.

## Пример

Retrospective наблюдение:

```text
StoryDiff/экспертный корпус:
сложный worldbuilding был сильно compressed в экранизации
```

Недопустимо:

```text
retro_adapt_worldbuilding_severity
storydiff_removed_count
expert_red_cynic_signal
```

Это пострелизные знания.

Допустимая гипотеза может предложить только заранее известные proxies:

```text
source_worldbuilding_entity_count
planned_runtime_minutes
writer_adaptation_count
source_complexity_coverage
```

При этом каждый proxy обязан иметь явный temporal contract.

## Retrospective evidence

Допустимые виды evidence:

- `storydiff`;
- `story_transform`;
- `expert_corpus`;
- `adaptation_analysis`;
- `production_context_outcome`.

Evidence хранится как:

- kind;
- `reference_id`;
- наша краткая observation summary;
- confidence.

Registry не хранит полный сторонний текст, transcript или `expert_interpretation`.

Retrospective evidence объясняет, **почему гипотеза появилась**, но никогда не является входным feature.

## Pre-release proxy sources

Foundation разрешает четыре source layer.

### `imdb_history`

Только:

```text
history_before_target_year
```

То есть историческая работа человека/команды должна иметь год строго меньше target year.

### `source_context`

Допустимы:

- `known_at_lte_cutoff`;
- `published_at_lte_cutoff`;
- `planned_before_release`.

### `production_context`

Только:

```text
known_at_lte_cutoff
```

Событие может произойти раньше, но prediction имеет право увидеть его только после публичного `known_at`.

### `pre_release_public_signal`

Только датированные публичные сигналы:

- `known_at_lte_cutoff`;
- `published_at_lte_cutoff`.

Этот source layer не разрешает пострелизные audience/review signals.

## Forbidden feature guard

Registry отвергает feature names с retrospective namespaces:

- `retro_adapt_*`;
- `storydiff_*`;
- `story_diff_*`;
- `story_transform_*`;
- `expert_*`;
- `post_release_*`.

Также явно запрещены target/post-release values вроде:

- `averageRating` как текущий target;
- `current_imdb_rating`;
- `actual_rating`;
- `future_rating`;
- `expert_score`;
- `critic_score`.

Исторические признаки типа `director_avg_rating`, рассчитанные только по прошлым фильмам, остаются допустимыми через `imdb_history/history_before_target_year`.

## Coverage и missingness

Proxy может указать отдельный `coverage_feature`.

Это важно: отсутствие source complexity или production context не должно превращаться в нейтральное значение и выглядеть как реальное наблюдение.

## Готовность к ablation

Hypothesis не может получить `ablation_ready`, пока отсутствует хотя бы один из трёх элементов:

1. retrospective evidence;
2. pre-release proxy feature;
3. preregistered ablation spec.

`validation_report()` возвращает причины блокировки:

- `retrospective_evidence_missing`;
- `pre_release_proxy_missing`;
- `ablation_spec_missing`;
- `hypothesis_rejected`.

## Preregistered ablation

Foundation фиксирует до запуска:

- baseline schema;
- candidate label;
- `holdout_policy = stable_temporal_last_two_years`;
- `primary_metric = mae`;
- допустимую MAE regression;
- нужен ли строгий improvement;
- обязательность dataset fingerprint.

Другой split или training-fit metric registry отвергает.

Экспортированный plan имеет SHA-256 fingerprint. Это не позволяет после просмотра результата незаметно изменить candidate features или acceptance contract и назвать эксперимент тем же самым.

## CLI

Инициализация:

```bash
python scripts/proxy_hypotheses.py init
```

Импорт bundle:

```bash
python scripts/proxy_hypotheses.py import-bundle \
  hypothesis.json \
  --mark-ready \
  --output temp/proxy-import.json
```

Проверка:

```bash
python scripts/proxy_hypotheses.py validate worldbuilding-compression
```

Перевод в `ablation_ready`:

```bash
python scripts/proxy_hypotheses.py mark-ready worldbuilding-compression
```

Экспорт preregistered plan:

```bash
python scripts/proxy_hypotheses.py export-plan \
  worldbuilding-compression \
  --output temp/worldbuilding-compression-ablation.json
```

## Что registry намеренно не делает

Он не:

- включает feature в production model;
- считает retrospective correlation доказательством причинности;
- решает автоматически, что мнение эксперта верно;
- использует expert interpretation как число;
- запускает CatBoost ablation;
- принимает hypothesis после одного кейса.

Следующий P6-инкремент — **temporal availability auditor + generic proxy ablation runner**, который материализует candidate features строго as-of target cutoff, проверяет coverage/leakage и сравнивает baseline/candidate на одном dataset fingerprint/holdout.
