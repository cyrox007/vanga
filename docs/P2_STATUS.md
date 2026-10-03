# P2 Creative Team — статус

## Кандидат schema v7

Первый инкремент P2 реализует только признаки, доступные до премьеры и вычисляемые из предыдущих работ в той же роли.

Добавлены:

- `director_genre_avg_rating`;
- `director_genre_prior_count`;
- `director_recent_avg_rating`;
- `writer_genre_avg_rating`;
- `writer_genre_prior_count`;
- `writer_recent_avg_rating`;
- `director_is_writer`.

`recent_avg_rating` считается по последним 5 фильмам с годом строго меньше года target-фильма. Genre history учитывает прошлые фильмы, у которых с target есть хотя бы один общий IMDb genre.

`*_genre_prior_count=0` явно показывает отсутствие подходящей жанровой истории. Числовой fallback `6.5` остаётся только совместимым заполнителем и не должен интерпретироваться как фактическая средняя оценка.

## Train/inference parity

Training:

- базовый disk-first pipeline v6 не переписан;
- `src/creative_training.py` добавляет Creative Team block поверх него;
- temporal split и базовые historical features остаются прежними;
- `VANGA_TRAIN_CREATIVE_TEAM_FEATURES=0` полностью возвращает feature set v6.

Inference:

- `src/creative_kinovanga.py` расширяет базовый `KinoVanga`;
- новые SQL-агрегаты используют те же правила `startYear < target_year`, те же роли и лимит последних 5 работ;
- старые metadata без новых feature names не запускают Creative Team queries и продолжают работать как раньше.

## Версии схем

- schema v5 — baseline без coverage features;
- schema v6 — coverage (`*_known`, `*_prior_count`);
- schema v7 — v6 + первый Creative Team block.

Production entrypoint не разрешает публиковать v5 или v6-baseline через обычный полный retrain. Они доступны только в `--evaluation-only`/`--smoke` режимах.

## Ablation

Для P2 добавлен:

```bash
python scripts/creative_team_ablation.py
```

Он последовательно обучает на одной неизменившейся IMDb БД:

1. baseline v6 — coverage включён, Creative Team выключен;
2. candidate v7 — тот же pipeline + Creative Team.

Сравниваются MAE/RMSE/R², temporal periods/row counts, список признаков и размер модели. Скрипт не вызывает `save_trained_model` и не меняет `models/current.json`.

Перед production publication требуется полный ablation на актуальной серверной IMDb БД. Candidate v7 публикуется только если temporal quality gate не показывает недопустимую регрессию.

## Что осталось в P2

Следующие блоки выполняются отдельными инкрементами и отдельными ablation:

- director/writer trend;
- история человека как `director+writer`;
- `director_writer_pair_count` и historical pair rating;
- `director_actor_pair_count` и historical pair rating;
- previous team collaboration count;
- исследование ensemble/team cohesion.

Pair/cohesion признаки нельзя добавлять в текущий блок до отдельной temporal проверки, чтобы при ухудшении качества было понятно, какой именно набор признаков его вызвал.
