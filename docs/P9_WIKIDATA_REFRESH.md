# P9. Единый Wikidata refresh

`WikidataFutureRefresh` объединяет два уже разделённых слоя Wikidata-сбора в один атомарный P9 batch:

- `WikidataFutureEnricher` — режиссёры, сценаристы, актёры, первоисточник, франшиза и production company;
- `WikidataFutureFactsCollector` — датированные `runtime_minutes` и `genres`.

Оба collector-а получают один и тот же `retrieved_at`, отдельно кешируют raw WDQS JSON, а затем их нормализованные bundles объединяются до записи в registry.

## Зачем нужен единый batch

Если team enrichment импортировать отдельно от runtime/genres, при сетевой или валидационной ошибке можно получить половину нового состояния. Unified refresh формирует один `FutureReleaseBatchImporter` payload, поэтому все изменения либо проходят одной транзакцией, либо полностью откатываются.

При объединении bundle проверяются duplicate identity. Одинаковый `source_id`, `person_id`, `entity_id`, `link_id` или `observation_id` допускается только если payload полностью совпадает. Разные payload с одним identity считаются ошибкой и refresh не импортируется.

Также проверяется, что team и facts collectors выбрали одинаковый набор `project_id`. Если выборка расходится, atomic refresh прерывается до импорта.

## CLI

Собрать единый refresh без изменения registry:

```bash
python scripts/wikidata_future_refresh.py \
  --project-id wikidata:Q123 \
  --output /tmp/vanga-wikidata-refresh.json
```

Сразу импортировать результат:

```bash
python scripts/wikidata_future_refresh.py \
  --project-id wikidata:Q123 \
  --import
```

`--project-id` можно повторять. Без него используются проекты P9 registry в пределах `--limit`.

Для воспроизводимого historical collection можно передать явный timestamp:

```bash
python scripts/wikidata_future_refresh.py \
  --retrieved-at 2026-10-04T10:00:00Z \
  --import
```

## Сетевой контракт

Сеть используется только collector-слоем. Runtime inference не обращается к Wikidata/WDQS и читает только локальный registry/cache.

`WikidataFutureFactsCollector` использует SPARQL 1.1 без `SERVICE wikibase:label`. Существующий team enricher пока сохраняет текущий WDQS label service, поэтому полный unified refresh ещё нельзя считать полностью независимым от особенностей WDQS v1. Параметр `--endpoint` уже вынесен наружу, но перед окончательным переходом на WDQS v2/QLever team query нужно отдельно перевести на обычные `rdfs:label` bindings.

## Что намеренно не входит

Unified refresh не пытается автоматически превращать `P577` в `worldwide exact` release date. Для дат релиза нужен отдельный resolver с qualifier/territory semantics и независимым подтверждением источника.

Wikidata description также не используется как synopsis: краткое entity description не является сюжетным пересказом.

## Следующий P9 шаг

После этого слоя остаются три крупных задачи:

1. безопасный release-date collector/resolver с territory semantics;
2. второй независимый источник release-date evidence;
3. датированный настоящий synopsis-source для дальнейшего StoryMap анализа.
