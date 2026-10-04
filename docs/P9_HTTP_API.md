# P9. Региональный HTTP API

HTTP-слой публикует уже подготовленный cache-only P9 registry и не выполняет сетевые запросы во время inference.

Существующий `POST /predict` не изменён. P9 добавляет два отдельных endpoint-а, чтобы новый региональный контракт не ломал старых клиентов.

## GET `/future/catalog`

Возвращает будущие релизы для **явно заданной ISO 3166-1 территории**.

Обязательные query-параметры:

- `cutoff` — ISO-8601 момент, на который строится temporal snapshot;
- `territory` — ISO 3166-1 alpha-2, например `DE`, `US`, `NL`.

Опционально:

- `from_at`;
- `to_at`;
- `include_conflicts=true|false`.

Пример:

```bash
curl 'http://127.0.0.1:9100/future/catalog?cutoff=2026-10-04T12:00:00Z&territory=DE'
```

Ответ содержит:

- canonical territory вида `iso3166:de`;
- `territory_policy=explicit_iso3166_no_worldwide_fallback`;
- release provenance;
- `regional_release_resolution` для каждого проекта;
- agreement/conflict между источниками;
- fingerprint каталога.

`worldwide` и `unspecified` как входная territory запрещены. Отсутствие региональной даты не подменяется глобальной датой.

## POST `/future/prediction-payload`

Собирает temporal-safe payload, который можно передать в существующий `/predict`, если `prediction_ready=true`.

Минимальный запрос:

```json
{
  "project_id": "wikidata:Q123",
  "cutoff": "2026-10-04T12:00:00Z",
  "territory": "DE"
}
```

Поддерживаются явные overrides:

```json
{
  "project_id": "wikidata:Q123",
  "cutoff": "2026-10-04T12:00:00Z",
  "territory": "DE",
  "runtime_override": 124,
  "genres_override": ["Drama", "Sci-Fi"],
  "synopsis": "Публично известный синопсис фильма"
}
```

`allow_current_imdb_snapshot` по умолчанию `false`. Недатированный текущий IMDb snapshot остаётся только явным opt-in и не считается historical/backtest-safe evidence.

### Готовый payload

При `prediction_ready=true` поле `request` содержит структуру для обычного `/predict`:

```json
{
  "prediction_ready": true,
  "target_territory": "iso3166:de",
  "blockers": [],
  "request": {
    "title": "Future Film",
    "director": "Director",
    "directors": ["Director"],
    "writer": "Writer",
    "year": 2027,
    "runtime": 124,
    "genres": ["Drama", "Sci-Fi"],
    "actors": []
  }
}
```

### Заблокированное состояние

Конфликт или недостаток данных не является HTTP-ошибкой. Endpoint возвращает `200`, но:

```json
{
  "prediction_ready": false,
  "request": null,
  "blockers": ["regional_release_date_conflict"]
}
```

Это позволяет UI отличать техническую ошибку API от корректного состояния «прогноз пока нельзя строить».

Региональные blockers:

- `regional_release_date_conflict`;
- `regional_release_date_missing`;
- `regional_exact_release_date_missing`.

Дополнительный warning `regional_release_mapping_incomplete` означает, что есть release evidence с Wikidata territory QID, но temporal-safe QID→ISO mapping ещё не доступен на заданный cutoff.

## HTTP-коды

- `200` — snapshot/payload успешно построен, включая штатный blocked-state;
- `400` — неверный формат параметров, territory, cutoff или overrides;
- `404` — неизвестный `project_id` при сборке prediction payload;
- `413` — слишком большой POST body;
- `503` — локальный P9 registry временно недоступен или произошла внутренняя ошибка.

## Граница ответственности

Эти endpoint-ы:

- не загружают CatBoost;
- не вызывают `/predict` автоматически;
- не обращаются к Wikidata/TMDb/IMDb по сети;
- читают только локальные DuckDB/cache данные;
- не выбирают `worldwide` вместо отсутствующей региональной даты.

Публичный `jsint-site` должен сначала запросить regional payload, показать пользователю territory/provenance/conflict status и вызывать `/predict` только если `prediction_ready=true`.
