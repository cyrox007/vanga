# P6 — Канонический Proxy Ablation Pipeline

После PR #70 и #71 в коде существовали два последовательных, но независимых контракта:

- `GenericProxyAblationGate` — строгая проверка temporal audit, feature purity, dataset/holdout parity и MAE;
- `ProxyAblationResultGate` — отдельный result contract с собственным вычислением verdict и persistence.

Чтобы не получить два разных источника истины, канонический pipeline использует **только решение `GenericProxyAblationGate`**.

## Поток

```text
Proxy Hypothesis Registry
        ↓
TemporalProxyAvailabilityAuditor
        ↓
GenericProxyAblationGate      ← единственный quality/verdict gate
        ↓ gate_fingerprint
ProxyAblationPipeline
        ↓
proxy_ablation_results + hypothesis status
```

`ProxyAblationResultGate.evaluate()` сохраняется для обратной совместимости со старыми исследовательскими артефактами, но новый workflow не использует его для повторного решения.

## Что проверяет Generic gate

Перед persistence уже должны пройти:

- preregistered plan fingerprint;
- temporal audit;
- один dataset fingerprint;
- одинаковые train/test годы и row counts;
- candidate привязан к plan и audit fingerprint;
- baseline features не удалены;
- добавлены **ровно** preregistered proxy + coverage features;
- MAE rule из preregistered plan.

Pipeline не пересчитывает эти правила вторым алгоритмом.

## Persistence

В существующую таблицу `proxy_ablation_results` записывается:

- `result_fingerprint_sha256 = gate_fingerprint_sha256`;
- dataset fingerprint;
- temporal test holdout;
- baseline/candidate MAE;
- MAE delta;
- passed/verdict;
- runner metadata.

Таким образом history хранит fingerprint именно того gate report, который принял решение.

## Status transition

Новый result можно записать только из:

```text
status = ablation_ready
```

Далее:

```text
comparison.passed = true  -> accepted
comparison.passed = false -> rejected
```

При rejection сохраняется технический reason из Generic gate.

## Idempotency

Если тот же `gate_fingerprint` уже находится в result history, pipeline возвращает существующий result и не требует снова переводить конечный status в `ablation_ready`.

Это позволяет безопасно повторять CLI после сетевого/процессного сбоя.

## Атомарность

Insert result и update hypothesis status выполняются в одной DuckDB-транзакции.

Если insert/update падает, выполняется rollback.

## Что не происходит

Даже `accepted`:

- не публикует CatBoost;
- не добавляет feature в production schema;
- не заменяет общий production quality gate;
- не делает retrospective evidence входным feature.

В результате pipeline возвращает:

```json
{
  "published": false,
  "research_only": true,
  "automatic_catboost_inclusion": false
}
```

## CLI

```bash
python scripts/proxy_ablation_pipeline.py \
  temp/plan.json \
  temp/audit.json \
  temp/baseline.json \
  temp/candidate.json \
  --result-id worldbuilding-compression-run-001 \
  --output temp/final-p6-result.json
```

Exit codes:

- `0` — Generic gate passed, hypothesis accepted;
- `3` — корректный experiment, но Generic gate rejected;
- `2` — invalid contract / temporal leakage / feature purity / fingerprint mismatch.

## Следующий этап

Accepted proxy должен отдельно превратиться в **versioned candidate feature schema** с воспроизводимым feature materializer. Только после этого он может участвовать в полном model quality gate и final refit.