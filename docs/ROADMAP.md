# Roadmap Vanga

Этот файл — единая точка фиксации согласованного развития Vanga. Если идея обсуждалась, но не попала сюда, она не считается частью roadmap.

Статусы:

- `[x]` — реализовано и слито;
- `[~]` — кодовый фундамент готов, но требуется реальное наполнение, empirical validation или production rollout;
- `[ ]` — ещё не реализовано;
- `[R]` — исследовательская задача: сначала доказать пользу на temporal/ablation данных.

## Неприкосновенные принципы

1. Pre-release Vanga использует только сведения, известные на соответствующий cutoff.
2. Retrospective Analysis физически и логически отделён от production inference.
3. Новый признак не попадает в production только потому, что выглядит логичным: обязательны temporal evaluation и quality gate.
4. `unknown` не равен «среднему качеству».
5. Эксперт — источник аналитического метода, а не абсолютной истины и не цель для имитации вкуса.
6. Нельзя нарушать VPS-бюджет: одна лёгкая CatBoost-модель, disk-first training, без двойной загрузки модели.
7. Сторонние обзоры по умолчанию не копируются целиком: сохраняются ссылка, автор, участок/таймкод и наша структурированная аннотация.
8. Экспертные профили не усредняются в «правильное мнение»; agreement/disagreement являются отдельными данными.
9. Студия, франшиза, консультант, актёрское амплуа и другие контексты не являются автоматическим плюсом или минусом.
10. Несколько режиссёров и полный principal cast считаются нормальным входом.
11. Production Context хранит отдельно `event_at`, `known_at` и provenance.
12. Inference не зависит от внешней сети; collectors кешируют данные заранее.

## Уже реализованное ядро

- [x] Локальный Flask API Vanga на `127.0.0.1:9100`.
- [x] Disk-first CatBoost training с ограничением CPU/RAM/model size.
- [x] Атомарные model generations через `models/releases/*` + `current.json`.
- [x] Temporal train/test split, historical features только из прошлого.
- [x] MAE/RMSE/R², empirical uncertainty, reproducible holdout fingerprints и quality gate.
- [x] SHAP `base + contributions` и `/model-info`.
- [x] Безопасный server updater и recovery tests.
- [x] Русский alias/fuzzy resolver, movie/person search API.
- [x] Writer как отдельная роль и нормализованный `title_writers`.
- [x] Data coverage/model familiarity/abstention.
- [x] Adaptation Analyzer с отдельной `adaptation.duckdb`.
- [x] `observation → structural_consequence → expert_interpretation`.
- [x] Canonical StoryMap data contract и детерминированный StoryDiff.
- [x] Public jsint-site: AJAX prediction, autocomplete, uncertainty, snapshots, share links, browser history, what-if, methodology, verified predictions.

## P0. Production rollout текущего main

Это главный оставшийся блок до объявления текущего состояния production-ready.

- [~] Единый offline/live release gate реализован: `python scripts/release_readiness.py`; требуется запуск на сервере.
- [ ] Обновить серверную Vanga до текущего `main` безопасным updater.
- [ ] Пересобрать IMDb DuckDB с `title_crew` и `title_writers`.
- [ ] Выполнить smoke retrain.
- [ ] Выполнить полный retrain актуальной выбранной schema.
- [ ] Проверить quality gate и опубликованную generation.
- [ ] Выполнить `release_readiness.py --strict-p9`.
- [ ] Проверить live `/health`, `/model-info`, `/search/movies`, `/search/people`, `/predict`, `/catalog/ratings`, `/future/catalog`.
- [ ] Обновить `jsint-site` до актуального `dev`.
- [ ] Проверить worker/beat, сверку ratings и публичный Vanga UI.
- [ ] Финальный production smoke: RU fuzzy, writer, multi-director/full-cast contract, uncertainty, snapshot/share/what-if, future catalog.

## P1. Data coverage и честная уверенность

- [x] `works_count`, known flags, prior counts, `known_people_ratio`, `missing_feature_count`.
- [x] `data_coverage` / `model_familiarity`, low-coverage UI и abstention.
- [~] Schema v6 и ablation tooling готовы; production publication требует серверного retrain/validation.
- [R] Coverage-aware uncertainty.
- [R] Calibration bins по уровням покрытия.

