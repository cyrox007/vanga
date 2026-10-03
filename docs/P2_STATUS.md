# P2 Creative Team — статус

## Schema v7: контекст роли и жанра

Реализованы `director_genre_*`, `writer_genre_*`, recent history и `director_is_writer`. Все исторические данные ограничены `startYear < target_year`.

## Schema v8: история пары режиссёр-сценарист

Добавлены `director_writer_pair_avg_rating`, `director_writer_pair_count`, `director_writer_pair_known`.

## Schema v9: режиссёр и первые три актёра

Добавлены `director_actor_N_pair_avg_rating`, `director_actor_N_pair_count`, `director_actor_N_pair_known` для `N = 1..3`. Это legacy personal slots, а не полный ансамбль.

## Schema v10: recent trend режиссёра и сценариста

`director_recent_trend`/`writer_recent_trend` = среднее последних 3 прошлых работ минус среднее предыдущих 3. При недостаточной истории `*_known=0`.

## Schema v11: полноценная режиссёрская команда

Legacy-признаки сохраняют primary director, а отдельный блок использует весь director-credit target-фильма:

- `director_team_size`;
- `director_team_known_ratio`;
- `director_team_avg_rating`;
- `director_team_prior_count_mean`;
- `director_team_prior_collaboration_count`;
- `director_team_prior_collaboration_avg_rating`;
- `director_team_collaboration_known`.

API принимает `directors: [...]`; старое `director` остаётся backward-compatible.

## Candidate schema v12: Full Cast Context

V12 устраняет ограничение «только первые три актёра» для общего профиля ансамбля. Legacy `actor_1..actor_3` остаются для совместимости и индивидуальных pair-признаков, но новый блок использует **всех actor/actress из IMDb `title_principals` target-фильма**.

Для каждого актёра отдельно вычисляется только прошлая история:

- общая средняя оценка прошлых фильмов и `prior_count`;
- средняя оценка прошлых фильмов, имеющих хотя бы один общий жанр с target-фильмом;
- genre-specific `prior_count`.

Персональные истории затем сворачиваются без добавления десятков высококардинальных `actor_N_id`:

- `cast_size`;
- `cast_known_ratio`;
- `cast_avg_rating`, `cast_rating_median`, `cast_rating_std`, `cast_rating_min`, `cast_rating_max`;
- `cast_prior_count_mean`, `cast_prior_count_max`;
- `cast_genre_known_ratio`;
- `cast_genre_avg_rating`, `cast_genre_rating_median`, `cast_genre_rating_std`, `cast_genre_rating_min`, `cast_genre_rating_max`;
- `cast_genre_prior_count_mean`, `cast_genre_prior_count_max`.

Таким образом каждый доступный principal actor влияет на общий профиль ансамбля, а сильная/слабая жанровая история не растворяется только в одной средней: модель получает median, spread, min/max, coverage и experience counts.

Актёр без исторических работ остаётся в знаменателе coverage и в count-агрегатах как ноль, но числовой filler `6.5` **не участвует** в средних реальных рейтингов.

API `/predict` теперь принимает до 32 актёров. Это защитный лимит публичного запроса, а training использует весь доступный principal cast из локальной IMDb БД.

## Temporal и missing-контракт

Для всех P2-схем используются только фильмы с `startYear < target_year`. Target rating, фильмы того же календарного года и будущие работы исключены.

Full Cast также использует это правило отдельно для истории каждого актёра и его жанровой репутации.

## Train/inference parity

Training:

- `src/creative_training.py` добавляет Full Cast после schema v11;
- `VANGA_TRAIN_FULL_CAST_FEATURES=0` воспроизводит schema v11;
- batch SQL возвращает персональную историю каждого principal actor, а общий Python-агрегатор строит итоговый feature vector.

Inference:

- `src/creative_kinovanga.py` сохраняет legacy top-3 personal slots;
- для v12 дополнительно разрешает и агрегирует весь переданный cast;
- `fetch_full_cast_context` использует тот же temporal/genre контракт;
- unresolved actor уменьшает coverage, но не получает фиктивную «репутацию».

## Версии схем

- v5 — baseline без coverage;
- v6 — coverage;
- v7 — genre/recent/director_is_writer;
- v8 — director↔writer;
- v9 — director↔actor top-3;
- v10 — director/writer trend;
- v11 — multi-director team;
- v12 — Full Cast Context.

Production entrypoint не разрешает публиковать v5-v11 baseline обычным полным retrain. Candidate v12 также обязан пройти quality gate.

## Ablation

- `python scripts/creative_team_ablation.py` — v6→v7;
- `python scripts/director_writer_pair_ablation.py` — v7→v8;
- `python scripts/director_actor_pair_ablation.py` — v8→v9;
- `python scripts/creative_trend_ablation.py` — v9→v10;
- `python scripts/director_team_ablation.py` — v10→v11, Full Cast принудительно выключен;
- `python scripts/full_cast_ablation.py` — v11→v12.

Все ablation непубликующие и не меняют `models/current.json`.

## Что осталось в P2

- история человека как `director+writer`;
- actor↔actor history и previous ensemble collaboration отдельным инкрементом;
- team-wide director↔writer/director↔actor aggregation;
- ensemble/team cohesion только после проверки Full Cast и raw pair history;
- серверный последовательный v6→v12 ablation и решение, какие кандидаты реально оставлять.

Связанный слой — `docs/PRODUCTION_CONTEXT.md`: студия, продюсер, franchise/shared universe, изменения производства и внешние creative consultancies.
