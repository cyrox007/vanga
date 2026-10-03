# P6 — Temporal Availability Audit и Proxy Ablation

Этот слой находится между `Proxy Hypothesis Registry` и любым включением нового признака в production-модель.

Главное правило:

> retrospective finding объясняет происхождение гипотезы, но никогда не является feature.

## Поток

```text
retrospective evidence
        ↓
proxy hypothesis registry
        ↓ preregistered plan + SHA-256
materialized pre-release candidate values
        ↓
temporal availability audit
        ↓ passed only
baseline/candidate temporal evaluation
        ↓
accepted/rejected hypothesis
```

Ни audit, ни evaluator сами не включают feature в CatBoost и не публикуют модель.

## Temporal proof

Каждое materialized значение имеет:

- `value`;
- `available_before_release=true`;
- доказательство времени, соответствующее контракту.

### `history_before_target_year`

Обязателен:

```json
{"history_max_year": 2024}
```

Для target `2025` допустимо только `history_max_year < 2025`.

### `known_at_lte_cutoff`

Обязателен `known_at <= cutoff_at`.

Факт мог произойти раньше, но если публично стал известен позже cutoff, прогноз его не видит.

### `published_at_lte_cutoff`

Обязателен `published_at <= cutoff_at`.

Это контракт для датированных публичных pre-release сигналов.

### `planned_before_release`

Planned fact обязан быть известен не позже cutoff и не позже release, если `release_at` задан.

## Незарегистрированные features

Materialized dataset не может добавить feature, отсутствующий в preregistered plan.

Это отдельно блокирует попытку передать, например:

- `storydiff_*`;
- `story_transform_*`;
- `expert_*`;
- другие retrospective значения,

даже если вызывающий код ошибочно пометил их `available_before_release=true`.

## Fingerprints

Audit проверяет:

1. `plan_fingerprint_sha256`;
2. `dataset_fingerprint_sha256`.

Ablation evaluator затем требует, чтобы baseline и candidate ссылались на тот же dataset fingerprint.

Изменение plan после preregistration приводит к fingerprint mismatch.

## Holdout parity

Baseline и candidate обязаны иметь одинаковые:

- `holdout_start_year`;
- `holdout_end_year`;
- `test_rows`;
- dataset fingerprint.

Поэтому улучшение нельзя получить заменой набора тестовых фильмов.

## Acceptance

Foundation использует MAE из preregistered plan.

Если:

```text
require_improvement = true
```

candidate проходит только при:

```text
candidate_mae < baseline_mae
```

Если improvement не обязателен, применяется заранее зафиксированный `max_mae_regression`.

Результат всегда содержит:

```json
{"automatic_catboost_publication": false}
```

То есть успешный research ablation не равен автоматической публикации.

## Materialized dataset

Минимальный пример строки:

```json
{
  "target_id": "tt123",
  "target_year": 2025,
  "cutoff_at": "2025-03-01T00:00:00Z",
  "release_at": "2025-06-01T00:00:00Z",
  "features": {
    "director_prior_count": {
      "value": 7,
      "available_before_release": true,
      "history_max_year": 2024
    },
    "planned_runtime_minutes": {
      "value": 125,
      "available_before_release": true,
      "known_at": "2025-01-15T00:00:00Z"
    }
  }
}
```

Missing value допустим как факт покрытия, но audit помечает отсутствие materialized feature как issue: candidate experiment обязан заранее решить, каким coverage/missingness feature это представлено.

## CLI

Temporal audit:

```bash
python scripts/proxy_ablation.py audit \
  temp/plan.json \
  temp/materialized.json \
  --output temp/audit.json
```

Exit codes:

- `0` — audit passed;
- `2` — temporal issues;
- `4` — invalid input/contract.

Ablation evaluation:

```bash
python scripts/proxy_ablation.py evaluate \
  temp/plan.json \
  temp/audit.json \
  temp/baseline.json \
  temp/candidate.json \
  --output temp/result.json
```

Exit codes:

- `0` — candidate passed preregistered MAE rule;
- `3` — valid experiment, но candidate rejected;
- `4` — dataset/holdout/fingerprint/contract mismatch.

## Что ещё не делает этот слой

Он намеренно не:

- извлекает данные из IMDb/Source/Production DB сам;
- генерирует retrospective features;
- запускает CatBoost training;
- принимает consensus экспертов за ground truth;
- публикует model generation.

Следующий шаг — source-specific materializers, которые формируют этот единый temporal-proof формат из уже существующих `imdb_history`, `source_context` и `production_context`.