# P9. Канонизация территорий release dates

Этот слой связывает разные identity namespaces территорий без переписывания исходных release observations.

## Проблема

Один и тот же регион может приходить как:

- Wikidata: `wikidata:Q30`;
- TMDb: `iso3166:US`.

В DuckDB `FutureReleaseStore` нормализует `territory` через `casefold`, поэтому фактические storage keys имеют вид `wikidata:q30` и `iso3166:us`. CLI/API могут принимать `US` или `iso3166:US`; resolver приводит их к canonical storage identity.

Сопоставлять территории по display name запрещено.

## Реализованный контракт

`FutureTerritoryStore` хранит temporal mapping:

- `source_territory` — исходная identity, например `wikidata:q30`;
- `canonical_territory` — ISO identity, например `iso3166:us`;
- `known_at` — когда mapping уже был известен системе;
- `source_id` — immutable provenance snapshot;
- `confidence`.

Исходная строка `future_release_windows` не изменяется. Канонизация выполняется только при чтении на заданный cutoff.

Это означает, что mapping, полученный 5 октября, не может автоматически появиться в snapshot на 1 октября.

## Wikidata → ISO collector

`WikidataTerritoryCollector` находит QID, уже встреченные в `future_release_windows`, и получает ISO 3166-1 alpha-2 через Wikidata property `P297`.

Query использует обычный SPARQL и не зависит от `SERVICE wikibase:label`.

Если один Wikidata item одновременно даёт несколько разных ISO code, mapping не выбирается эвристически и возвращается warning `iso_code_conflict_within_wikidata`.

Запуск:

```bash
python scripts/wikidata_territories.py --apply
```

Явный QID:

```bash
python scripts/wikidata_territories.py \
  --qid Q30 \
  --qid Q145 \
  --apply
```

Показ canonical release snapshot:

```bash
python scripts/wikidata_territories.py \
  --apply \
  --show-project wikidata:Q123 \
  --territory US
```

## Cross-source agreement/conflict

`release_snapshot_as_of(project_id, cutoff, canonical_territory=...)`:

1. читает все release observations, известные к cutoff;
2. оставляет ISO territory как уже canonical;
3. Wikidata QID разрешает только через mapping, видимый к cutoff;
4. unresolved mapping не угадывает;
5. группирует одинаковые release windows разных источников;
6. одно уникальное окно означает resolved agreement;
7. несколько разных окон означают explicit conflict.

Таким образом Wikidata `wikidata:q30` и TMDb `iso3166:us` могут стать доказательствами одного canonical US release window только после фактического QID→ISO mapping.

## Правила безопасности

1. `worldwide` — отдельное логическое состояние, а не fallback для отсутствующего qualifier.
2. `unspecified` не равно `worldwide` и не канонизируется в страну.
3. Название страны не используется как identity evidence.
4. Неизвестный Wikidata QID остаётся unresolved.
5. Конфликт mapping-источников не разрешается confidence-эвристикой.
6. Inference не обращается к сети; mapping и release windows читаются из локальной DuckDB.

## Следующий шаг

Canonical resolver уже умеет сравнивать Wikidata и TMDb на одной территории. Следующий P9-инкремент — подключить canonical regional release snapshot к future prediction/catalog contract и определить policy выбора целевой территории для публичного прогноза без подмены её `worldwide`.
