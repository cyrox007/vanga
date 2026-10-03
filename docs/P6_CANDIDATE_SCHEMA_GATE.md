# P6 — Combined Ablation Gate для Candidate Schema

Frozen candidate schema объединяет accepted proxy hypotheses, но это ещё не доказывает, что их совместное использование улучшает модель.

Два individually полезных feature могут взаимодействовать и вместе ухудшить temporal MAE.

Этот слой добавляет отдельный combined gate для **самой schema**, не меняя статусы исходных accepted hypotheses.

## Канонический поток

```text
accepted proxy hypotheses
        ↓
ProxyCandidateSchemaRegistry
        ↓ frozen schema + fingerprint
ProxyCandidateSchemaPlanBuilder
        ↓ combined preregistered plan
ProxySourceMaterializer
        ↓
TemporalProxyAvailabilityAuditor
        ↓
GenericProxyAblationGate       ← единственный quality decision
        ↓
ProxyCandidateSchemaGate
        ↓
combined_validated | combined_rejected
```

## Один источник решения

`ProxyCandidateSchemaGate` не считает MAE policy самостоятельно.

Он вызывает существующий `GenericProxyAblationGate`, который уже проверяет:

- plan fingerprint;
- temporal audit;
- одинаковый dataset baseline/candidate;
- одинаковые train/test годы и row counts;
- feature purity;
- candidate binding к plan/audit fingerprints;
- preregistered MAE acceptance policy.

Combined gate только проверяет связь результата с immutable candidate schema и сохраняет history.

## Dataset binding schema

Frozen schema хранит dataset fingerprint индивидуальных accepted ablations.

Combined experiment обязан использовать тот же fingerprint.

Это дополнительная проверка поверх Generic gate: даже если baseline/candidate оба случайно посчитаны на другом одинаковом dataset, schema gate их отвергнет.

Для проверки на новом snapshot нужно freeze новую schema version после соответствующих accepted ablations.

## Lifecycle

Начальное состояние:

```text
frozen_research_candidate
```

После одного combined experiment:

```text
combined_validated
```

или:

```text
combined_rejected
```

Один schema version допускает один уникальный combined gate result.

Повтор того же `gate_fingerprint_sha256` идемпотентен.

Другой plan/dataset/policy после финального результата требует новую schema version. История не переписывается.

## Result history

Таблица `proxy_candidate_schema_results` хранит:

- `schema_id`;
- frozen `schema_fingerprint_sha256`;
- combined `plan_fingerprint_sha256`;
- `proxy_audit_fingerprint_sha256`;
- `gate_fingerprint_sha256`;
- dataset fingerprint;
- holdout;
- baseline/candidate reports;
- comparison;
- verdict;
- полный Generic gate JSON;
- время фиксации.

## Исходные hypotheses не меняются

Если schema rejected, individual hypotheses всё равно остаются `accepted`.

Это означает только:

> конкретное объединение feature contracts не прошло combined test.

Индивидуальное доказательство каждого proxy не стирается.

## Frozen fingerprint и lifecycle status

`schema_fingerprint_sha256` относится к immutable frozen definition.

Lifecycle status хранится отдельно в строке registry и в combined result. Поэтому переход `frozen → validated/rejected` не меняяет frozen fingerprint.

Это позволяет воспроизводимо доказать, какой именно набор feature contracts тестировался.

## Production остаётся заблокирован

Даже результат:

```text
combined_validated
```

возвращает:

```json
{
  "research_only": true,
  "automatic_catboost_inclusion": false,
  "production_publication_allowed": false
}
```

Дальше нужен отдельный promotion contract: candidate schema → реальная model schema → полный production quality gate → final refit.

## CLI

Combined evaluation:

```bash
python scripts/proxy_candidate_schema_gate.py evaluate \
  p6-schema-001 \
  temp/p6-schema-001-plan.json \
  temp/p6-schema-001-audit.json \
  temp/baseline.json \
  temp/candidate.json \
  --output temp/p6-schema-001-result.json
```

Exit codes:

- `0` — combined schema validated;
- `3` — корректный experiment, но combined schema rejected;
- `2` — fingerprint/dataset/temporal/feature contract error.

Просмотр result:

```bash
python scripts/proxy_candidate_schema_gate.py result p6-schema-001
```

## Следующий шаг

После `combined_validated` нужен **promotion manifest**, который:

1. привяжет candidate schema fingerprint к новой model schema version;
2. зафиксирует exact feature order/materializer versions;
3. не позволит promotion при `combined_rejected`;
4. всё ещё не будет публиковать модель без обычного production quality gate и final refit.