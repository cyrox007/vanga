# IMDb Data Freshness / Backfill guard

## Зачем нужен этот слой

Vanga не должна публиковать новую production-модель, если локальный IMDb snapshot устарел. Особенно критичен провал последних календарных лет: тогда у актёров, режиссёров, сценаристов и production history искусственно обрезается свежая история.

Официальные IMDb TSV обновляются отдельно от модели, поэтому freshness проверяется как самостоятельный контракт перед full training.

## Что проверяется

`src/data_freshness.py` строит воспроизводимый JSON report:

- наличие обязательных DuckDB tables;
- `MIN/MAX(startYear)`;
- coverage последних календарных лет;
- movies / rated movies;
- director coverage;
- writer coverage;
- principal cast coverage;
- число high-vote current-year titles;
- наличие и свежесть локальных `*.tsv.gz`;
- ETag / Last-Modified / Content-Length из `*.meta.json`;
- не старше ли DuckDB скачанных source files;
- logical SHA-256 fingerprint snapshot.

По состоянию на 2026 guard ожидает наличие данных за 2024, 2025 и 2026. Если локальная база заканчивается 2024/2025 годом, full training блокируется.

## Current year не равен stable target

Наличие фильма 2026 года и даже большого `numVotes` ещё не означает, что его текущий rating можно автоматически считать зрелым training target.

Core IMDb tables дают `startYear`, но этого недостаточно, чтобы доказать возраст конкретного релиза в днях. Поэтому текущий год помечается:

`current_year_status = provisional`

и по умолчанию:

`recommended_training_target_max_year = current_year - 1`.

Для включения части current-year releases нужен отдельный release-date/maturity contract с точной датой выхода и минимальным возрастом/числом голосов.

## CLI

Только отчёт:

```bash
python scripts/data_freshness.py report
```

Fail-fast проверка для production workflow:

```bash
python scripts/data_freshness.py check
```

При провале `check` возвращает exit code 2.

Сохранить manifest:

```bash
python scripts/data_freshness.py check \
  --write-manifest data/imdb/freshness-manifest.json
```

Для воспроизводимого теста можно передать `--as-of`.

## Связь с `ds_update.py`

После download/rebuild updater пишет:

`data/imdb/freshness-manifest.json`.

Новые dataset metadata также получают `downloaded_at`.

Сам updater не обязан падать, если snapshot ещё не production-ready: его задача скачать и собрать данные. Но full model publication должна быть запрещена до исправления freshness problems.

## Связь с `traning.py`

- `--smoke` — freshness guard не блокирует запуск;
- `--evaluation-only` — freshness guard не блокирует запуск;
- обычный FULL mode — `require_fresh_imdb_data()` обязателен **до начала тяжёлого CatBoost training**.

Fingerprint и `stable_history_through_year` сохраняются в metadata опубликованной candidate-модели.

## Почему одного backfill недостаточно

Текущий temporal training использует последние два года как holdout. Это правильно для честной оценки, но означает, что evaluation-модель обучается без этих лет.

Поэтому после freshness/backfill нужен следующий отдельный инкремент:

**temporal validation → quality gate → final refit на всех стабильных данных → atomic publication**.

Quality metrics при этом должны оставаться метриками holdout-модели; refit-модель не должна выдавать training fit за честную out-of-time оценку.

## История рейтинга

Текущий IMDb TSV показывает текущее состояние `averageRating/numVotes`, но не восстанавливает point-in-time историю рейтинга задним числом. P7 rating snapshots остаётся обязательным отдельным слоем.
