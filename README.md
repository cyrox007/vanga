# Vanga

Vanga — экспериментальная система предрелизного анализа фильмов. Она начиналась как CatBoost-модель прогноза IMDb rating, но сейчас включает temporal-safe данные о творческой команде, будущем релизе, первоисточнике, StoryMap/Adaptation Analyzer, экспертный исследовательский слой и публичное демо на JSInteractive.

Главный принцип: **предрелизный прогноз не имеет права использовать данные, которые появились после премьеры**.

## Что умеет текущая система

### Production prediction

- CatBoost inference через локальный Flask API;
- режиссёр, сценарист, актёры, жанры, год, runtime и исторические role-specific признаки;
- temporal train/test split;
- empirical uncertainty;
- SHAP `base + contributions`;
- data coverage/model familiarity и abstention при слабом покрытии;
- атомарные model generations и quality gate;
- безопасный retrain без обязательной остановки рабочего поколения.

### Creative Team research

В коде реализована последовательная candidate-линейка schema v6→v15:

- genre/recent history режиссёра и сценариста;
- director↔writer и director↔actor history;
- recent trend;
- multi-director team;
- Full Cast Context;
- actor↔actor familiarity;
- director+writer dual-role history;
- team-wide collaboration.

Эти признаки **не считаются автоматически production-approved**: каждый следующий блок должен пройти отдельный temporal ablation и quality gate. Актуальный статус — в `docs/ROADMAP.md` и `docs/P2_STATUS.md`.

### Future releases / P9

Отдельный локальный registry будущих фильмов умеет:

- хранить immutable source provenance и `known_at`;
- импортировать данные атомарными batches;
- собирать discovery/enrichment из Wikidata;
- использовать TMDb как независимый опциональный источник theatrical release dates;
- хранить statement-level `P577` с precision/territory semantics;
- хранить temporal runtime/genres/synopsis;
- канонизировать Wikidata territory QID → ISO 3166;
- выявлять cross-source conflicts;
- работать без сети во время inference;
- строить региональный каталог без implicit `worldwide` fallback.

HTTP API:

```text
GET  /future/catalog
POST /future/prediction-payload
```

Для регионального сценария `territory` обязателен (`DE`, `US`, `NL` и т. п.). Если данные конфликтуют или неполны, API возвращает blocked-state вместо угадывания даты.

### StoryMap / Adaptation Analyzer

Ретроспективный слой физически отделён от production prediction. Реализованы:

- canonical StoryMap contract;
- RU/EN text extractor baseline;
- entity/story alignment;
- semantic/causal edges;
- structural transformations и StoryDiff;
- benchmark suite и quality gate;
- отдельная `adaptation.duckdb`.

Публичный Adaptation Analyzer не считается готовым, пока автоматический StoryMap не пройдёт реальный вручную проверенный benchmark.

### Expert Analysis Corpus

Есть инфраструктура structured expert corpus, blind validation, consensus и agreement-transfer. Первые согласованные профили — Красный Циник и BadComedian.

Цель — **извлекать переносимые принципы анализа**, а не предсказывать будущую реакцию конкретного критика. Реальный экспертный корпус ещё требует наполнения и blind validation.

### Actor Persona & Character Graph

Зафиксировано отдельное research-направление для:

- повторяющихся персонажей и необычных cameo;
- `self_portrayal` / `fictionalized_self`;
- iconic-role references и easter eggs;
- actor persona и role versatility;
- deliberate persona inversion;
- meta-cast/persona synergy.

Подробнее: `docs/ACTOR_PERSONA_CHARACTER_GRAPH.md`.

## Локальный API

По умолчанию production service слушает только loopback:

```bash
127.0.0.1:9100
```

Быстрая ручная проверка:

```bash
curl http://127.0.0.1:9100/health
curl http://127.0.0.1:9100/model-info
curl 'http://127.0.0.1:9100/search/movies?q=Interstellar&year=2014'
curl 'http://127.0.0.1:9100/search/people?q=Nolan&role=director'
```

Prediction выполняется через `POST /predict`. Публичный сайт должен обращаться к Vanga через backend/reverse proxy, а не открывать inference-port наружу.

## IMDb data и retrain

IMDb datasets обновляются отдельно, после чего локальная DuckDB пересобирается с производными таблицами вроде `title_writers`.

```bash
.venv/bin/python ds_update.py
```

Smoke retrain:

```bash
.venv/bin/python traning.py --smoke
```

Полный retrain:

```bash
.venv/bin/python traning.py
```

Обучение disk-first и рассчитано на малый VPS. Candidate не переключает `models/current.json`, пока не пройдёт публикационный контракт. Успешные модели хранятся в `models/releases/<generation>`.

## Quality gate

При сопоставимом temporal holdout candidate сравнивается с активной generation. Проверяется не только период, но и fingerprint test dataset / IMDb snapshot.

