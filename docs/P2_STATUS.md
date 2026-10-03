# P2 Creative Team — статус

## Schema v7: контекст роли и жанра

Первый инкремент P2 реализует признаки, доступные до премьеры и вычисляемые из предыдущих работ в той же роли:

- `director_genre_avg_rating`;
- `director_genre_prior_count`;
- `director_recent_avg_rating`;
- `writer_genre_avg_rating`;
- `writer_genre_prior_count`;
- `writer_recent_avg_rating`;
- `director_is_writer`.

`recent_avg_rating` считается по последним 5 фильмам с годом строго меньше года target-фильма. Genre history учитывает прошлые фильмы, у которых с target есть хотя бы один общий IMDb genre.

`*_genre_prior_count=0` явно показывает отсутствие подходящей жанровой истории. Числовой fallback `6.5` остаётся только совместимым заполнителем и не должен интерпретироваться как фактическая средняя оценка.

## Schema v8: история пары режиссёр-сценарист

Второй изолированный P2-инкремент добавляет:

- `director_writer_pair_avg_rating`;
- `director_writer_pair_count`;
- `director_writer_pair_known`.

Target-пара определяется теми же персональными ID, которые уже использует модель: первый режиссёр по IMDb `ordering` и первый сценарист из `title_crew.writers`.

Для исторического сотрудничества учитывается любой предыдущий фильм, где выбранный режиссёр имеет director-credit, а выбранный сценарист — writer-credit. Среднее и count считаются по уникальным фильмам.

## Schema v9: режиссёр и первые три актёра

Третий изолированный P2-инкремент добавляет по три признака для каждого из первых трёх actor/actress по IMDb `ordering`:

- `director_actor_N_pair_avg_rating`;
- `director_actor_N_pair_count`;
- `director_actor_N_pair_known`, где `N = 1..3`.

Target-режиссёр и порядок актёров совпадают с существующим training contract `src.data_filtr`: первый director и первые три actor/actress. Исторический фильм учитывается, если выбранный режиссёр имеет director-credit, а конкретный актёр — actor/actress-credit.

Признаки намеренно остаются по слотам, а не сворачиваются сразу в один «cohesion score». Это позволяет сначала проверить raw pair history на temporal holdout и только потом исследовать агрегированную командную совместимость.

## Candidate schema v10: recent trend режиссёра и сценариста

Четвёртый изолированный P2-инкремент добавляет:

- `director_recent_trend`;
- `director_recent_trend_known`;
- `writer_recent_trend`;
- `writer_recent_trend_known`.

Trend не является субъективной оценкой «растёт/падает». Он вычисляется детерминированно:

`mean(3 самых свежих прошлых работ) - mean(3 предыдущих прошлых работ)`.

Для значения нужны все 6 исторических фильмов в соответствующей роли. Если работ меньше шести, `trend=0`, но одновременно `trend_known=0`, поэтому отсутствие истории не смешивается с реально ровным трендом.

Target rating, фильмы того же календарного года и будущие работы исключаются условием `startYear < target_year`. Training и inference используют одинаковую сортировку: год по убыванию, затем `tconst` по убыванию для детерминированного порядка внутри года.

## Temporal и missing-контракт

Для всех P2-схем действует одно правило: используются только исторические фильмы с `startYear < target_year`. Target rating, фильмы того же календарного года и будущие работы не участвуют.

Для pair history без данных:

- `*_pair_count = 0`;
- `*_pair_known = 0`;
- `*_pair_avg_rating = 6.5` — только числовой filler для модели.

Для trend без достаточных шести работ:

- `*_recent_trend = 0`;
- `*_recent_trend_known = 0`.

## Train/inference parity

Training:

- базовый disk-first pipeline v6 не переписан;
- `src/creative_training.py` добавляет P2-блоки поверх него;
- все P2-блоки используют одно дополнительное DuckDB-соединение, чтобы не увеличивать число соединений и память на VPS;
- `VANGA_TRAIN_CREATIVE_TEAM_FEATURES=0` возвращает v6;
- `VANGA_TRAIN_DIRECTOR_WRITER_PAIR_FEATURES=0` возвращает v7;
- `VANGA_TRAIN_DIRECTOR_ACTOR_PAIR_FEATURES=0` возвращает v8;
- `VANGA_TRAIN_CREATIVE_TREND_FEATURES=0` возвращает v9.

Inference:

- `src/creative_kinovanga.py` расширяет базовый `KinoVanga`;
- используются те же resolved person IDs и role-specific histories;
- SQL-агрегаты используют тот же strict temporal cutoff;
- дополнительные queries выполняются только для feature names, реально присутствующих в metadata активной модели;
- старые модели v5-v9 не выполняют v10 trend-запросы.

## Версии схем

- schema v5 — baseline без coverage features;
- schema v6 — coverage (`*_known`, `*_prior_count`);
- schema v7 — v6 + genre/recent/director_is_writer;
- schema v8 — v7 + director↔writer pair history;
- schema v9 — v8 + director↔actor pair history для actor slots 1..3;
- schema v10 — v9 + director/writer recent trend 3-vs-3.

Production entrypoint не разрешает публиковать v5-v9 baseline через обычный полный retrain. Они доступны только в `--evaluation-only`/`--smoke` режимах. По умолчанию новый полный retrain формирует candidate v10 и всё равно обязан пройти существующий quality gate.

## Ablation

### v6 → v7

```bash
python scripts/creative_team_ablation.py
```

Pair/trend-блоки выключены, поэтому сравнивается только первый Creative Team increment.

### v7 → v8

```bash
python scripts/director_writer_pair_ablation.py
```

Director↔actor и trend блоки принудительно выключены.

### v8 → v9

```bash
python scripts/director_actor_pair_ablation.py
```

Trend принудительно выключен, поэтому исторический эксперимент остаётся воспроизводимым после появления schema v10.

### v9 → v10

```bash
python scripts/creative_trend_ablation.py
```

На одной неизменившейся IMDb БД сравниваются:

1. baseline v9 — coverage + Creative Team + director↔writer + director↔actor;
2. candidate v10 — тот же pipeline + director/writer recent trend 3-vs-3.

Все ablation сравнивают MAE/RMSE/R², temporal periods/row counts, список признаков и размер модели. Скрипты не вызывают `save_trained_model` и не меняют `models/current.json`.

Перед production publication требуется полный последовательный ablation на актуальной серверной IMDb БД. Candidate schema может быть опубликован только если temporal quality gate не показывает недопустимую регрессию.

## Что осталось в P2

Следующие блоки также должны идти отдельными инкрементами:

- история человека как `director+writer`;
- previous team collaboration count как отдельный агрегат;
- исследование ensemble/team cohesion только после проверки raw pair history;
- после серверного ablation решить, какие из schema v7-v10 реально сохранять в production feature set.

Нельзя автоматически считать положительный trend или большое число прошлых совместных работ признаком качества: это только кандидаты, полезность которых должна быть подтверждена temporal ablation.
