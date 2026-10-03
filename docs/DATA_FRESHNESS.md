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

Сам updater не обязан падать, если snapshot ещё не production-ready: его задача скачать и собрать данные. Но full model publication запрещена до исправления freshness problems.

## Связь с `traning.py`

- `--smoke` — freshness guard не блокирует запуск;
- `--evaluation-only` — freshness guard не блокирует запуск;
- обычный FULL mode — `require_fresh_imdb_data()` обязателен **до начала тяжёлого CatBoost training**.

Fingerprint и `stable_history_through_year` сохраняются в metadata опубликованной модели.

## Production lifecycle: validation → quality gate → refit

После freshness/backfill Vanga не публикует непосредственно temporal-validation модель.

FULL mode работает так:

1. freshness guard определяет `recommended_training_target_max_year`;
2. target rows более новых лет исключаются из validation dataset;
3. последние два календарных года **внутри stable history** остаются out-of-time holdout;
4. validation model считает MAE/RMSE/R² и uncertainty quantiles;
5. candidate проходит quality gate против active metadata, когда holdout сопоставим;
6. validation CatBoost освобождается из памяти;
7. запускается отдельный disk-first **FINAL REFIT** на всех stable target rows до `recommended_training_target_max_year` включительно;
8. публикуется refit-модель;
9. metadata публикации продолжает хранить только честные metrics отдельной temporal-validation модели.

Например, в 2026 году при stable cutoff 2025 validation использует 2024–2025 как holdout и более ранние годы как train. После прохождения quality gate production artifact переобучается уже на всей стабильной истории до 2025 включительно.

Это принципиально: production model получает свежую историю 2024–2025, но качество не оценивается на тех же строках, на которых final artifact был refit.

## Защита малой VPS

Validation и final refit не удерживаются в памяти одновременно. После quality gate validation model удаляется и вызывается GC, затем строится второй disk-backed dataset.

Refit использует те же ограничения CatBoost, что и validation:

- один thread;
- `used_ram_limit=900mb`;
- ограничение CTR;
- disk-first TSV;
- тот же feature schema.

Если feature schema между validation и refit различается, публикация прекращается.

## Metadata опубликованной модели

Новый lifecycle фиксирует как минимум:

- `training_mode = temporal_validation_then_stable_refit`;
- `published_metrics_source = separate_temporal_validation_model`;
- validation train/test years и metrics;
- `validation_target_max_year`;
- `refit_year_from/refit_year_to`;
- `refit_rows`;
- `refit_target_max_year`;
- freshness fingerprint;
- pre-refit quality gate.

## История рейтинга

Текущий IMDb TSV показывает текущее состояние `averageRating/numVotes`, но не восстанавливает point-in-time историю рейтинга задним числом. P7 rating snapshots остаётся обязательным отдельным слоем.
