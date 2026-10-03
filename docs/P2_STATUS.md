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

## Candidate schema v8: история пары режиссёр-сценарист

Второй изолированный P2-инкремент добавляет:

- `director_writer_pair_avg_rating`;
- `director_writer_pair_count`;
- `director_writer_pair_known`.

Target-пара определяется теми же персональными ID, которые уже использует модель: первый режиссёр по IMDb `ordering` и первый сценарист из `title_crew.writers`.

Для исторического сотрудничества учитывается любой предыдущий фильм, где выбранный режиссёр имеет director-credit, а выбранный сценарист — writer-credit. Это позволяет не терять реальные совместные работы, где человек был со-режиссёром или со-сценаристом.

Temporal-контракт строгий: historical pair использует только фильмы с `startYear < target_year`. Рейтинг target-фильма, работы того же года и будущие фильмы не участвуют.

Если совместных работ нет:

- `director_writer_pair_count = 0`;
- `director_writer_pair_known = 0`;
- `director_writer_pair_avg_rating = 6.5` используется только как числовой filler и не считается реальной оценкой пары.

## Train/inference parity

Training:

- базовый disk-first pipeline v6 не переписан;
- `src/creative_training.py` добавляет P2-блоки поверх него;
- оба P2-блока используют одно дополнительное DuckDB-соединение, чтобы не увеличивать память на VPS;
- `VANGA_TRAIN_CREATIVE_TEAM_FEATURES=0` полностью возвращает feature set v6;
- `VANGA_TRAIN_DIRECTOR_WRITER_PAIR_FEATURES=0` при включённом Creative Team возвращает feature set v7.

Inference:

- `src/creative_kinovanga.py` расширяет базовый `KinoVanga`;
- SQL-агрегаты используют те же правила `startYear < target_year` и те же resolved person IDs;
- pair query выполняется только если соответствующие feature names присутствуют в metadata активной модели;
- v5/v6/v7 модели продолжают работать без pair-запросов.

## Версии схем

- schema v5 — baseline без coverage features;
- schema v6 — coverage (`*_known`, `*_prior_count`);
- schema v7 — v6 + genre/recent/director_is_writer;
- schema v8 — v7 + director↔writer pair history.

Production entrypoint не разрешает публиковать v5/v6/v7-baseline через обычный полный retrain. Они доступны только в `--evaluation-only`/`--smoke` режимах. По умолчанию новый полный retrain формирует candidate v8 и всё равно обязан пройти существующий quality gate.

## Ablation

### v6 → v7

```bash
python scripts/creative_team_ablation.py
```

Скрипт принудительно выключает pair block, поэтому исторический эксперимент остаётся воспроизводимым:

1. baseline v6 — coverage включён, Creative Team выключен;
2. candidate v7 — Creative Team включён, pair выключен.

### v7 → v8

```bash
python scripts/director_writer_pair_ablation.py
```

На одной неизменившейся IMDb БД последовательно сравниваются:

1. baseline v7 — coverage + Creative Team, pair выключен;
2. candidate v8 — тот же pipeline + director↔writer pair history.

Оба ablation сравнивают MAE/RMSE/R², temporal periods/row counts, список признаков и размер модели. Скрипты не вызывают `save_trained_model` и не меняют `models/current.json`.

Перед production publication требуется полный ablation на актуальной серверной IMDb БД. Candidate schema может быть опубликован только если temporal quality gate не показывает недопустимую регрессию.

## Что осталось в P2

Следующие блоки выполняются отдельными инкрементами и отдельными ablation:

- director/writer trend;
- история человека как `director+writer`;
- `director_actor_pair_count` и historical pair rating;
- previous team collaboration count;
- исследование ensemble/team cohesion.

Каждый следующий pair/cohesion блок изолируется от предыдущего, чтобы при изменении temporal MAE было понятно, какой именно набор признаков дал эффект.
