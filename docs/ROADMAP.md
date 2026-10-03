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
- [ ] Выполнить полный retrain schema с writer features.
- [ ] Проверить quality gate и фактическую publication generation.
- [ ] Проверить `/health`, `/model-info`, `/search/movies`, `/search/people`, `/predict`, `/catalog/ratings`.
- [ ] Обновить `jsint-site` до актуального `dev`.
- [ ] Применить миграции Vanga snapshots/actual ratings.
- [ ] Проверить Celery worker/beat и автоматическую сверку ratings.
- [ ] Выполнить production smoke русского fuzzy поиска, writer input, uncertainty, snapshot, share, what-if и methodology.

## P1. Data coverage и честная уверенность

Проблема: при недостатке исторических данных модель может регрессировать к среднему и выглядеть увереннее, чем должна.

- [ ] Возвращать `works_count` для режиссёра, сценариста и актёров.
- [ ] Добавить `director_known`, `writer_known`, `actor_N_known`.
- [ ] Добавить `director_prior_count`, `writer_prior_count`, `actor_N_prior_count`.
- [ ] Добавить `known_people_ratio` и `missing_feature_count`.
- [ ] Рассчитать итоговый `data_coverage`/`model_familiarity` для каждого прогноза.
- [ ] Не трактовать fallback 6.5 как реальное историческое среднее человека.
- [ ] Отображать в UI качество покрытия входных данных.
- [ ] Ввести режим «низкая обеспеченность данными».
- [ ] Для крайне слабого покрытия разрешить abstention: «недостаточно данных для надёжного прогноза» вместо ложной точности.
- [R] Сделать uncertainty coverage-aware: расширять эмпирический диапазон при unknown/missing/малой истории.
- [R] Проверить отдельные calibration bins по data coverage.

## P2. Creative Team Model v6

Цель — моделировать не «известные фамилии», а роли, контекст и совместимость творческой команды.

- [ ] `director_genre_avg_rating` и `writer_genre_avg_rating`.
- [ ] `director_recent_avg_rating` и `writer_recent_avg_rating` (последние N прошлых работ).
- [R] `director_trend`/`writer_trend`: улучшается или ухудшается recent form.
- [ ] `director_is_writer`.
- [ ] История человека отдельно как director, writer и director+writer.
- [ ] `director_writer_pair_count` и historical pair rating.
- [ ] `director_actor_pair_count` и historical pair rating.
- [ ] Число предыдущих совместных работ ключевой команды.
- [R] Ensemble/team cohesion признаки без утечки из будущего.
- [R] Проверить, какие pair features реально улучшают temporal MAE.
- [ ] Для каждого нового блока проводить ablation и не публиковать ухудшающие признаки.

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

## P5. Экспертный корпус и «метод Красного Циника»

Цель — извлечь метод анализа уже вышедших адаптаций и проверить, переносится ли он на фильмы, которых эксперт не разбирал.

- [ ] Сформировать список публичных разборов Красного Циника, где есть явное сравнение с первоисточником.
- [ ] Для каждого case хранить URL, автора, дату, таймкод/раздел и нашу структурированную аннотацию.
- [ ] Не считать мнение критика объективным фактом: expert interpretation всегда отдельный layer.
- [ ] Размечать, какие observations и structural consequences эксперт использует для аргумента.
- [ ] Разделить corpus на train/development и blind validation: часть обзоров системе не показывать.
- [ ] На blind set дать только source + film summaries и проверить, находит ли Analyzer те же классы проблем самостоятельно.
- [ ] После этого проверять фильмы, которых в экспертном корпусе вообще нет.
- [ ] Добавлять других критиков/аналитиков, чтобы не обучить систему вкусу одного человека.
- [R] Измерять agreement экспертов по dimension/change_type/structural consequence.
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
- [ ] Нормализовать фильм, режиссёра, сценариста, актёров, source material, franchise и release date.
- [ ] Не делать inference зависимым от сетевого API: найденные данные кешировать локально.
- [ ] При конфликте источников хранить provenance/confidence, а не молча выбирать значение.

## P10. Публичный продукт на jsint-site

- [x] Основной prediction UX, autocomplete, AJAX, explanation, uncertainty.
- [x] Snapshots, share URLs, browser history, what-if compare.
- [x] «Vanga против реальности» и methodology.
- [ ] Показ `data_coverage` и причин низкой обеспеченности данными.
- [ ] Понятный grouped SHAP: сценарий/режиссура/актёры/жанр/командная совместимость/нехватка данных.
- [ ] Страница накопленной точности: число сверенных snapshots, MAE по поколениям/периодам/coverage bins.
- [ ] Каталог будущих релизов и последние прогнозы.
- [ ] Share/OG-картинка для конкретного snapshot.
- [ ] Публичный Adaptation Analyzer для уже вышедших экранизаций, когда качество автоматического StoryMap будет доказано.

## Порядок ближайших работ

1. **Production rollout текущего Vanga + jsint-site и retrain с актуальной схемой.**
2. **Data coverage / known flags / prior counts / low-confidence UX.**
3. **Creative Team v6 и ablation.**
4. **Text → StoryMap extractor для RU/EN summaries.**
5. **Пилот экспертного корпуса адаптаций и blind validation.**
6. **Source/adaptation pre-release features, выведенные из ретроспективных закономерностей.**
7. **Накопление временной истории IMDb ratings.**
8. **Future-release discovery и дальнейший публичный UX.**

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
- Этот `docs/ROADMAP.md` — источник истины по согласованным планам дальнейшего развития.