Порог допустимой MAE-регрессии задаётся:

```bash
export VANGA_TRAIN_MAX_MAE_REGRESSION=0.03
```

При недопустимой регрессии рабочая generation не переключается.

## Production update

Серверный updater работает от `main` и не запускает тяжёлый полный retrain без явного флага.

Обычное безопасное обновление:

```bash
sudo bash deploy/update-vanga.sh --yes
```

Обновление с полным retrain:

```bash
sudo bash deploy/update-vanga.sh --yes --full-retrain
```

Также доступны `--skip-db` и `--skip-pip`. Updater использует orchestration lock, staging, health-check и rollback-путь.

## Проверка готовности релиза

Перед production rollout:

```bash
.venv/bin/python scripts/release_readiness.py
```

Для релиза future catalog P9 должен быть реально наполнен:

```bash
.venv/bin/python scripts/release_readiness.py --strict-p9
```

После рестарта API:

```bash
.venv/bin/python scripts/release_readiness.py \
  --live-api \
  --strict-p9 \
  --markets DE,US,NL
```

Checker не загружает вторую CatBoost-модель и не запускает внешние collectors. Подробности: `docs/RELEASE_READINESS.md`.

## systemd

Репозиторий содержит units:

- `deploy/systemd/vanga.service` — inference API;
- `deploy/systemd/vanga-retrain.service` — retrain job;
- `deploy/systemd/vanga-retrain.timer` — periodic retrain.

Пример:

```bash
sudo cp deploy/systemd/vanga.service /etc/systemd/system/
sudo cp deploy/systemd/vanga-retrain.service /etc/systemd/system/
sudo cp deploy/systemd/vanga-retrain.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now vanga.service
sudo systemctl enable --now vanga-retrain.timer
```

## Внешние collectors и inference

Wikidata/Wikipedia/TMDb используются только в collector/enrichment слоях. Сетевой сбой не должен ломать `/predict`.

Для Wikimedia рекомендуется явный User-Agent:

```bash
export VANGA_WIKIMEDIA_USER_AGENT='Vanga/1.0 (https://jsinteractive.ru; contact@example.org)'
```

TMDb release-date collector использует отдельный read access token и является опциональным. Секреты не должны попадать в репозиторий.

## Основные локальные базы

- `imdb.duckdb` — production IMDb snapshot;
- `enrichment.duckdb` — Wikimedia enrichment;
- `adaptation.duckdb` — retrospective Adaptation Analyzer;
- `expert_corpus.duckdb` — structured expert corpus;
- `source_context.duckdb` — pre-release source material context;
- `production_context.duckdb` — factual production context;
- `future_releases.duckdb` — P9 future registry;
- `rating_history.duckdb` — append-only rating history;
- `audience_signals.duckdb` — timestamped aggregated pre-release audience signals.

Конкретные пути конфигурируются переменными окружения в `settings.py`.

## Публичный продукт

`jsint-site` уже поддерживает:

- основной prediction UX;
- autocomplete;
- SHAP/uncertainty/data coverage;
- snapshots/share/history/what-if;
- «Vanga против реальности»;
- methodology;
- future catalog с выбором регионального рынка и отображением conflict/readiness state в ветке `dev`.

Production deploy сайта выполняется отдельно после готовности Vanga API.

## Документация

Главные точки входа:

- `docs/ROADMAP.md` — источник истины по статусу и дальнейшим задачам;
- `docs/RELEASE_READINESS.md` — pre-deploy gate;
- `docs/P1_STATUS.md`, `docs/P2_STATUS.md` — data coverage и Creative Team;
- `docs/PRODUCTION_CONTEXT.md` — production factual layer;
- `docs/ACTOR_PERSONA_CHARACTER_GRAPH.md` — actor/character/persona research;
- `docs/ADAPTATION_ANALYZER.md` — retrospective adaptation architecture;
- `docs/STORYMAP_EXTRACTION.md`, `docs/STORYMAP_QUALITY_GATE.md` — StoryMap;
- `docs/EXPERT_ANALYSIS_CORPUS.md` — expert methodology corpus;
- `docs/P6_CANONICAL_PIPELINE.md` — promotion retrospective findings в pre-release proxies;
- `docs/P7_RATING_HISTORY.md`, `docs/P8_AUDIENCE_SIGNALS.md` — observational layers;
- `docs/P9_FUTURE_RELEASES.md`, `docs/P9_REGIONAL_RELEASE_POLICY.md`, `docs/P9_HTTP_API.md` — future releases.

## Статус

Архитектурный фундамент текущей Vanga собран. Основной незавершённый блок перед production-ready состоянием — **реальный server rollout/retrain/ablation/data population**, а не строительство базовой архитектуры. Точный остаток и порядок работ всегда фиксируются в `docs/ROADMAP.md`.
