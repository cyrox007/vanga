# Roadmap Vanga

Этот файл — единая точка фиксации планов развития Vanga. Если идея обсуждалась, но не попала сюда, она не считается частью согласованного roadmap.

Статусы:

- `[x]` — реализовано и слито;
- `[~]` — фундамент есть, требуется довести/выкатить;
- `[ ]` — запланировано;
- `[R]` — исследовательская задача: сначала доказать пользу на данных.

## Принципы, которые нельзя нарушать

1. **Pre-release Vanga использует только данные, известные до премьеры.** Любые полные сюжеты, критические обзоры и фактические рейтинги после выхода запрещены во входных признаках прогноза.
2. **Retrospective Analysis отделён от pre-release inference.** Пострелизные признаки имеют namespace `retro_adapt_*` и живут в отдельной `adaptation.duckdb`.
3. **Новые признаки не публикуются только потому, что кажутся логичными.** Их польза проверяется на temporal holdout/ablation; публикацию защищает quality gate.
4. **Не путать отсутствие данных с нейтральным качеством.** `unknown` должен быть отдельным состоянием, а не молча превращаться в среднее значение 6.5.
5. **Эксперт — учитель методики, а не абсолютная истина.** Наблюдаемый факт, структурное следствие и оценочное мнение хранятся раздельно.
6. **Ограничения VPS обязательны.** Не возвращать тяжёлые ансамбли и двойную загрузку CatBoost; inference должен переживать неудачный retrain.
7. **Сторонние обзоры не копируются целиком без подходящей лицензии/разрешения.** По умолчанию сохраняются ссылка, автор, таймкод/раздел и наша структурированная аннотация.
8. **Экспертные профили не смешиваются в единую вкусовую шкалу.** Согласие и расхождение экспертов анализируются как отдельные сигналы, а не усредняются в «правильную оценку».
9. **Студия, консультант, франшиза или культурное движение не являются автоматическим плюсом/минусом.** В модель допускаются только измеримые производственные факты и их temporal-валидированные proxy.
10. **Несколько режиссёров — нормальный случай, а не исключение.** Нельзя без отдельного признака сводить полноценную режиссёрскую команду к одному человеку.
11. **Актёрский ансамбль нельзя сводить к трём фамилиям.** Legacy top-3 могут сохраняться как персональные признаки, но общий cast-контекст должен учитывать каждого доступного principal actor/actress, его общую и жанровую историю и явное покрытие данными.
12. **Совместимость ансамбля сначала измеряется прозрачными связями.** Нельзя сразу вводить непрозрачный `cohesion score`: сначала отдельно проверяются actor↔actor/director↔actor/director↔writer histories и только после temporal ablation допускается более общий агрегат.

## Уже реализовано

- [x] Отдельный локальный Vanga API на `127.0.0.1:9100`.
- [x] Disk-first training, один поток CatBoost, лимиты RAM/CPU/swap и размера модели.
- [x] Атомарные поколения модели через `models/releases/*` и `current.json`.
- [x] Temporal train/test split и historical features только из прошлого.
- [x] Quality metrics: MAE, RMSE, R², temporal holdout metadata.
- [x] Empirical uncertainty на основе квантилей ошибки temporal holdout.
- [x] Quality gate: сопоставимый retrain не публикуется при недопустимой регрессии MAE.
- [x] Role-specific history режиссёров и актёров, train/inference parity.
- [x] Сценарист как отдельный pre-release signal: `writer_id`, `writer_avg_rating`.
- [x] IMDb `title.crew`, нормализованная `title_writers`.
- [x] Русский alias resolver и fuzzy autocomplete через Wikidata с локальной IMDb-проверкой.
- [x] Movie/person discovery API и batch lookup текущих IMDb ratings.
- [x] SHAP `base + contributions`.
- [x] `/model-info` с generation/schema/features/quality/uncertainty/quality gate/model size.
- [x] Безопасный серверный updater Vanga.
- [x] На сайте: AJAX prediction, autocomplete, genre chips, writer input, history, snapshots, share links, what-if A/B, uncertainty/quality UI.
- [x] На сайте: автоматическая сверка сохранённых прогнозов с текущим IMDb rating и публичный блок «Vanga против реальности».
- [x] Публичная методология Vanga.
- [x] Adaptation Analyzer: отдельная `adaptation.duckdb`, sources/cases/annotations/features.
- [x] Три слоя: `observation → structural_consequence → expert_interpretation`.
- [x] Canonical StoryMap и детерминированный StoryDiff для removed/added/merged и потери причинных связей.
- [x] Bootstrap RU/EN film plot из существующего Wikimedia enrichment.

