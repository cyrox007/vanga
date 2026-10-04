# Expert Analysis Corpus — readiness gate и испытания

## Что уже реализовано

Expert Corpus хранит только структурированные данные анализа:

`expert → material → case → claim → evidence`

и разделяет:

`observation → structural_consequence → expert_interpretation`.

Полные сторонние тексты и транскрипты по умолчанию не сохраняются.

Blind validation, consensus и agreement/transfer реализованы отдельными слоями. Новый readiness gate проверяет, что реальный корпус достаточно наполнен, прежде чем результаты этих инструментов считать содержательными.

## Readiness gate

Запуск:

```bash
.venv/bin/python scripts/expert_corpus_gate.py
```

По умолчанию gate требует минимум:

- 2 независимых expert profile;
- материалы минимум для двух экспертов;
- 5 train cases;
- 2 blind cases;
- 2 external-transfer cases;
- 10 структурированных claims;
- supporting evidence для каждого claim.

Отсутствие contradicting evidence пока является предупреждением, а не автоматическим blocker, но указывает на риск confirmation bias.

Порог пилота можно менять:

```bash
.venv/bin/python scripts/expert_corpus_gate.py \
  --min-train 5 \
  --min-blind 2 \
  --min-external 2 \
  --min-claims 10
```

## Порядок реального пилота

1. Внести профили Красного Циника и BadComedian.
2. Для каждого материала сохранить URL, название, дату публикации/получения и тип источника.
3. Не копировать полный текст обзора без разрешения/совместимой лицензии.
4. Создать cases и заранее назначить split.
5. Размечать claim только через отдельные observation, structural consequence и interpretation.
6. Для каждого claim добавить supporting evidence; contradicting evidence хранить отдельно.
7. Проверить readiness gate.
8. Экспортировать blind manifest без скрытой экспертной разметки.
9. Получить независимый StoryMap/Analyzer prediction run.
10. Только после этого выполнить post-hoc blind evaluation.
11. Отдельно проверить external-transfer фильмы, которых соответствующий эксперт не разбирал.
12. Запустить consensus/agreement-transfer и leave-one-expert-out проверку.

## Blind validation

Экспорт закрытого manifest:

```bash
.venv/bin/python scripts/expert_blind_validation.py manifest \
  --split blind \
  --output temp/expert-blind-manifest.json
```

После независимого анализа:

```bash
.venv/bin/python scripts/expert_blind_validation.py evaluate \
  temp/expert-blind-predictions.json \
  --split blind \
  --output temp/expert-blind-result.json
```

Blind predictions не должны формироваться из скрытых expert claims.

## Что считается успехом

Цель — не воспроизводить манеру или оценку конкретного критика. Успехом считается перенос структурного принципа на новые фильмы:

- causality/motivation;
- setup/payoff;
- continuity;
- adaptation/context loss;
- character/worldbuilding/theme/ending preservation;
- другие dimensions, которые подтверждаются независимым structural evidence.

Только устойчивые findings переходят в P6 как гипотезы pre-release proxy. Пострелизные expert claims никогда не становятся прямыми признаками будущего фильма.
