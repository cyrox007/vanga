# P5 — Blind validation Expert Analysis Corpus

## Цель

Blind validation проверяет, находит ли автоматический Adaptation Analyzer те же **классы структурных проблем**, которые независимо размечены экспертами, не передавая системе их выводы заранее.

Это не предсказание реакции конкретного критика и не «оценка вкуса». Красный Циник, BadComedian и последующие профили оцениваются раздельно.

## Anti-leakage workflow

Процесс разделён на две физически разные операции.

### 1. Экспорт blind manifest

```bash
python scripts/expert_blind_validation.py manifest \
  --split blind \
  --output temp/expert-blind-manifest.json
```

Manifest содержит только:

- `case_id`;
- IMDb ID, если есть;
- название/год фильма;
- `source_work_id`;
- split;
- fingerprint manifest.

Он **не содержит**:

- expert claim;
- observation;
- structural consequence;
- expert interpretation;
- URL/таймкод экспертного материала;
- gold `dimension/change_type`.

`train` нельзя использовать как blind acceptance split.

### 2. Автоматический Analyzer сохраняет prediction run

Минимальный формат:

```json
{
  "version": 1,
  "run_id": "blind-2026-10-03-001",
  "manifest_fingerprint_sha256": "...",
  "cases": [
    {
      "case_id": "case-001",
      "findings": [
        {
          "dimension": "worldbuilding",
          "change_type": "removed",
          "reference_id": "storydiff:auto-123",
          "confidence": 0.82
        }
      ]
    }
  ]
}
```

Каждый finding обязан иметь `reference_id` на структурный результат Analyzer. Свободная текстовая догадка без reference не проходит контракт.

Каждый case из manifest должен присутствовать явно. Если Analyzer ничего не нашёл, передаётся `findings: []`. Это не позволяет молча выбросить сложные случаи из recall.

### 3. Только после сохранения run читается gold corpus

```bash
python scripts/expert_blind_validation.py evaluate \
  temp/prediction-run.json \
  --split blind \
  --output temp/expert-blind-result.json
```

Prediction run обязан ссылаться на точный fingerprint текущего manifest. Если состав blind set изменился, старый prediction нельзя оценить как новый прогон.

## Что считается gold

По умолчанию gold claim участвует в blind evaluation только если:

- находится в выбранном split;
- `confidence >= 0.5`;
- имеет хотя бы один `supporting` evidence record.

Для диагностических экспериментов supporting-evidence filter можно отключить, но такой прогон не должен использоваться как строгий blind gate.

## Единица сравнения

Foundation сравнивает наличие класса:

`(case_id, dimension, change_type)`.

Несколько экспертных формулировок одного класса внутри одного фильма не раздувают gold count.

Это намеренно более грубая метрика, чем semantic equivalence конкретных claims. Она отвечает на вопрос: **обнаружил ли Analyzer тот же тип структурного изменения?**

Следующий уровень сможет отдельно сравнивать evidence/reference и конкретные causal chains.

## Метрики

Считаются:

- TP / FP / FN;
- precision;
- recall;
- F1;
- overall detection metrics;
- отдельно по каждому expert profile;
- отдельно по dimension.

Системный prediction один и тот же для всех экспертов. Для конкретного эксперта он сравнивается только на тех cases, где у этого эксперта есть допустимая gold-разметка.

Отчёт явно содержит:

- `expert_profiles_kept_separate = true`;
- `expert_interpretation_exposed_to_predictor = false`;
- `preference_score = null`.

То есть per-expert метрики не превращаются в «средний экспертный вкус».

## Fingerprints

Сохраняются:

- fingerprint blind manifest;
- fingerprint нормализованного prediction run;
- `run_id`;
- параметры confidence/evidence policy.

Это позволяет воспроизводить конкретный blind run и отличать его от последующей подгонки правил.

## Что этот слой пока не измеряет

Foundation не утверждает semantic identity конкретных формулировок claims и не сравнивает полный текст рецензии.

Он пока не измеряет:

- точность конкретного evidence span;
- совпадение причинной цепочки целиком;
- severity agreement;
- agreement/disagreement между экспертами;
- leave-one-expert-out transfer;
- перенос на фильмы без экспертного разбора.

Эти задачи идут следующими отдельными P5-инкрементами после наполнения реального корпуса.