## P0. Production rollout текущей версии

Перед новым крупным feature-инкрементом нужно подтвердить работу актуального кода на сервере.

- [ ] Обновить серверную Vanga до текущего `main` безопасным updater.
- [ ] Пересобрать IMDb DuckDB с `title.crew/title_writers`.
- [ ] Выполнить smoke retrain.
- [ ] Выполнить полный retrain актуальной schema.
- [ ] Проверить quality gate и фактическую publication generation.
- [ ] Проверить `/health`, `/model-info`, `/search/movies`, `/search/people`, `/predict`, `/catalog/ratings`.
- [ ] Обновить `jsint-site` до актуального `dev`.
- [ ] Применить миграции Vanga snapshots/actual ratings.
- [ ] Проверить Celery worker/beat и автоматическую сверку ratings.
- [ ] Выполнить production smoke русского fuzzy поиска, multi-director/full-cast input, writer input, uncertainty, snapshot, share, what-if и methodology.

## P1. Data coverage и честная уверенность

Проблема: при недостатке исторических данных модель может регрессировать к среднему и выглядеть увереннее, чем должна.

- [x] Возвращать `works_count` для режиссёра, сценариста и актёров.
- [x] Добавить `director_known`, `writer_known`, `actor_N_known`.
- [x] Добавить `director_prior_count`, `writer_prior_count`, `actor_N_prior_count`.
- [x] Добавить `known_people_ratio` и `missing_feature_count`.
- [x] Рассчитать итоговый `data_coverage`/`model_familiarity` для каждого прогноза.
- [x] Не трактовать fallback 6.5 как реальное историческое среднее человека.
- [x] Отображать в UI качество покрытия входных данных.
- [x] Ввести режим «низкая обеспеченность данными».
- [x] Для крайне слабого покрытия разрешить abstention: «недостаточно данных для надёжного прогноза» вместо ложной точности.
- [~] Schema v6 и безопасный ablation готовы в коде; требуется серверный полный retrain/ablation перед production publication.
- [R] Сделать uncertainty coverage-aware: расширять эмпирический диапазон при unknown/missing/малой истории.
- [R] Проверить отдельные calibration bins по data coverage.

## P2. Creative Team Model

Цель — моделировать не «известные фамилии», а роли, контекст и совместимость творческой команды. Нумерация ML schema независима от номера roadmap-фазы: P1 дал schema v6, контекст роли/жанра — v7, director↔writer pair — v8, director↔actor pair — v9, recent trend — v10, multi-director context — v11, Full Cast Context — v12, actor↔actor ensemble familiarity — candidate v13.

