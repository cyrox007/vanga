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

Legacy-признаки сохраняют primary director, а отдельный блок использует весь director-credit target-фильма: размер команды, coverage, общую историю и предыдущие совместные фильмы полного режиссёрского состава. API принимает `directors: [...]`; старое `director` остаётся backward-compatible.

## Schema v12: Full Cast Context

V12 сохраняет legacy `actor_1..actor_3`, но общий профиль строит по всем `actor/actress` из IMDb `title_principals` target-фильма. Для каждого актёра отдельно считается общая и жанровая прошлая история, после чего она агрегируется в `cast_*`: coverage, mean/median/std/min/max и experience counts. Filler `6.5` не участвует в средних реальных рейтингов.

## Schema v13: Actor Pair History / Ensemble Familiarity

V13 считает все unordered actor↔actor пары principal cast и их прошлые совместные фильмы. Основные признаки: `cast_pair_total`, `cast_pair_known_ratio`, collaboration mean/median/max и pair-rating avg/median/std. Пары без истории остаются в знаменателе familiarity как нули; unresolved actor не получает фиктивной истории.

## Candidate schema v14: director+writer dual-role history

V14 отделяет ещё одну закономерность от обычной режиссёрской или сценарной репутации: **опыт человека в фильмах, где он одновременно имел director-credit и writer-credit**.

Для режиссёрской стороны учитывается весь текущий multi-director team, а не только primary director:

- `director_team_dual_role_known_ratio`;
- `director_team_dual_role_avg_rating`;
- `director_team_dual_role_prior_count_mean`;
- `director_team_dual_role_prior_count_max`.

Для текущего сценариста по существующему first-writer contract:

- `writer_dual_role_avg_rating`;
- `writer_dual_role_prior_count`;
- `writer_dual_role_known`;
- `writer_is_in_director_team`.

`writer_is_in_director_team` не является оценкой качества: это только явный факт, что текущий сценарист одновременно входит в режиссёрскую команду target-фильма.

Если из двух режиссёров dual-role история есть только у одного, `known_ratio=0.5`; режиссёр без такой истории входит в count-агрегаты как ноль, но filler `6.5` не участвует в среднем реальных рейтингов.

## Temporal и missing-контракт

Для всех P2-схем используются только фильмы с `startYear < target_year`. Target rating, фильмы того же календарного года и будущие работы исключены.

Для v14 исторический фильм учитывается человеку только если на одном и том же prior title у него есть и director-credit, и writer-credit. Это не то же самое, что история пары «один режиссёр + другой сценарист» из v8.

## Train/inference parity

Training:

- `src/creative_training.py` добавляет v14 после actor-pair v13;
- `VANGA_TRAIN_DUAL_ROLE_FEATURES=0` воспроизводит schema v13;
- batch SQL использует весь director team и first writer;
- все P2-блоки продолжают использовать одно дополнительное DuckDB-соединение.

Inference:

- использует те же resolved director IDs и writer ID;
- `fetch_dual_role_context` повторяет strict temporal и missing-семантику training;
- старые модели без v14 feature names не выполняют dual-role запросы.

## Версии схем

- v5 — baseline без coverage;
- v6 — coverage;
- v7 — genre/recent/director_is_writer;
- v8 — director↔writer;
- v9 — director↔actor top-3;
- v10 — director/writer trend;
- v11 — multi-director team;
- v12 — Full Cast Context;
- v13 — actor↔actor pair history / ensemble familiarity;
- v14 — director+writer dual-role history.

Production entrypoint не разрешает случайно публиковать v5-v13 baseline обычным полным retrain. Candidate v14 также обязан пройти temporal quality gate.

## Ablation

- `python scripts/creative_team_ablation.py` — v6→v7;
- `python scripts/director_writer_pair_ablation.py` — v7→v8;
- `python scripts/director_actor_pair_ablation.py` — v8→v9;
- `python scripts/creative_trend_ablation.py` — v9→v10;
- `python scripts/director_team_ablation.py` — v10→v11;
- `python scripts/full_cast_ablation.py` — v11→v12;
- `python scripts/cast_pair_ablation.py` — v12→v13, v14 принудительно выключен;
- `python scripts/dual_role_ablation.py` — v13→v14.

Все ablation непубликующие и не меняют `models/current.json`.

## Что осталось в P2

- team-wide director↔writer/director↔actor aggregation для всех режиссёров и всего cast;
- previous key-team collaboration aggregate как отдельный прозрачный блок;
- ensemble/team cohesion только после server ablation raw history-признаков;
- серверный последовательный v6→v14 ablation и решение, какие кандидаты реально оставлять.

Связанный слой — `docs/PRODUCTION_CONTEXT.md`: студия, продюсер, franchise/shared universe, изменения производства и внешние creative consultancies.
