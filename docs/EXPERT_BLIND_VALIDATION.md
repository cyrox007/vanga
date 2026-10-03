# P5 — Blind validation Expert Analysis Corpus

## Цель

`src/expert_blind_validation.py` проверяет, способен ли автоматический Adaptation Analyzer находить те же **классы структурных наблюдений**, которые независимо размечены экспертами.

Это не попытка предсказывать мнение конкретного критика и не построение единой «правильной экспертной оценки».

Каждый expert profile оценивается отдельно.

## Что получает Analyzer

Перед прогоном создаётся manifest:

```bash
python scripts/expert_blind_validation.py manifest \
  --split blind \
  --output temp/blind-manifest.json
```

Manifest содержит только:

- `case_id`;
- IMDb ID, если есть;
- название/год фильма;
- `source_work_id`;
- split;
- fingerprints.

Он **не содержит**:

- expert claims;
- observation;
- structural consequence;
- expert interpretation;
- supporting/contradicting evidence;
- expert IDs.

## Sealed gold fingerprint

Manifest содержит `sealed_gold_fingerprint_sha256`.

Это SHA-256 закрытого набора:

`expert_id + case_id + claim_id + dimension + change_type + confidence`.

Сами значения не раскрываются Analyzer-у.

`manifest_fingerprint_sha256` включает sealed gold fingerprint. Поэтому если после выдачи manifest кто-то изменил gold-разметку, старый prediction run нельзя оценить против нового corpus незаметно.

## Формат prediction run

Analyzer сохраняет только структурные findings:

```json
{
  "version": 1,
  "run_id": "blind-2026-10-03",
  "manifest_fingerprint_sha256": "...",
  "cases": [
    {
      "case_id": "case-001",
      "findings": [
        {
          "dimension": "worldbuilding",
          "change_type": "removed",
          "reference_id": "storydiff:auto-42",
          "confidence": 0.91
        }
      ]
    }
  ]
}
```

Неизвестные поля отклоняются. В частности нельзя передать `expert_interpretation`, `claim_id`, gold labels или другие скрытые данные под видом prediction metadata.

Каждый case из manifest должен присутствовать в run, даже если `findings=[]`.

## Оценка

```bash
python scripts/expert_blind_validation.py evaluate \
  temp/blind-predictions.json \
  --split blind \
  --output temp/blind-report.json
```

По умолчанию:

- prediction confidence >= 0.5;
- gold confidence >= 0.5;
- gold claim обязан иметь supporting evidence.

Для каждого expert profile отдельно считаются TP/FP/FN, precision/recall/F1 и метрики по dimensions.

## Почему нет общего expert score

Красный Циник, BadComedian и будущие эксперты — независимые источники методики. Их разметка не складывается в одну шкалу вкуса.

Поэтому отчёт содержит:

- `per_expert`;
- `combined_expert_score = null`;
- `cross_expert_dimension_score = null`;
- `preference_score = null`.

Сводное agreement/disagreement исследуется отдельным статистическим слоем, а не через усреднение F1 или оценок.

## Неполнота экспертного материала

Если эксперт не обсуждал dimension в конкретном case, это **не отрицательная метка**.

Prediction по такой dimension:

- не считается FP;
- попадает в `unscored_prediction_count`.

FP возможен только внутри dimension, которую этот эксперт явно размечал в данном case.

Это защищает от ложного вывода «эксперт не упомянул → проблемы не было».

## Splits

Evaluator допускает:

- `development` — настройка процесса;
- `blind` — закрытая проверка;
- `external_transfer` — фильмы/материалы вне корпуса разработки.

`train` запрещён как acceptance split.

## Ограничения

Blind evaluator сравнивает классы `(dimension, change_type)`, а не тексты мнений.

Он не доказывает, что эксперт объективно прав. Он отвечает на более узкий вопрос:

> воспроизводит ли автоматический структурный анализ классы наблюдений, которые независимый эксперт использовал в аргументации?

Результат остаётся `research_only=true` и напрямую не подмешивается в production CatBoost.