- [~] `director_genre_avg_rating` и `writer_genre_avg_rating` — реализованы в schema v7, требуется полный ablation.
- [~] `director_genre_prior_count` и `writer_genre_prior_count` — явное состояние отсутствия жанровой истории.
- [~] `director_recent_avg_rating` и `writer_recent_avg_rating` по последним 5 прошлым работам — реализованы в schema v7, требуется полный ablation.
- [~] `director_recent_trend`, `writer_recent_trend` и `*_trend_known` — реализованы в schema v10 как разница среднего последних 3 и предыдущих 3 прошлых работ; нужен temporal v9→v10 ablation.
- [~] `director_is_writer` — реализован в schema v7, требуется полный ablation.
- [ ] История человека отдельно как director, writer и director+writer.
- [~] `director_writer_pair_avg_rating`, `director_writer_pair_count`, `director_writer_pair_known` — реализованы в schema v8; нужен полный temporal v7→v8 ablation перед публикацией.
- [~] `director_actor_N_pair_avg_rating`, `director_actor_N_pair_count`, `director_actor_N_pair_known` для первых трёх актёров — реализованы в schema v9; нужен полный temporal v8→v9 ablation.
- [~] API `directors: [...]` + backward-compatible `director` — реализован в schema v11.
- [~] `director_team_size`, `director_team_known_ratio`, `director_team_avg_rating`, `director_team_prior_count_mean` — schema v11.
- [~] `director_team_prior_collaboration_count`, `director_team_prior_collaboration_avg_rating`, `director_team_collaboration_known` — schema v11; учитывается весь набор режиссёров, а не только первый credit.
- [~] Full Cast schema v12 использует всех `actor/actress` из IMDb `title_principals`, сохраняя legacy actor slots 1..3 для обратной совместимости.
- [~] Общая репутация ансамбля v12: `cast_known_ratio`, `cast_avg_rating`, median/std/min/max и `cast_prior_count_mean/max`.
- [~] Жанровая репутация ансамбля v12: `cast_genre_known_ratio`, `cast_genre_avg_rating`, median/std/min/max и genre prior counts; каждый актёр считается только по прошлым фильмам с пересечением жанров target-фильма.
- [~] `/predict` принимает до 32 актёров; это лимит публичного запроса, training использует весь доступный principal cast локальной IMDb БД.
- [~] Actor Pair candidate v13 считает все unordered actor↔actor пары текущего principal cast и их предыдущие совместные фильмы.
- [~] V13 признаки: `cast_pair_total`, `cast_pair_known_ratio`, `cast_pair_prior_collaboration_mean/median/max`, `cast_pair_prior_rating_avg/median/std`.
- [~] Пары без истории и пары с unresolved actor остаются в знаменателе familiarity как нулевые, но не получают фиктивные совместные рейтинги.
- [R] Исследовать team-wide director↔writer и director↔actor aggregation, не смешивая с primary-director baseline.
- [ ] Число предыдущих совместных работ ключевой команды как отдельный агрегат.
- [R] Ensemble/team cohesion признаки без утечки из будущего — только после отдельной проверки v13.
- [R] Проверить, какие pair/cohesion features реально улучшают temporal MAE.
- [~] Для первого P2-блока добавлен отдельный ablation `baseline v6 → candidate v7`, более поздние P2-блоки исключены.
- [~] Для director↔writer pair добавлен отдельный ablation `baseline v7 → candidate v8`, последующие блоки принудительно исключены.
- [~] Для director↔actor pair добавлен отдельный ablation `baseline v8 → candidate v9`, trend принудительно исключён.
- [~] Для recent trend добавлен отдельный ablation `baseline v9 → candidate v10`.
- [~] Для multi-director context добавлен отдельный ablation `baseline v10 → candidate v11`; Full Cast принудительно выключен.
- [~] Для Full Cast добавлен отдельный непубликуемый ablation `baseline v11 → candidate v12`; v13 принудительно выключен.
- [~] Для Actor Pair History добавлен отдельный непубликуемый ablation `baseline v12 → candidate v13`.
- [ ] Для каждого следующего блока проводить отдельный ablation и не публиковать ухудшающие признаки.

## P2.5. Production Context

Цель — учесть производство фильма как систему: студию, продюсеров, франшизу, shared universe, изменения команды и внешнее творческое влияние. Полный контракт описан в `docs/PRODUCTION_CONTEXT.md`.

