# P9. Даты релиза и территория

`WikidataFutureReleaseCollector` получает будущие даты релиза из Wikidata `P577` на уровне statement, а не из плоского `wdt:P577`.

Это принципиально важно: у одного фильма могут одновременно существовать разные даты — фестивальная премьера, прокат в отдельных странах, стриминг и другие события. Поэтому отсутствие контекста нельзя автоматически трактовать как «мировая дата релиза».

## Что сохраняется

Collector читает:

- `P577` statement;
- `wikibase:timeValue`;
- `wikibase:timePrecision`;
- qualifier `P291` (`place of publication`), если он указан;
- IMDb id и en/ru label фильма для identity/enrichment.

Поддерживаемая временная точность:

- precision `11+` → `exact`;
- precision `10` → `month`;
- precision `9` → `year`;
- более грубая точность не импортируется и возвращается как warning.

## Territory contract

Если у release statement есть `P291=Q…`, окно сохраняется в territory:

```text
wikidata:Q…
```

Например:

```text
wikidata:Q30
```

означает ровно тот Wikidata entity, который указан qualifier-ом. Collector не пытается самовольно превращать город в страну, страну в регион или отдельную территорию в `worldwide`.

Если `P291` отсутствует, territory сохраняется как:

```text
unspecified
```

и добавляется warning:

```text
release_territory_unspecified
```

`unspecified` **не является** синонимом `worldwide`.

Поэтому стандартный:

```python
store.snapshot_as_of(project_id, cutoff)
```

который по умолчанию использует `territory="worldwide"`, не примет неуточнённый Wikidata `P577` за мировую дату релиза.

Для проверки конкретного qualifier нужно запрашивать его явно:

```python
store.snapshot_as_of(
    project_id,
    cutoff,
    territory="wikidata:Q30",
)
```

А для неуточнённого evidence:

```python
store.snapshot_as_of(
    project_id,
    cutoff,
    territory="unspecified",
)
```

## Почему нет автоматического worldwide

Отсутствие `P291` означает только отсутствие известной системе территориальной детализации. Из этого нельзя доказать, что дата относится ко всему миру.

Даже наличие `P291` не доказывает, что qualifier представляет именно страну: Wikidata entity может обозначать другую географическую или публикационную сущность. Поэтому на этом слое сохраняется QID, а более высокий resolver сможет отдельно нормализовать территориальную иерархию, когда правила будут достаточно строгими и проверяемыми.

## Point-in-time provenance

Каждый сетевой sync создаёт новый immutable `source_id`, содержащий retrieval timestamp. Старый raw snapshot и evidence не переписываются.

Это сохраняет правильный ответ на вопрос «что было известно на конкретный cutoff».

Отдельный следующий инкремент должен добавить для release windows стабильный logical `source_stream_id` — аналогично temporal facts. Это позволит новой версии той же Wikidata statement заменять старую внутри одного logical stream на позднем cutoff, сохраняя оба immutable source snapshots.

До завершения этого шага расхождение одной и той же statement между разными retrieval snapshots остаётся консервативным конфликтом, а не молча разрешается в пользу последнего значения.

## WDQS / QLever

Release discovery query теперь использует обычные SPARQL 1.1 конструкции:

- `p:P577`;
- `psv:P577`;
- `pq:P291`;
- `rdfs:label`.

`SERVICE wikibase:label` и `bd:serviceParam` не используются.

Endpoint настраивается через CLI:

```bash
python scripts/wikidata_future_releases.py \
  --from-at 2026-10-04T00:00:00Z \
  --to-at 2028-01-01T00:00:00Z \
  --endpoint https://query.wikidata.org/sparql \
  --import-db data/future_releases.duckdb
```

## Что ещё нужно до production-grade release resolver

1. `source_stream_id` для повторных snapshot одной release statement;
2. нормализация географической иерархии только по доказуемым правилам;
3. второй независимый источник release-date evidence;
4. правила для разных типов релизных событий, если источник явно их различает;
5. публичное отображение provenance/territory/conflict в каталоге будущих релизов.
