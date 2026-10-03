# StoryMap Benchmark Quality Gate

## Назначение

Benchmark suite уже умеет считать micro/macro precision/recall/F1 по case/split/kind. Этот слой отвечает на другой вопрос: **достаточно ли качество extractor/alignment, чтобы переходить к следующему исследовательскому этапу**.

Gate остаётся `research_only`: он не публикует ML-модель Vanga и не влияет на pre-release prediction.

## Почему policy отдельный

Порог нельзя выбирать после просмотра blind-результата. Поэтому gate принимает отдельный versioned JSON policy.

Минимальный пример:

```json
{
  "policy_id": "p4-development-v1",
  "version": 1,
  "split": "development",
  "min_case_count": 20,
  "min_micro_precision": 0.70,
  "min_micro_recall": 0.65,
  "min_micro_f1": 0.68,
  "min_macro_f1": 0.62,
  "kind_requirements": {
    "event": {"min_case_count": 10, "min_micro_f1": 0.65},
    "motivation": {"min_case_count": 5, "min_micro_f1": 0.55}
  }
}
```

Числа выше — **пример формата**, а не утверждённые пороги проекта. Реальная policy должна быть зафиксирована до соответствующего blind-прогона и основана на pilot/development данных.

## Контракт

Acceptance split может быть только:

- `development`;
- `blind`.

`train` запрещён как gate split.

Проверяются:

- минимальное число cases;
- micro precision;
- micro recall;
- micro F1;
- macro F1;
- необязательные per-kind requirements;
- необязательный exact SHA-256 fingerprint benchmark suite.

Если обязательного split или kind нет, gate завершается fail, а не подменяет его общим результатом.

## Fingerprints

Verdict содержит:

- `policy_fingerprint_sha256`;
- `suite_fingerprint_sha256`;
- `policy_id/version`;
- выбранный split;
- observed metrics;
- список checks/failures.

Это позволяет доказать, что blind verdict был рассчитан по заранее зафиксированным порогам и конкретной версии suite.

## CLI

```bash
python storymap_quality_gate.py \
  temp/storymap-suite-report.json \
  configs/storymap-quality-policy.json \
  --output temp/storymap-quality-verdict.json
```

Exit codes:

- `0` — gate passed;
- `2` — ошибка входных данных/контракта;
- `3` — benchmark корректен, но quality thresholds не пройдены.

## Переход к P5

P5 Expert Analysis Corpus не должен зависеть от того, что StoryMap «кажется правдоподобным». До blind validation экспертов нужно:

1. собрать development gold cases;
2. зафиксировать quality policy;
3. заморозить blind cases;
4. прогнать suite;
5. сохранить machine-readable verdict;
6. только затем сравнивать автоматические structural observations с экспертным корпусом.