- [ ] Нормализовать `production_company` / `production_label` с provenance.
- [ ] Нормализовать producer IDs / creative lead там, где источник воспроизводим.
- [ ] Ввести `franchise_id`, installment index и `shared_universe_id`.
- [R] Проверить исторические studio/producer/franchise aggregates строго по более ранним релизам.
- [R] Исследовать `continuity_load` / cross-project dependency как proxy сложности shared-universe производства.
- [ ] Создать timestamped registry production changes: смена режиссёра, сценариста, creative lead, release date, format.
- [R] Исследовать pre-release `rewrite_count`, `director_change_count`, `writer_change_count`, `release_delay_count`.
- [R] Исследовать публично подтверждённые reshoot/additional-photography и major recut только если событие было известно до даты прогноза.
- [ ] Ввести общий registry внешних story/script/character/worldbuilding/authenticity/sensitivity consultants.
- [R] Проверять влияние consultancy scope статистически; название конкретной компании не считать причинным признаком само по себе.
- [ ] Любое утверждение эксперта о «веянии», корпоративном или культурном влиянии хранить как `expert_interpretation`; в pre-release модель переносить только измеримый proxy.
- [ ] Не использовать политическую позицию, культурную идентичность, расу, этничность и иные чувствительные характеристики людей как признаки качества фильма.

## P3. Первоисточник и адаптация как pre-release признаки

Нельзя использовать простой бинарный признак `adaptation=yes/no`. Нужно описывать сложность материала, формат и компетентность команды.

- [ ] `source_type`: роман/цикл/комикс/игра/пьеса/ремейк/реальная история и т. п.
- [ ] `source_author`/creator ID, где это возможно без чрезмерной кардинальности.
- [R] `source_popularity` на основании воспроизводимого источника.
- [ ] `source_age_at_adaptation`.
- [ ] `source_series_size`.
- [R] `source_worldbuilding_complexity`.
- [R] `source_character_density` и `source_plotline_density`.
- [ ] Формат адаптации: фильм/мини-сериал/сериал, runtime/episodes.
- [R] `adaptation_compression_ratio` и `runtime_per_major_arc`.
- [ ] История сценариста именно на адаптациях.
- [ ] История режиссёра именно на адаптациях.
- [ ] История пары director+writer на адаптациях.
- [R] Разделить историю адаптаций по типам source и жанрам.

## P4. Adaptation Analyzer — автоматический разбор уже вышедших фильмов

Фундамент уже есть. Следующий обязательный слой — автоматическое построение StoryMap из RU/EN пересказов.

- [ ] `RU summary + EN summary → canonical StoryMap` для первоисточника.
- [ ] `RU summary + EN summary → canonical StoryMap` для экранизации.
- [ ] Стабильное сопоставление сущностей между языками и произведениями.
- [ ] Извлечение персонажей, событий, мотиваций, отношений, lore, тем и финала.
- [ ] Извлечение причинных связей `causes/motivates/explains/depends_on/relates_to`.
- [ ] Confidence и provenance для каждого узла/связи.
- [ ] Автоматический StoryDiff поверх полученных карт.
- [ ] Отдельно оценивать plot/character/motivation/worldbuilding/theme/tone/ending preservation.
- [ ] Выявлять потерю контекста: действие осталось, объясняющий его элемент удалён.
- [ ] Выявлять merge персонажей/линий, compression и rewrite.
- [R] Semantic embeddings использовать как вспомогательный сигнал, а не замену structural diff.
- [R] Кешировать embeddings/StoryMap, чтобы не нагружать малый сервер повторно.

## P5. Expert Analysis Corpus

Цель — извлекать методы анализа уже вышедших фильмов у нескольких независимых экспертов и проверять, переносятся ли структурные закономерности на фильмы, которых эти эксперты не разбирали. Мы не предсказываем будущую реакцию конкретного критика и не строим «средний экспертный вкус».

### Профиль: Красный Циник

