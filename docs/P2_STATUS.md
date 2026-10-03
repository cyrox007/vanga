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

## Schema v8: история пары режиссёр-сценарист

Добавлены `director_writer_pair_avg_rating`, `director_writer_pair_count`, `director_writer_pair_known`. Исторический фильм учитывается только при `startYear < target_year`.

## Schema v9: режиссёр и первые три актёра

Добавлены `director_actor_N_pair_avg_rating`, `director_actor_N_pair_count`, `director_actor_N_pair_known` для `N = 1..3`. Слоты совпадают с первыми тремя actor/actress по IMDb ordering.

## Schema v10: recent trend режиссёра и сценариста

Добавлены `director_recent_trend`, `director_recent_trend_known`, `writer_recent_trend`, `writer_recent_trend_known`.

Trend вычисляется как:

`mean(3 самых свежих прошлых работ) - mean(3 предыдущих прошлых работ)`.

Если работ меньше шести, `trend=0`, но `trend_known=0`.

## Candidate schema v11: полноценная режиссёрская команда

Предыдущие схемы использовали primary director для legacy feature contract. Это недостаточно для фильмов с полноценной сорежиссурой, поэтому v11 добавляет отдельный multi-director block, не ломая старые признаки.

API принимает:

- legacy `director: "Имя"`;
- новый `directors: ["Имя 1", "Имя 2", ...]`.

Если переданы оба поля, `director` сохраняется как primary/backward-compatible значение, а полный список используется director-team блоком.

Новые признаки:

- `director_team_size`;
- `director_team_known_ratio`;
- `director_team_avg_rating`;
- `director_team_prior_count_mean`;
- `director_team_prior_collaboration_count`;
- `director_team_prior_collaboration_avg_rating`;
- `director_team_collaboration_known`.

`director_team_prior_collaboration_*` учитывает предыдущий фильм только если **весь текущий набор режиссёров** имеет director-credit в этом фильме. Это позволяет корректно описывать постоянные режиссёрские дуэты/команды, а не случайно приписывать историю одному primary director.

## Temporal и missing-контракт

Для всех P2-схем используются только фильмы с `startYear < target_year`. Target rating, фильмы того же календарного года и будущие работы не участвуют.

Для pair history без данных используются `count=0`, `known=0`, а `6.5` остаётся только числовым filler.

Для director-team:

- `director_team_known_ratio` показывает долю режиссёров с реальной прошлой историей;
- `director_team_collaboration_known=0` отделяет отсутствие прошлых совместных фильмов от реального среднего;
- `director_team_size` хранит фактическое число режиссёров target-фильма.

## Train/inference parity

Training:

- `src/creative_training.py` добавляет P2-блоки поверх базового disk-first pipeline;
- v11 получает полный список director-credit прямо из IMDb `title_principals`;
- `VANGA_TRAIN_DIRECTOR_TEAM_FEATURES=0` воспроизводит schema v10;
- все P2-блоки используют одно дополнительное DuckDB-соединение.

Inference:

- `src/creative_kinovanga.py` принимает несколько режиссёров;
- каждый режиссёр разрешается отдельно через существующий input resolver;
- legacy признаки продолжают использовать первого/primary director;
- v11 вычисляет отдельный team context по всем resolved director IDs;
- старые модели без v11 feature names не используют director-team признаки.

## Версии схем

- schema v5 — baseline без coverage features;
- schema v6 — coverage (`*_known`, `*_prior_count`);
- schema v7 — v6 + genre/recent/director_is_writer;
- schema v8 — v7 + director↔writer pair history;
- schema v9 — v8 + director↔actor pair history;
- schema v10 — v9 + director/writer recent trend 3-vs-3;
- schema v11 — v10 + multi-director team history.

Production entrypoint не разрешает случайно публиковать неполные baseline-схемы. Новый полный retrain формирует candidate v11 и всё равно обязан пройти quality gate.

## Ablation

- `python scripts/creative_team_ablation.py` — v6→v7;
- `python scripts/director_writer_pair_ablation.py` — v7→v8;
- `python scripts/director_actor_pair_ablation.py` — v8→v9;
- `python scripts/creative_trend_ablation.py` — v9→v10;
- `python scripts/director_team_ablation.py` — v10→v11.

Каждый эксперимент сравнивает MAE/RMSE/R², temporal periods/row counts, feature set и размер модели и не меняет `models/current.json`.

Перед production publication требуется полный последовательный ablation на актуальной серверной IMDb БД.

## Что осталось в P2

- история человека как `director+writer`;
- team-wide director↔writer/director↔actor aggregation как отдельное исследование;
- previous team collaboration aggregate;
- ensemble/team cohesion только после проверки raw team/pair history;
- серверный v6→v11 ablation и решение, какие кандидаты реально оставлять.

Связанный следующий слой — `docs/PRODUCTION_CONTEXT.md`: студия, продюсер, франшиза/shared universe, изменения производства и внешние creative consultancies.
