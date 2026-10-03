# P5 — Expert agreement и held-out transfer evaluation

## Зачем нужен слой

Expert Analysis Corpus хранит профили экспертов раздельно. Следующий вопрос — где независимые экспертные разборы подтверждают один и тот же структурный паттерн, где расходятся и переносится ли найденный Analyzer-ом паттерн на полностью исключённый expert profile.

Этот слой не определяет, кто из экспертов «прав». Он также не строит общий taste score.

## Agreement / disagreement

`ExpertAgreementAnalyzer` сравнивает только structural classes:

`(case_id, dimension, change_type)`.

Сравнение существует только если минимум два expert profile **явно размечали одну и ту же пару `case_id + dimension`**.

Возможны три состояния:

- `exact_agreement` — наборы `change_type` совпадают полностью;
- `partial_overlap` — есть хотя бы один общий `change_type`, но наборы различаются;
- `explicit_disagreement` — оба эксперта размечали dimension, но наборы `change_type` не пересекаются.

Если dimension размечена только одним экспертом, она получает `not_comparable`.

### Молчание не является disagreement

Если Красный Циник обсуждает worldbuilding, а BadComedian в том же фильме не затрагивает worldbuilding, это не считается расхождением.

Отчёт явно фиксирует:

```text
silence_is_disagreement = false
compare_only_shared_case_dimensions = true
```

Это защищает анализ от искусственного конфликта между разными тематическими фокусами экспертов.

## Pairwise metrics

Для каждой пары экспертов считаются только сопоставимые `case + dimension`:

- comparable dimension count;
- exact agreement count/rate;
- partial overlap count;
- explicit disagreement count;
- mean Jaccard между наборами `change_type`.

`winner = null` и `preference_score = null` являются частью контракта.

## CLI agreement

```bash
python scripts/expert_agreement_transfer.py agreement \
  --split blind \
  --output temp/expert-agreement.json
```

По умолчанию учитываются только gold claims с `confidence >= 0.5` и supporting evidence.

## Held-out expert transfer

Transfer protocol проверяет Analyzer на expert profile, который должен быть полностью исключён из discovery/training процесса.

### 1. Экспорт manifest

```bash
python scripts/expert_agreement_transfer.py transfer-manifest \
  --held-out-expert red-cynic \
  --split external_transfer \
  --output temp/red-cynic-transfer-manifest.json
```

Manifest содержит только case metadata и не содержит:

- held-out claims;
- observation;
- structural consequence;
- expert interpretation;
- gold dimensions/change types.

Fingerprint включает protocol, split, held-out expert и состав cases.

### 2. Prediction run

Run должен явно содержать:

```json
{
  "version": 1,
  "run_id": "transfer-001",
  "manifest_fingerprint_sha256": "...",
  "excluded_expert_ids": ["red-cynic"],
  "cases": []
}
```

Если `held_out_expert_id` отсутствует в `excluded_expert_ids`, evaluator отказывается считать transfer result.

Это **декларативный контракт**, а не техническое доказательство происхождения модели. Код не может криптографически доказать, что внешний discovery pipeline действительно никогда не видел held-out profile. Поэтому отчёт всегда содержит:

```text
held_out_expert_exclusion_declared = true
exclusion_is_declarative_not_audit_proof = true
```

Для строгого исследования этот контракт должен дополняться воспроизводимым corpus snapshot/fingerprint и отдельным discovery pipeline.

### 3. Post-hoc evaluation

```bash
python scripts/expert_agreement_transfer.py transfer-evaluate \
  temp/red-cynic-transfer-run.json \
  --held-out-expert red-cynic \
  --split external_transfer \
  --output temp/red-cynic-transfer-result.json
```

Gold held-out profile читается только на этом шаге.

Оцениваются только dimensions, которые held-out эксперт реально размечал для конкретного case. Остальные prediction classes идут в `unscored_prediction_count`, а не в FP.

## Метрики transfer

Считаются:

- TP / FP / FN;
- precision / recall / F1;
- metrics по dimension;
- scored prediction count;
- unscored prediction count;
- case count;
- `coverage_warning` при числе cases < 5.

`coverage_warning` особенно важен на раннем корпусе: transfer на одном-двух фильмах не является доказательством общей переносимости метода.

## Ограничения

Текущий слой не обучает новую модель на экспертных данных и не предсказывает предпочтения эксперта.

Он также не доказывает причинность между экспертным паттерном и будущим рейтингом фильма.

Следующий P5/P6 шаг — зарегистрировать воспроизводимые **structural proxy hypotheses** и проверить, какие из них можно превратить в признаки, доступные до премьеры, без post-release leakage.
