# P9. Единый refresh pipeline будущих релизов

`FutureReleaseRefreshPipeline` объединяет уже существующие P9 collector-слои в один операционный проход:

```text
Wikidata discovery
  -> fingerprinted batch
  -> atomic import
  -> project IDs текущего discovery batch
  -> Wikidata enrichment чанками
  -> fingerprinted batches
  -> atomic imports
  -> machine-readable refresh report
```

Inference по-прежнему не обращается к сети. Refresh — отдельная maintenance-задача, которая обновляет локальный `future_releases.duckdb`.

## Почему нет одной глобальной транзакции

Сетевые запросы нельзя держать внутри одной длинной DuckDB-транзакции. Поэтому атомарность задаётся на уровне каждого collector batch:

- discovery batch либо импортируется целиком, либо откатывается;
- каждый enrichment batch либо импортируется целиком, либо откатывается;
- если enrichment временно упал, уже успешно сохранённый discovery не удаляется.

При частичном сбое pipeline выбрасывает `FutureReleaseRefreshError`, внутри которого есть `report` с `failed_stage`, уже импортированными batches и project IDs проблемного chunk.

Это позволяет безопасно повторить refresh: importer идемпотентен по provider/source fingerprint.

## Scope enrichment

После discovery pipeline обогащает **только project IDs текущего discovery batch**. Старые проекты, уже находящиеся в registry, не включаются автоматически в текущий сетевой проход.

Это важно по двум причинам:

1. refresh остаётся воспроизводимым относительно конкретного discovery snapshot;
2. размер enrichment-нагрузки не растёт бесконечно вместе с локальным registry.

Для отдельного повторного enrichment старых проектов используется `scripts/wikidata_future_enrichment.py --project-id ...`.

## Chunking

Один Wikidata enrichment request ограничен 200 проектами. `enrichment_chunk_size` автоматически ограничивается диапазоном `1..200`.

Например, 450 обнаруженных проектов при chunk size 200 дадут три enrichment batch: `200 + 200 + 50`.

## Отчёт

Успешный report содержит:

- `from_at`, `to_at`, `retrieved_at`;
- discovery collector/source fingerprints;
- atomic discovery import result;
- project IDs текущего discovery batch;
- summaries enrichment batches;
- atomic enrichment import results;
- warning count;
- `refresh_complete=true`;
- `network_required_for_inference=false`;
- `refresh_fingerprint_sha256`.

`refresh_fingerprint_sha256` считается по стабильному контракту запуска и source fingerprints, а не по техническому `imported_at`, поэтому идемпотентный повтор одного и того же snapshot имеет тот же refresh fingerprint.

При ошибке report сохраняет:

- `refresh_complete=false`;
- `failed_stage`;
- для enrichment — `failed_enrichment_batch` и `failed_project_ids`;
- результаты уже завершённых импортов.

## CLI

Полный refresh будущих релизов:

```bash
python scripts/future_release_refresh.py \
  --db future_releases.duckdb \
  --from-at 2026-10-04T00:00:00Z \
  --to-at 2029-01-01T00:00:00Z \
  --discovery-limit 1000 \
  --enrichment-chunk-size 100 \
  --output /var/log/vanga/p9-refresh.json
```

Код завершения:

- `0` — refresh полностью завершён;
- `2` — частичный/неуспешный refresh, отчёт всё равно доступен.

Для cron/systemd рекомендуется всегда сохранять `--output` и отдельно проверять код завершения процесса.

## Повторный запуск

При явном одинаковом `retrieved_at` и неизменившемся source snapshot collectors формируют те же fingerprints, а `FutureReleaseBatchImporter` возвращает `idempotent=true`. Данные не дублируются.

Обычный production refresh использует новый текущий UTC timestamp: это новое наблюдение, поэтому новые statement evidence могут быть сохранены с новым `known_at`.

## Что этот pipeline не делает

Он не:

- запускает CatBoost retrain;
- переключает production model;
- вызывает `/predict`;
- обновляет `jsint-site`;
- делает post-release данные pre-release признаками;
- пытается угадать отсутствующие runtime/genres/synopsis/status.

Следующие P9 adapters для runtime/genres/synopsis/status должны иметь собственный temporal/provenance contract и подключаться к refresh только после отдельных тестов.