## P2. Creative Team Model

Кодовая лестница v7→v15 существует и тестируется по отдельным блокам; публикация каждого шага зависит от temporal ablation.

- [~] v7: director/writer genre history, recent history, `director_is_writer`.
- [~] v8: director↔writer pair history.
- [~] v9: director↔actor pair history.
- [~] v10: recent trend.
- [~] v11: multi-director team, team history и prior collaboration.
- [~] v12: Full Cast Context по всему principal cast.
- [~] v13: actor↔actor prior collaboration/familiarity.
- [~] v14: director+writer dual-role history.
- [~] v15: team-wide director↔writer и director↔actor histories.
- [~] Для каждого перехода v6→…→v15 есть отдельный непубликуемый ablation-контур.
- [ ] Выполнить полный последовательный ablation на актуальном production dataset.
- [R] После v15 исследовать key-team aggregate/cohesion; не вводить непрозрачный cohesion score раньше raw-history проверки.

## P2.5. Production Context

- [~] Отдельный factual registry `production_context.duckdb` готов.
- [~] Projects/entities/sources/events/consultancies и temporal snapshots готовы.
- [~] Production company/label, producer/creative lead, franchise/installment/shared universe schema готовы.
- [~] Production changes (`event_at`, `known_at`, stage, provenance) готовы.
- [~] Consultancy registry и CLI `init/import/snapshot/timeline` готовы.
- [~] Materializer/outcome history/continuity infrastructure готовы.
- [ ] Наполнить реальными воспроизводимыми данными.
- [R] Проверить studio/producer/franchise aggregates только по прошлым релизам.
- [R] Проверить rewrite/director change/writer change/release delay/reshoot proxies.
- [R] Проверить continuity/shared-universe load.
- [ ] Чувствительные характеристики людей никогда не использовать как признак качества фильма.

## P2.7. Actor Persona & Character Graph

Архитектура зафиксирована в `docs/ACTOR_PERSONA_CHARACTER_GRAPH.md`. Это отдельное исследовательское направление, а не набор субъективных ручных бонусов.

- [x] Зафиксирован temporal-safe контракт actor → role/character → film/franchise → persona evidence.
- [R] Character identity: повторное появление одного персонажа между фильмами, кроссоверами и нетипичными cameo.
- [R] `self_portrayal` и `fictionalized_self` как отдельные типы роли.
- [R] Iconic-role reference / easter egg / parody без ложного утверждения, что это тот же канонический персонаж.
- [R] Actor persona, role distance, role versatility и deliberate persona inversion.
- [R] Meta-cast density/persona synergy, включая ансамбли, построенные на экранной репутации участников.
- [R] Связать экспертные observations с переносимыми принципами вроде `persona_dependency` и `role_inversion_payoff`.
- [ ] Не включать actor-persona признаки в production schema до temporal ablation, provenance и blind validation.

## P3. Первоисточник и адаптация как pre-release layer

Кодовый фундамент уже существенно готов; реальное наполнение и ML promotion остаются отдельной задачей.

- [~] `source_context.duckdb` и temporal provenance contract готовы.
- [~] Source type/title/author/format/series size и planned runtime context поддерживаются.
- [~] Source complexity, format pressure и source-team-history materializers реализованы и покрыты тестами.
- [R] Source popularity из воспроизводимого датированного источника.
- [R] Worldbuilding complexity, character density, plotline density.
- [R] Adaptation compression/runtime-per-major-arc.
- [~] История writer/director/team на адаптациях имеет инфраструктуру; требуется dataset/ablation.
- [ ] Ничего из post-release StoryDiff не подавать напрямую в prediction.

## P4. Adaptation Analyzer / StoryMap

Старый roadmap считал этот слой почти не начатым; фактически baseline pipeline уже существует.

