# Creative Team — candidate schema v7

Этот этап развивает P2 roadmap, но **не меняет production schema v6 по умолчанию**. Candidate v7 включается только явно и до отдельного temporal ablation не может быть опубликован через `traning.py`.

## P2-A: контекст роли и жанра

В первый блок входят:

- `director_genre_avg_rating` — средний рейтинг прошлых режиссёрских работ, имеющих хотя бы один общий жанр с целевым фильмом;
- `director_genre_prior_count` / `director_genre_known`;
- `writer_genre_avg_rating` — аналогично для сценариста;
- `writer_genre_prior_count` / `writer_genre_known`;
- `director_recent_avg_rating` — среднее последних пяти прошлых режиссёрских работ;
- `director_recent_count` / `director_recent_known`;
- `writer_recent_avg_rating` — среднее последних пяти прошлых сценарных работ;
- `writer_recent_count` / `writer_recent_known`;
- `director_is_writer` — один и тот же IMDb ID указан как режиссёр и первый сценарист проекта.

## Temporal contract

Для любого целевого фильма используются только работы с `startYear < target.startYear`.

Recent form определяется детерминированно:

1. прошлые фильмы сортируются по `startYear DESC`;
2. при одинаковом годе — по `tconst DESC`;
3. берутся первые пять уникальных фильмов.

Genre history использует пересечение жанров. Если исторический фильм совпал с целевым сразу по двум жанрам, он всё равно учитывается **один раз**.

Отсутствие истории кодируется парой:

- числовой fallback `6.5` в `*_avg_rating`;
- явные `*_known=0` и count `0`.

Это сохраняет принцип P1: fallback не должен выглядеть как фактический средний рейтинг.

## Производительность

Дополнительный batch SQL выполняется только при:

```bash
VANGA_TRAIN_CREATIVE_TEAM_FEATURES=1
```

По умолчанию флаг выключен, поэтому обычный retrain schema v6 не получает дополнительную нагрузку.

Inference ориентируется не на переменную окружения, а на `metadata.feature_names` конкретного поколения. Если активная модель не содержит Creative Team features, дополнительные запросы genre/recent history вообще не выполняются.

## Smoke и evaluation

Короткая проверка candidate v7:

```bash
python traning.py --smoke --creative-team-features
```

Полная непубликуемая оценка:

```bash
python traning.py --evaluation-only --creative-team-features
```

Запуск `python traning.py --creative-team-features` без `--smoke` или `--evaluation-only` завершается до обучения: candidate v7 пока нельзя публиковать.

## Ablation v6 → v7

После того как schema v6 сама пройдёт production validation, P2-A проверяется на том же актуальном dataset:

```bash
python scripts/creative_team_ablation.py --iterations 1500 --batch-size 10000
```

Сценарий последовательно обучает:

1. baseline v6: coverage ON, Creative Team OFF;
2. candidate v7: coverage ON, Creative Team ON.

Он проверяет неизменность `imdb.duckdb`, совпадение temporal train/test диапазонов и числа строк, сравнивает MAE/RMSE/R², число признаков и размер модели, сохраняет JSON в `temp/ablation-reports/` и **не меняет `models/current.json`**.

Даже успешный non-regression результат не означает автоматическую публикацию. Отдельным следующим изменением можно будет разрешить v7 в production entrypoint и затем пропустить полноценный retrain через quality gate.

## Следующий P2-блок

Pair/cohesion признаки намеренно не смешиваются с этим инкрементом. Отдельно будут исследованы:

- `director_writer_pair_count` и historical pair rating;
- `director_actor_pair_count` и historical pair rating;
- число предыдущих совместных работ ключевой команды;
- team cohesion / ensemble features.

Для них нужен собственный ablation, потому что стоимость SQL и риск переобучения заметно выше, чем у role-specific context.