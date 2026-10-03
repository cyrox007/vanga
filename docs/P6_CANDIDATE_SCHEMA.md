# P6 — Versioned Candidate Schema для accepted proxies

После успешного preregistered proxy ablation hypothesis получает `status=accepted`. Это ещё не означает, что feature автоматически входит в production CatBoost.

Этот слой фиксирует accepted proxies в отдельную **research candidate schema**.

## Цепочка

```text
retrospective finding
  ↓
proxy hypothesis
  ↓ temporal audit + ablation
accepted hypothesis
  ↓
ProxyCandidateSchemaRegistry
  ↓ immutable feature contracts
combined schema ablation / production quality gate
```

## Что требуется для freeze

Каждая hypothesis должна одновременно:

1. иметь `status=accepted`;
2. иметь сохранённый `passed=true` result в `proxy_ablation_results`;
3. всё ещё соответствовать тому preregistered plan, который прошёл ablation.

Последнее проверяется отдельно: текущий registry contract реконструируется как исходный `status=ablation_ready` и его SHA-256 сравнивается с `plan_fingerprint_sha256` принятого результата.

Если после acceptance кто-то изменил:

- evidence;
- feature specs;
- temporal contract;
- coverage feature;
- ablation policy,

freeze завершается ошибкой. Нужен новый preregistered ablation.

## Совместимость нескольких hypotheses

Одна schema может объединять несколько accepted hypotheses только если у их passing results совпадают:

- `baseline_schema`;
- `dataset_fingerprint_sha256`;
- temporal holdout policy;
- holdout start/end year;
- holdout row count.

Accepted результаты с разных IMDb/source snapshots нельзя молча объединить.

## Feature deduplication

Если две hypotheses используют один feature с одинаковым:

```text
source_layer + temporal_contract
```

feature хранится один раз, а schema сохраняет:

- все `supporting_hypothesis_ids`;
- все `acceptance_result_fingerprints`;
- роли feature;
- coverage dependencies.

Если один и тот же feature зарегистрирован с разными source/temporal contracts, freeze блокируется.

## Coverage features

`coverage_feature` становится отдельным model feature contract.

Например:

```text
planned_runtime_minutes
  coverage -> source_format_known
```

в frozen schema даёт два `model_feature_names`.

Это важно: missingness не должна исчезнуть при переносе accepted proxy в candidate schema.

## Immutable versioning

Freeze требует:

```json
{
  "schema_id": "p6-schema-001",
  "schema_label": "p6-candidate-1",
  "schema_version": 1,
  "hypothesis_ids": ["worldbuilding-compression"]
}
```

`schema_id`, `schema_label` и `schema_version` защищены от повторного использования с другим содержимым.

Schema получает `schema_fingerprint_sha256`.

Повторный freeze того же `schema_id` с тем же содержимым идемпотентен.

## Статус schema

Текущий статус:

```text
frozen_research_candidate
```

Schema явно содержит:

```json
{
  "automatic_catboost_inclusion": false,
  "production_publication_allowed": false,
  "production_quality_gate_required": true
}
```

Если объединено больше одной hypothesis:

```json
{"combined_ablation_required": true}
```

Потому что два individually полезных признака могут взаимодействовать и вместе ухудшить модель.

## Materialization / combined plan

`ProxyCandidateSchemaPlanBuilder` превращает frozen schema в plan, совместимый с уже существующими:

- `ProxySourceMaterializer` (#76);
- `TemporalProxyAvailabilityAuditor` (#70);
- `GenericProxyAblationGate` (#70).

Coverage features при этом flatten-ятся в явные feature specs.

Пример:

```bash
python scripts/proxy_candidate_schema.py export-plan \
  p6-schema-001 \
  --output temp/p6-schema-001-plan.json
```

По умолчанию combined policy строгий:

```text
primary_metric = mae
require_improvement = true
max_mae_regression = 0
```

Если допуск регрессии заранее разрешён исследовательским протоколом:

```bash
python scripts/proxy_candidate_schema.py export-plan \
  p6-schema-001 \
  --allow-regression 0.005
```

Это меняет plan fingerprint, поэтому policy фиксируется до запуска.

## Важное ограничение

Combined schema plan использует synthetic id:

```text
candidate-schema:<schema_id>
```

Это **не** обычная proxy hypothesis. Поэтому его нельзя передавать в `ProxyAblationPipeline`, который меняет `proxy_hypotheses.status`.

Следующий P6 слой должен хранить результат combined schema ablation отдельно и переводить уже candidate schema, а не исходные hypotheses.

## CLI

Freeze:

```bash
python scripts/proxy_candidate_schema.py freeze schema.json
```

Просмотр:

```bash
python scripts/proxy_candidate_schema.py show p6-schema-001
```

Список:

```bash
python scripts/proxy_candidate_schema.py list
```

Combined plan:

```bash
python scripts/proxy_candidate_schema.py export-plan p6-schema-001
```

## Что этот слой не делает

Он не:

- объявляет candidate schema production schema v16;
- публикует CatBoost;
- переобучает production model;
- принимает expert consensus за ground truth;
- допускает retrospective feature names;
- заменяет combined ablation или production quality gate.