- [ ] Сформировать список публичных разборов, где есть явное сравнение с первоисточником.
- [ ] Размечать потерю контекста, причинных связей, мотиваций, персонажей, worldbuilding, тем, финала, merge/compression/rewrite.
- [ ] Размечать производственные вмешательства/переписывания/смены creative direction только как утверждения эксперта с evidence и provenance.
- [ ] Проверять, какие выводы подтверждаются автоматическим StoryMap/StoryDiff и production-context facts.

### Профиль: BadComedian

- [ ] Сформировать отдельный список публичных разборов для корпуса.
- [ ] Размечать внутреннюю логику сценария, мотивационную согласованность, setup/payoff, противоречия между сценами, continuity и расхождение заявленного с показанным, когда это применимо.
- [ ] Формализовать доказательную структуру `claim → evidence[] → contradicting_evidence[] → structural_consequence`.

### Общий контракт корпуса

- [~] Зафиксировать независимые `expert_source` профили — Красный Циник и BadComedian являются первыми двумя, но не конечным списком.
- [ ] Для каждого case хранить URL, автора, дату, таймкод/раздел и нашу структурированную аннотацию.
- [ ] Не считать мнение критика объективным фактом: expert interpretation всегда отдельный layer.
- [ ] Размечать, какие observations и structural consequences эксперт использует для аргумента.
- [ ] Хранить evidence и contradicting evidence отдельно от интерпретации.
- [ ] Разделить corpus на train/development и blind validation: часть обзоров системе не показывать.
- [ ] На blind set дать только source + film summaries/StoryMap/StoryDiff и проверить, находит ли Analyzer те же классы проблем самостоятельно.
- [ ] После этого проверять фильмы, которых в экспертном корпусе вообще нет.
- [ ] Добавлять других критиков/аналитиков отдельными профилями, чтобы не обучить систему вкусу одного человека.
- [R] Измерять agreement и disagreement экспертов по dimension/change_type/structural consequence.
- [R] Проверять устойчивость найденной закономерности после исключения одного экспертного профиля.
- [R] Выявлять закономерности, которые подтверждаются структурным diff и реакцией аудитории, а не только мнением критика.
- [ ] Полные тексты/транскрипты сторонних работ хранить только при наличии разрешения или совместимой лицензии.

## P6. От анализа адаптаций обратно к pre-release Vanga

Retrospective Analyzer не должен напрямую кормить Vanga пострелизными признаками. Его задача — обнаруживать закономерности и проектировать честные заранее известные признаки.

Пример допустимого переноса:

`retro: complex worldbuilding + lore compression → часто высокая ошибка/низкий rating`

→ в pre-release Vanga появляются только заранее известные:

`source_worldbuilding_complexity + planned_runtime + writer_adaptation_count`.

- [ ] Для каждой найденной закономерности описывать pre-release proxy.
- [ ] Проверять доступность proxy на момент прогноза.
- [ ] Отдельно тестировать contribution нового proxy на temporal holdout.
- [ ] Запрещать features, появляющиеся только после премьеры.

## P7. Динамика рейтинга во времени

Текущий IMDb dataset даёт текущее состояние рейтинга, но не историческую кривую. Эту базу нужно начать собирать самим.

- [ ] Ежедневно сохранять `imdb_id`, timestamp, `averageRating`, `numVotes` для отслеживаемых фильмов.
- [ ] Не перезаписывать историю при следующем sync.
- [ ] Выделить цели `rating_early`, `rating_30d`, `rating_180d`, `rating_long_term`, когда накопятся данные.
- [R] Исследовать фильмы с «переоценкой со временем» и быстрым падением после стартового маркетинга.
- [R] Проверить отдельные модели/цели initial reception и long-term reception.
- [ ] На публичной сверке показывать дату фактического IMDb rating и число голосов.

## P8. Pre-release audience signals

Использовать только сигналы, реально существовавшие до релиза, и не подменять ими качество фильма.

- [R] Объём интереса к трейлеру/поиску.
- [R] Pre-release sentiment и поляризация обсуждения при наличии воспроизводимого источника и прав на использование.
- [R] Темп роста интереса перед премьерой.
- [ ] Хранить дату сбора сигнала, чтобы исключить post-release leakage.
- [ ] Не использовать расу, этничность и другие чувствительные характеристики людей как признаки качества/рейтинга.

