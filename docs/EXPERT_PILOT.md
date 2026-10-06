# Первый реальный Expert Corpus pilot

## Цель

Этот pilot фиксирует стартовую выборку **до** структурированной разметки. Это нужно, чтобы не выбирать удобные фильмы постфактум после того, как уже известны результаты Analyzer.

Manifest:

`data/expert/pilot_sources.json`

В нём хранятся только:

- профиль эксперта;
- название материала;
- URL публичного источника;
- дата публикации, когда она подтверждена;
- case и заранее назначенный split;
- связь case ↔ material.

Полные тексты/транскрипты и готовые expert claims в manifest не включаются.

## Состав

Первый pilot содержит 11 cases двух независимых профилей:

- Красный Циник / Red Cynic;
- BadComedian / Евгений Баженов.

Split фиксирован заранее:

- `train`: 5 cases;
- `development`: 2 cases;
- `blind`: 2 cases;
- `external_transfer`: 2 cases.

`blind` и `external_transfer` нельзя переносить в train после просмотра результатов.

## Импорт

```bash
.venv/bin/python scripts/expert_corpus.py import data/expert/pilot_sources.json
.venv/bin/python scripts/expert_corpus.py stats
```

Сразу после metadata-only импорта readiness gate **должен завершиться ошибкой**:

```bash
.venv/bin/python scripts/expert_corpus_gate.py
```

Это ожидаемое состояние: в pilot manifest намеренно нет выдуманных claims/evidence.

## Разметка train/development

Для выбора следующего материала используется отдельный helper CLI:

```bash
.venv/bin/python scripts/expert_corpus_annotate.py list --split train
.venv/bin/python scripts/expert_corpus_annotate.py template <case_id> --output annotation.json
```

После заполнения шаблона сначала выполняется безопасная проверка. `validate` открывает транзакцию, проверяет claims/evidence текущими валидаторами Expert Corpus и откатывает транзакцию, поэтому база не изменяется:

```bash
.venv/bin/python scripts/expert_corpus_annotate.py validate annotation.json
```

После успешной проверки разметка применяется тем же helper CLI:

```bash
.venv/bin/python scripts/expert_corpus_annotate.py apply annotation.json
```

`apply` записывает claims и evidence в одной транзакции. Если любая строка невалидна, вся операция откатывается и частичная разметка в базе не остаётся. Повторное применение того же bundle безопасно благодаря upsert-семантике идентификаторов.

Annotation bundle не должен содержать `profiles`, `cases`, `materials` или `case_materials`: metadata pilot зафиксированы заранее и не меняются во время разметки. Незаменённые поля `ЗАПОЛНИТЬ:` блокируются как при `validate`, так и при `apply`.

Для каждого открытого case:

1. изучается оригинальный материал по сохранённому URL;
2. фиксируется таймкод/раздел;
3. отдельно записывается наблюдаемое `observation`;
4. отдельно — `structural_consequence`;
5. отдельно — `expert_interpretation`;
6. указывается `dimension` и `change_type`;
7. добавляется supporting evidence;
8. при наличии — contradicting evidence;
9. StoryMap/StoryDiff evidence обязательно получает `reference_id`.

Нельзя вставлять в corpus полный транскрипт обзора.

## Blind split

До независимого Analyzer run экспертная разметка blind cases не должна попадать в development-процесс.

Правильная последовательность:

1. подготовить source/film summaries и StoryMap/StoryDiff;
2. экспортировать blind manifest;
3. получить predictions Analyzer без чтения sealed expert gold;
4. только после фиксации prediction artifact открыть/внести gold annotations;
5. выполнить post-hoc evaluation.

## External transfer

Текущий `external_transfer` контракт реализует **held-out expert transfer**:

- gold конкретного эксперта существует как sealed evaluation data;
- prediction process декларирует, что этот expert profile полностью исключён из development/training;
- затем результат сравнивается post-hoc с его gold claims.

Это не следует путать с ещё более строгим будущим benchmark «фильм не разбирал ни один эксперт». Для него при необходимости должен появиться отдельный evaluation contract, а не переиспользование текущего split с другой семантикой.

## Условия перехода к следующему этапу

Pilot готов к содержательному blind/transfer испытанию только когда:

- оба expert profile реально имеют размеченные claims;
- минимум 5 train cases размечены;
- минимум 2 blind cases имеют sealed gold;
- минимум 2 external-transfer cases имеют sealed held-out-expert gold;
- есть не менее 10 claims;
- каждый claim имеет supporting evidence;
- `scripts/expert_corpus_gate.py` проходит без blockers.

После этого запускаются blind validation, agreement/consensus и held-out expert transfer. Только устойчивые структурные findings могут перейти в P6 как гипотезы pre-release proxy.