- [~] RU/EN text → deterministic sentence-graph StoryMap extractor реализован.
- [~] Entity/story alignment реализован.
- [~] Story semantics для событий, отношений и causal/semantic edges реализована.
- [~] Story transformations и structural diff реализованы.
- [~] Benchmark suite, benchmark runner и quality gate реализованы.
- [~] Confidence/provenance contracts покрыты кодом и тестами.
- [~] Отдельные CLI для extract/align/semantic/transform/benchmark/quality-gate готовы.
- [ ] Собрать достаточно большой вручную проверенный RU/EN benchmark corpus.
- [R] Доказать качество автоматического StoryMap на реальных source/film парах перед публичным Adaptation Analyzer.
- [R] Embeddings допустимы только как вспомогательный сигнал и при контроле VPS-ресурсов.

## P5. Expert Analysis Corpus

Цель — усвоить переносимые способы анализа Красного Циника, BadComedian и других экспертов, а не предсказывать их будущую реакцию.

### Инфраструктура

- [x] Отдельный structured expert corpus store реализован.
- [x] Evidence/contradicting evidence, expert source, provenance и разделение observation/consequence/interpretation поддерживаются.
- [x] Blind-validation infrastructure реализована.
- [x] Expert consensus/agreement-transfer infrastructure реализована.
- [x] CLI и тесты для corpus/blind validation/consensus/agreement-transfer готовы.

### Реальный корпус

- [ ] Сформировать воспроизводимый список работ Красного Циника.
- [ ] Сформировать отдельный список работ BadComedian.
- [ ] Разметить adaptation loss, causality, motivation, setup/payoff, continuity, character/worldbuilding/theme/ending и production claims с evidence.
- [ ] Разделить реальные cases на development и закрытый blind set.
- [ ] Проверить перенос на фильмы, которых конкретный эксперт не разбирал.
- [R] Проверить устойчивость принципов при исключении одного expert profile.
- [R] Выделять только закономерности, подтверждаемые structural evidence и/или независимыми данными.
- [ ] Полные сторонние тексты хранить только при разрешении/совместимой лицензии.

## P6. Перенос retrospective findings в pre-release proxies

- [x] Registry proxy hypotheses реализован отдельно от production features.
- [x] Audited/unified materializers реализованы.
- [x] Temporal ablation pipeline и gate реализованы.
- [x] Candidate schema plan/gate и promotion manifest реализованы.
- [~] Реальные expert/StoryMap гипотезы требуется наполнить и прогнать через pipeline.
- [ ] В production попадают только proxies, реально доступные на prediction cutoff и прошедшие gate.

## P7. Динамика IMDb rating

- [x] Append-only `rating_history.duckdb` и ingest API реализованы.
- [x] Milestone extraction и rating dataset tooling реализованы.
- [x] История физически отделена от текущего IMDb snapshot и не перезаписывается API-контрактом.
- [~] Требуется production schedule ежедневного накопления наблюдений.
- [~] `rating_early`, `rating_30d`, `rating_180d`, `rating_long_term` готовы как направление, но требуют накопленного времени/данных.
- [R] Initial reception vs long-term reception.
- [~] Публичная сверка уже показывает текущий факт; нужно явно вывести timestamp фактического observation в UI.

## P8. Pre-release audience signals

- [x] Отдельный timestamped aggregate registry реализован с method/version/provenance.
- [x] Raw user data/полные тексты обсуждений в registry не сохраняются.
- [x] P8→P6 materializer реализован.
- [R] Trailer/search interest volume.
- [R] Pre-release sentiment/polarization только при воспроизводимом и допустимом источнике.
- [R] Темп роста интереса перед премьерой.
- [ ] Не использовать чувствительные характеристики людей как quality features.

## P9. Future-release discovery

P9 как архитектурный слой реализован. Остался production data refresh и расширение покрытия.

- [x] Локальный каталог будущих релизов с immutable source provenance и `known_at`.
- [x] Projects/people/entities/aliases/status/release windows/source material/franchise/production labels schema.
- [x] Batch importer и atomic refresh.
- [x] Wikidata discovery/enrichment, temporal runtime/genres collector и raw cache.
- [x] Statement-level Wikidata `P577` release dates с precision/territory semantics.
- [x] TMDb как независимый опциональный источник theatrical release dates.
- [x] Canonical temporal territory mapping Wikidata QID → ISO 3166.
- [x] Explicit regional policy без implicit `worldwide` fallback.
- [x] Cross-source conflicts сохраняются, а не скрываются выбором одного значения.
- [x] Inference/cache layer не зависит от сети.
- [x] `GET /future/catalog` и `POST /future/prediction-payload`.
- [~] Требуется production population/refresh и проверка фактического покрытия рынков.
- [~] Dated synopsis/source-material coverage нужно расширять независимыми источниками.

