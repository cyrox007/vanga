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

## Schema v12: Full Cast Context

V12 устраняет ограничение «только первые три актёра» для общего профиля ансамбля. Legacy `actor_1..actor_3` остаются для совместимости и индивидуальных pair-признаков, но новый блок использует **всех actor/actress из IMDb `title_principals` target-фильма**.

Для каждого актёра отдельно вычисляется только прошлая история:

- общая средняя оценка прошлых фильмов и `prior_count`;
- средняя оценка прошлых фильмов, имеющих хотя бы один общий жанр с target-фильмом;
- genre-specific `prior_count`.

Итоговые агрегаты:

- `cast_size`, `cast_known_ratio`;
- `cast_avg_rating`, `cast_rating_median`, `cast_rating_std`, `cast_rating_min`, `cast_rating_max`;
- `cast_prior_count_mean`, `cast_prior_count_max`;
- `cast_genre_known_ratio`;
- `cast_genre_avg_rating`, `cast_genre_rating_median`, `cast_genre_rating_std`, `cast_genre_rating_min`, `cast_genre_rating_max`;
- `cast_genre_prior_count_mean`, `cast_genre_prior_count_max`.

Актёр без исторических работ остаётся в знаменателе coverage и в count-агрегатах как ноль, но filler `6.5` не участвует в средних реальных рейтингов.

## Candidate schema v13: Actor Pair History / Ensemble Familiarity

V13 отвечает на отдельный вопрос: **насколько актёры текущего ансамбля уже знакомы друг с другом по прошлым фильмам**. Это ещё не единый `team_cohesion score`: сначала сохраняются прозрачные pair-level агрегаты, которые можно честно проверить ablation-экспериментом.

Для всех unordered actor↔actor пар текущего principal cast считаются только фильмы с `startYear < target_year`, где оба человека имели actor/actress credit.

Новые признаки:

- `cast_pair_total` — число возможных пар `N*(N-1)/2`;
- `cast_pair_known_ratio` — доля пар хотя бы с одной прошлой совместной работой;
- `cast_pair_prior_collaboration_mean`;
- `cast_pair_prior_collaboration_median`;
- `cast_pair_prior_collaboration_max`;
- `cast_pair_prior_rating_avg`;
- `cast_pair_prior_rating_median`;
- `cast_pair_prior_rating_std`.

Пары без совместной истории получают count=0 и остаются в знаменателе familiarity. Filler `6.5` не используется как реальная оценка неизвестной пары: rating-агрегаты считаются только по парам с реальной историей, а при полном отсутствии истории возвращается числовой fallback 6.5 вместе с `known_ratio=0`.

Если часть cast не разрешена, пары с `Unknown` также остаются в общем `pair_total` и понижают coverage/familiarity, но не получают фиктивных совместных фильмов.

## Temporal и missing-контракт

Для всех P2-схем используются только фильмы с `startYear < target_year`. Target rating, фильмы того же календарного года и будущие работы исключены.

Это правило отдельно применяется к индивидуальной актёрской истории v12 и ко всем actor↔actor pair histories v13.

## Train/inference parity

Training:

- `src/creative_training.py` добавляет v13 после Full Cast;
- `VANGA_TRAIN_CAST_PAIR_FEATURES=0` воспроизводит schema v12;
- batch SQL строит все unordered пары principal cast и их прошлые совместные фильмы.

Inference:

- используются те же resolved actor IDs, которые применяет Full Cast;
- число пар считается по всему переданному cast, включая unresolved участников;
- `fetch_cast_pair_context` использует тот же strict temporal cutoff;
- старые модели без v13 feature names не выполняют cast-pair запросы.

## Версии схем

- v5 — baseline без coverage;
- v6 — coverage;
- v7 — genre/recent/director_is_writer;
- v8 — director↔writer;
- v9 — director↔actor top-3;
- v10 — director/writer trend;
- v11 — multi-director team;
- v12 — Full Cast Context;
- v13 — actor↔actor pair history / ensemble familiarity.

Production entrypoint не разрешает публиковать v5-v12 baseline обычным полным retrain. Candidate v13 также обязан пройти temporal quality gate.

## Ablation

- `python scripts/creative_team_ablation.py` — v6→v7;
- `python scripts/director_writer_pair_ablation.py` — v7→v8;
- `python scripts/director_actor_pair_ablation.py` — v8→v9;
- `python scripts/creative_trend_ablation.py` — v9→v10;
- `python scripts/director_team_ablation.py` — v10→v11;
- `python scripts/full_cast_ablation.py` — v11→v12, v13 принудительно выключен;
- `python scripts/cast_pair_ablation.py` — v12→v13.

Все ablation непубликующие и не меняют `models/current.json`.

## Что осталось в P2

- история человека как `director+writer`;
- team-wide director↔writer/director↔actor aggregation;
- после server ablation v13 — исследование более общего ensemble/team cohesion, но только как новый отдельный инкремент;
- серверный последовательный v6→v13 ablation и решение, какие кандидаты реально оставлять.

Связанный слой — `docs/PRODUCTION_CONTEXT.md`: студия, продюсер, franchise/shared universe, изменения производства и внешние creative consultancies.
