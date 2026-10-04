# P9. Future-release discovery

P9 хранит будущие релизы в отдельной локальной DuckDB и не делает inference зависимым от внешнего API.

## База

По умолчанию используется `future_releases.duckdb`. Путь можно переопределить через `VANGA_FUTURE_RELEASE_DB`.

Registry хранит:

- проекты: canonical title, IMDb/Wikidata ID;
- aliases с `known_at`, source и confidence;
- историю release windows с territory/precision;
- историю production status;
- режиссёров, сценаристов и cast;
- source material, franchise/shared universe, studio/production company/label;
- provenance каждого факта.

## Temporal contract

Любой project fact доступен только если его `known_at <= cutoff`.

Один источник может менять release date. Для каждого source+territory as-of используется только его последнее видимое наблюдение, поэтому перенос даты не переписывает прошлую историю.

Если разные актуальные источники дают разные release windows, registry не выбирает одну дату молча:

- `release_date_conflict=true`;
- `release_window=null`;
- `release_at=null`;
- все кандидатуры возвращаются в `release_candidates[]` вместе с evidence/confidence.

Аналогично конфликт production status возвращается как набор `status_candidates[]`.

## Нормализация

Люди и production/source entities имеют стабильные canonical IDs. Повторное подтверждение той же logical связи другим source не должно удваивать человека/франшизу/студию в snapshot.

Title resolver использует только exact-normalized aliases (Unicode NFKC + casefold + punctuation normalization). Fuzzy auto-merge в registry запрещён: неоднозначный alias должен быть разрешён вручную или upstream collector'ом.

## Release precision

Release window хранит `release_start_at`, `release_end_at` и `precision`:

- `exact`;
- `month`;
- `quarter`;
- `year`;
- `window`.

`release_at` в snapshot возвращается только для `precision=exact`. Нельзя выдавать year/month window за точную дату.

## CLI

```bash
python scripts/future_releases.py import-bundle future.json
python scripts/future_releases.py snapshot project-123 --cutoff 2026-10-01T00:00:00Z
python scripts/future_releases.py catalog --cutoff 2026-10-01T00:00:00Z --from-at 2026-10-01T00:00:00Z --to-at 2028-01-01T00:00:00Z
python scripts/future_releases.py resolve-title "Название фильма" --cutoff 2026-10-01T00:00:00Z
```

Bundle может содержать массивы:

`source`, `projects`, `people`, `entities`, `aliases`, `release_windows`, `statuses`, `project_people`, `project_entities`.

Фактическое имя первого массива в CLI — `sources`.

## Граница ответственности

Этот слой — cache/registry. Он не ходит в сеть и сам не решает, какой внешний источник считать истиной. Отдельный collector/enrichment обязан:

1. получить данные;
2. записать provider/url/retrieved_at;
3. сохранить `known_at` и confidence;
4. нормализовать IDs;
5. импортировать bundle в P9 registry.

Prediction/catalog используют только локальную БД. `network_required_for_inference=false` является частью snapshot/catalog contract.

## Дальше

Следующие P9 инкременты:

- source adapters/collectors с rate limiting и cache-only output;
- cross-registry enrichment из Source Context и Production Context;
- materializer будущего проекта в pre-release prediction request;
- публичный каталог на jsint-site.
