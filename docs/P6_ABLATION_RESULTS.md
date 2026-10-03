# P6 — Persistence результатов Proxy Ablation

Этот слой завершает исследовательский цикл hypothesis:

```text
proposed
  ↓
ablation_ready
  ↓ GenericProxyAblationGate
accepted | rejected
```

Статус после ablation меняется только вместе с сохранением проверяемого результата.

## Что сохраняется

В `proxy_ablation_results` сохраняются:

- `hypothesis_id`;
- preregistered `plan_fingerprint_sha256`;
- `proxy_audit_fingerprint_sha256`;
- `gate_fingerprint_sha256`;
- `dataset_fingerprint_sha256`;
- temporal holdout;
- baseline metrics;
- candidate metrics;
- comparison / `delta_mae`;
- итог `accepted` или `rejected`;
- время фиксации результата.

Полный retrospective экспертный текст сюда не попадает.

## Атомарность

Сохранение result и переход status выполняются одной транзакцией DuckDB.

Нельзя получить состояние:

```text
hypothesis=accepted
result отсутствует
```

или наоборот.

## Проверки перед записью

Новый result принимается только если:

1. hypothesis существует;
2. status равен `ablation_ready`;
3. supplied plan имеет валидный fingerprint;
4. plan совпадает с текущим preregistered plan registry;
5. gate относится к этой hypothesis;
6. gate fingerprint воспроизводим;
7. gate ссылается на тот же plan fingerprint;
8. присутствуют audit/dataset fingerprints;
9. `published=false`;
10. `research_only=true`;
11. `comparison.passed` является boolean.

Решение не передаётся вызывающим кодом:

```text
passed=true  -> accepted
passed=false -> rejected
```

Таким образом CLI не может сказать «прими hypothesis», если gate её отклонил.

## Idempotency

`gate_fingerprint_sha256` уникален.

Повторная запись того же результата возвращает существующий `result_id` и не создаёт вторую строку. Это особенно важно после перехода hypothesis из `ablation_ready` в конечный status: fingerprint исходного preregistered plan уже зафиксирован в result.

## Rejected

При отрицательном ablation `rejection_reason` формируется из результата gate, например:

```text
Proxy ablation rejected: mae_not_improved; delta_mae=0.013
```

Это не экспертная интерпретация, а техническая причина rejection.

## Accepted не означает публикацию

Даже `accepted` hypothesis не публикует CatBoost автоматически.

Result содержит:

```json
{"automatic_catboost_publication": false}
```

Следующий отдельный этап должен решить, как accepted proxy превращается в versioned candidate schema и проходит полный production quality gate.

## CLI

Запись результата:

```bash
python scripts/proxy_ablation_results.py record \
  temp/plan.json \
  temp/gate.json \
  --output temp/recorded-result.json
```

Последний result:

```bash
python scripts/proxy_ablation_results.py latest \
  worldbuilding-compression
```

## Почему result store отделён от gate

`GenericProxyAblationGate` остаётся чистой проверкой и может использоваться в CI/исследовательских прогонах без изменения registry.

`ProxyAblationResultStore` — единственный слой этого инкремента, который изменяет статус hypothesis после уже вычисленного и проверенного gate.