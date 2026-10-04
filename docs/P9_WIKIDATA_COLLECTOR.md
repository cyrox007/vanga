# P9. Wikidata future-release collector

`WikidataFutureReleaseCollector` — первый конкретный discovery adapter для P9. Он используется только при обновлении локального cache и **никогда не вызывается inference-запросом**.

## Pipeline

```text
WDQS
  -> raw JSON cache
  -> normalization с Wikidata time precision
  -> fingerprinted P9 batch
  -> FutureReleaseBatchImporter
  -> future_releases.duckdb
  -> offline catalog / prediction payload
```

Raw response сохраняется до нормализации в `data/future_releases/wikidata/`. Имя файла включает UTC retrieval timestamp и fingerprint исходного JSON.

## Release date precision

Collector читает не только `P577`, а statement value node и `wikibase:timePrecision`.

- precision `>= 11` -> `exact`;
- precision `10` -> полный календарный `month` window;
- precision `9` -> полный календарный `year` window;
- более грубая точность пока пропускается с warning.

Это не позволяет Wikidata-значению `2028-01-01`, которое на самом деле означает только «2028 год», превратиться в ложную премьеру 1 января.

Каждый P577 statement становится отдельным provenance source. Если Wikidata одновременно содержит конфликтующие актуальные release statements, P9 registry увидит конфликт и не выберет одну дату молча.

Deprecated statements исключаются из WDQS-запроса.

## Сеть и rate limiting

HTTP выполняется только collector'ом. Используются существующие настройки:

- `VANGA_WIKIMEDIA_USER_AGENT`;
- `VANGA_WIKIMEDIA_MIN_INTERVAL_SECONDS`;
- `VANGA_WIKIMEDIA_TIMEOUT_SECONDS`.

Для тестов HTTP transport инъецируется, поэтому CI не зависит от WDQS.

## CLI

Собрать и сохранить batch без импорта:

```bash
python scripts/wikidata_future_releases.py \
  --from-at 2026-10-04T00:00:00Z \
  --to-at 2029-01-01T00:00:00Z \
  --output /tmp/future-wikidata.json
```

Сразу импортировать через atomic P9 contract:

```bash
python scripts/wikidata_future_releases.py \
  --from-at 2026-10-04T00:00:00Z \
  --to-at 2029-01-01T00:00:00Z \
  --import-db future_releases.duckdb
```

Даже при `--import-db` collector сначала создаёт raw cache и fingerprinted batch, а затем использует `FutureReleaseBatchImporter`. Прямой сетевой write в registry отсутствует.

## Текущий scope

Первый adapter намеренно импортирует только discovery identity + release statements:

- canonical title;
- Wikidata QID;
- IMDb ID, если он уже есть в Wikidata;
- title alias;
- release window + precision + statement-level provenance.

Director/writer/cast/source/franchise enrichment остаётся отдельным collector-pass. Такой разрез позволяет сначала проверить надёжность discovery/release-date слоя и не превращать один большой SPARQL-запрос в неуправляемую точку отказа.
