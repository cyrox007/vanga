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

## Candidate schema v9: режиссёр и первые три актёра

Третий изолированный P2-инкремент добавляет по три признака для каждого из первых трёх actor/actress по IMDb `ordering`:

- `director_actor_N_pair_avg_rating`;
- `director_actor_N_pair_count`;
- `director_actor_N_pair_known`, где `N = 1..3`.

Target-режиссёр и порядок актёров совпадают с уже существующим training contract `src.data_filtr`: первый director и первые три actor/actress. Исторический фильм учитывается, если выбранный режиссёр имеет director-credit, а конкретный актёр — actor/actress-credit.

Признаки намеренно остаются по слотам, а не сворачиваются сразу в один «cohesion score». Это позволяет сначала проверить, есть ли у реальной истории конкретных пар сигнал на temporal holdout, и только после этого исследовать агрегированные командные метрики.

## Temporal и missing-контракт

Для v7/v8/v9 действует одно правило: используются только исторические фильмы с `startYear < target_year`. Target rating, фильмы того же календарного года и будущие работы не участвуют.

Если истории конкретной пары нет:

- `*_pair_count = 0`;
- `*_pair_known = 0`;
- `*_pair_avg_rating = 6.5` — только числовой filler для модели, а не фактическая оценка пары.

## Train/inference parity

Training:

- базовый disk-first pipeline v6 не переписан;
- `src/creative_training.py` добавляет P2-блоки поверх него;
- все P2-блоки используют одно дополнительное DuckDB-соединение, чтобы не увеличивать число соединений и память на VPS;
- `VANGA_TRAIN_CREATIVE_TEAM_FEATURES=0` возвращает feature set v6;
- `VANGA_TRAIN_DIRECTOR_WRITER_PAIR_FEATURES=0` при включённом Creative Team возвращает v7;
- `VANGA_TRAIN_DIRECTOR_ACTOR_PAIR_FEATURES=0` при включённых предыдущих блоках возвращает v8.

Inference:

- `src/creative_kinovanga.py` расширяет базовый `KinoVanga`;
- используются те же resolved person IDs и тот же порядок первых трёх актёров;
- SQL-агрегаты используют тот же strict temporal cutoff;
- pair queries запускаются только для feature names, реально присутствующих в metadata активной модели;
- старые модели v5-v8 не выполняют v9-запросы.

## Версии схем

- schema v5 — baseline без coverage features;
- schema v6 — coverage (`*_known`, `*_prior_count`);
- schema v7 — v6 + genre/recent/director_is_writer;
- schema v8 — v7 + director↔writer pair history;
- schema v9 — v8 + director↔actor pair history для actor slots 1..3.

Production entrypoint не разрешает публиковать v5-v8 baseline через обычный полный retrain. Они доступны только в `--evaluation-only`/`--smoke` режимах. По умолчанию новый полный retrain формирует candidate v9 и всё равно обязан пройти существующий quality gate.

## Ablation

### v6 → v7

```bash
python scripts/creative_team_ablation.py
```

Pair-блоки выключены, поэтому сравнивается только первый Creative Team increment.

### v7 → v8

```bash
python scripts/director_writer_pair_ablation.py
```

Director↔actor block принудительно выключен, поэтому сравнивается только director↔writer history.

### v8 → v9

```bash
python scripts/director_actor_pair_ablation.py
```

На одной неизменившейся IMDb БД сравниваются:

1. baseline v8 — coverage + Creative Team + director↔writer;
2. candidate v9 — тот же pipeline + director↔actor history для первых трёх актёров.

Все ablation сравнивают MAE/RMSE/R², temporal periods/row counts, список признаков и размер модели. Скрипты не вызывают `save_trained_model` и не меняют `models/current.json`.

Перед production publication требуется полный последовательный ablation на актуальной серверной IMDb БД. Candidate schema может быть опубликован только если temporal quality gate не показывает недопустимую регрессию.

## Что осталось в P2

Следующие блоки также должны идти отдельными инкрементами:

- director/writer trend;
- история человека как `director+writer`;
- previous team collaboration count как отдельный агрегат;
- исследование ensemble/team cohesion только после проверки raw pair history.

Нельзя автоматически считать большое число прошлых совместных работ признаком качества: cohesion-признаки остаются исследовательскими до отдельного ablation.