## P10. Публичный продукт на jsint-site

- [x] Prediction UX, autocomplete, AJAX, explanation, uncertainty.
- [x] Snapshots/share URLs/history/what-if compare.
- [x] «Vanga против реальности» и methodology.
- [x] Data coverage UI.
- [~] Future catalog + explicit market selector + regional conflict/readiness UI слиты в `jsint-site/dev`; требуется production deploy.
- [ ] Multi-director autocomplete/chips UI.
- [ ] Расширенный cast UI без визуальной перегрузки.
- [ ] Grouped SHAP по логическим блокам.
- [ ] Страница накопленной точности по generation/period/coverage bins.
- [ ] Share/OG image конкретного snapshot.
- [ ] Публичный Adaptation Analyzer только после StoryMap quality gate на реальном benchmark.

## Порядок оставшихся работ

1. **Production rollout:** Vanga `main` → IMDb rebuild → smoke/full retrain → quality gate → P9 refresh → readiness gate → live smoke → `jsint-site/dev`.
2. **Creative Team empirical pass:** последовательный v6→v15 temporal ablation и публикация только реально полезного набора.
3. **StoryMap quality pass:** реальный RU/EN benchmark corpus и quality gate.
4. **Expert Corpus pilot:** Красный Циник + BadComedian → blind validation → transfer на непокрытые фильмы.
5. **P6 promotion:** пропустить подтверждённые expert/StoryMap findings через pre-release proxy pipeline.
6. **Production Context:** реальное наполнение и ablation candidate proxies.
7. **Actor Persona & Character Graph:** собрать temporal dataset и проверить persona/role hypotheses.
8. **Долгосрочное накопление:** rating history + audience signals.
9. **Product polish:** grouped SHAP, multi-director/full-cast UI, accuracy page, OG, Adaptation Analyzer.

## Критерии готовности ML-инкремента

Новый ML-блок считается готовым к публикации только если:

1. источник и дата данных воспроизводимы;
2. отсутствует post-release leakage;
3. train/inference вычисляют признак одинаково;
4. missing/unknown представлены явно;
5. есть тесты;
6. выполнен temporal/ablation evaluation;
7. пройден quality gate;
8. соблюдён VPS resource budget;
9. документация обновлена;
10. UI не создаёт ложную точность или ложную причинность.

## Связанные документы

- `README.md` — текущее состояние и эксплуатация.
- `docs/RELEASE_READINESS.md` — единый pre-deploy gate.
- `docs/P1_STATUS.md`, `docs/P2_STATUS.md` — ML schema/ablation status.
- `docs/PRODUCTION_CONTEXT.md` — production factual layer.
- `docs/ACTOR_PERSONA_CHARACTER_GRAPH.md` — actor/character/persona research contract.
- `docs/SOURCE_CONTEXT.md`, `docs/SOURCE_COMPLEXITY.md` — source/adaptation pre-release layer.
- `docs/STORYMAP_EXTRACTION.md`, `docs/STORYMAP_BENCHMARK_SUITE.md`, `docs/STORYMAP_QUALITY_GATE.md` — StoryMap pipeline.
- `docs/EXPERT_ANALYSIS_CORPUS.md`, `docs/EXPERT_BLIND_VALIDATION.md`, `docs/EXPERT_AGREEMENT_TRANSFER.md` — expert corpus.
- `docs/P6_CANONICAL_PIPELINE.md` — retrospective → pre-release proxy promotion.
- `docs/P7_RATING_HISTORY.md`, `docs/P8_AUDIENCE_SIGNALS.md` — long-term observational layers.
- `docs/P9_FUTURE_RELEASES.md`, `docs/P9_REGIONAL_RELEASE_POLICY.md`, `docs/P9_HTTP_API.md` — future releases.
- Этот `docs/ROADMAP.md` — источник истины по дальнейшему развитию.