## P9. Future-release discovery

Официальные IMDb datasets могут плохо покрывать далёкие будущие проекты. Нужен отдельный discovery/enrichment слой.

- [ ] Каталог будущих релизов с source provenance и датой последнего обновления.
- [ ] Нормализовать фильм, режиссёров, сценаристов, актёров, source material, franchise, production label и release date.
- [ ] Не делать inference зависимым от сетевого API: найденные данные кешировать локально.
- [ ] При конфликте источников хранить provenance/confidence, а не молча выбирать значение.

## P10. Публичный продукт на jsint-site

- [x] Основной prediction UX, autocomplete, AJAX, explanation, uncertainty.
- [x] Snapshots, share URLs, browser history, what-if compare.
- [x] «Vanga против реальности» и methodology.
- [x] Показ `data_coverage` и причин низкой обеспеченности данными.
- [ ] UI для нескольких режиссёров с autocomplete/chips и сохранением порядка.
- [ ] UI для расширенного актёрского состава: top-3 персональные признаки + Full Cast coverage/genre summary без визуальной перегрузки.
- [ ] Понятный grouped SHAP: сценарий/режиссура/режиссёрская команда/актёры/full cast/actor-pair familiarity/жанр/командная совместимость/production context/нехватка данных.
- [ ] Страница накопленной точности: число сверенных snapshots, MAE по поколениям/периодам/coverage bins.
- [ ] Каталог будущих релизов и последние прогнозы.
- [ ] Share/OG-картинка для конкретного snapshot.
- [ ] Публичный Adaptation Analyzer для уже вышедших экранизаций, когда качество автоматического StoryMap будет доказано.

## Порядок ближайших работ

1. **Production rollout текущего Vanga + jsint-site и retrain с актуальной схемой.**
2. **Завершить серверную валидацию P1 schema v6.**
3. **Creative Team: полный v6→v7→v8→v9→v10→v11→v12→v13 ablation; затем director+writer/team-wide aggregation/cohesion только отдельными инкрементами.**
4. **Production Context: studio/producer/franchise registry и timestamped production-change facts.**
5. **Text → StoryMap extractor для RU/EN summaries.**
6. **Пилот Expert Analysis Corpus: Красный Циник + BadComedian + blind validation.**
7. **Source/adaptation pre-release features, выведенные из ретроспективных закономерностей.**
8. **Накопление временной истории IMDb ratings.**
9. **Future-release discovery и дальнейший публичный UX.**

## Критерии готовности любого ML-инкремента

Новый блок считается готовым к публикации только если:

1. источник и дата данных воспроизводимы;
2. нет post-release leakage;
3. train/inference вычисляют признак одинаково;
4. missing/unknown состояние явно представлено;
5. есть тесты;
6. выполнена ablation/temporal evaluation;
7. качество не нарушает установленный quality gate;
8. укладывается в ресурсы production VPS;
9. документация обновлена;
10. в публичном UI не создаётся ложное ощущение точности или причинности.

## Связанные документы

- `README.md` — текущее состояние Vanga и эксплуатация.
- `docs/ADAPTATION_ANALYZER.md` — архитектура ретроспективного анализа адаптаций.
- `docs/DATA_COVERAGE.md` и `docs/P1_STATUS.md` — контракт и статус P1.
- `docs/P2_STATUS.md` — Creative Team schema v7-v13 и безопасные поэтапные ablation.
- `docs/PRODUCTION_CONTEXT.md` — multi-director, studio/franchise/producer context и внешний creative influence.
- `docs/EXPERT_ANALYSIS_CORPUS.md` — многопрофильный экспертный корпус, blind validation и переносимые методы анализа.
- Этот `docs/ROADMAP.md` — источник истины по согласованным планам дальнейшего развития.
