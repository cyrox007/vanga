# P9. Wikidata enrichment будущих релизов

`WikidataFutureEnricher` — второй Wikidata-pass после discovery collector. Discovery отвечает за identity и release windows, enrichment — за factual context уже найденных локальных проектов.

## Что собирается

Для проектов, у которых в P9 registry уже есть Wikidata QID, enrichment читает отдельные statement-level связи:

- `P57` → director;
- `P58` → writer;
- `P161` → cast member;
- `P144` → source work / `based_on`;
- `P179` → franchise/series;
- `P272` → production company.

Deprecated statements исключаются. Release date `P577` здесь намеренно не запрашивается: это зона ответственности discovery collector.

## Temporal/provenance contract

Каждая импортируемая связь получает:

- `known_at = retrieved_at` текущего enrichment-pass;
- отдельный source с provider `Wikidata`;
- URL страницы фильма;
- retrieval-specific source/link ID;
- confidence;
- canonical Wikidata ID target-сущности.

Повторный sync не переписывает прошлый `known_at`: он добавляет новое evidence. Snapshot P9 группирует повторные подтверждения одного logical person/entity и не должен раздувать команду.

Если один и тот же actor имеет несколько statements с разным billing order, enrichment выбирает минимальный видимый ordinal детерминированно и возвращает `actor_billing_order_conflict` warning.

Если Wikidata сообщает другой IMDb ID фильма, чем уже закреплён в P9 registry, enrichment **не изменяет project identity автоматически**. Возвращается `project_imdb_id_mismatch`. Это защищает historical as-of от позднего identity mutation.

## Canonical IDs

Люди внутри этого adapter имеют ID `wikidata:Q...` и сохраняют IMDb person ID (`nm...`) как дополнительный идентификатор, если он присутствует и валиден.

Entities используют kind-aware IDs:

- `wikidata:source_work:Q...`;
- `wikidata:franchise:Q...`;
- `wikidata:production_company:Q...`.

Kind входит в ID, чтобы одна Wikidata-сущность, встречающаяся в разных семантических ролях, не перезаписывала тип другой сущности.

## Pipeline

```text
future_releases.duckdb
  -> список уже найденных project_id + Wikidata QID
  -> WDQS enrichment query
  -> raw JSON cache
  -> normalization
  -> fingerprinted batch
  -> FutureReleaseBatchImporter
  -> future_releases.duckdb
  -> FuturePredictionPayloadBuilder
```

Inference не вызывает WDQS. Сетевой слой полностью отделён от prediction.

## CLI

Обогатить первые 100 проектов с Wikidata QID и только сохранить result:

```bash
python scripts/wikidata_future_enrichment.py \
  --registry-db future_releases.duckdb \
  --limit 100 \
  --output /tmp/wikidata-enrichment.json
```

Обогатить конкретные проекты и сразу импортировать в тот же registry:

```bash
python scripts/wikidata_future_enrichment.py \
  --registry-db future_releases.duckdb \
  --project-id wikidata:Q123 \
  --project-id wikidata:Q456 \
  --import
```

За один request поддерживается максимум 200 проектов. Для большого каталога collector запускается несколькими fingerprinted batches.

## Ограничения текущего pass

Этот pass пока не пытается автоматически выводить:

- shared universe;
- production label;
- producer/creative lead;
- synopsis/runtime/genres;
- production status.

Эти данные требуют отдельных доказуемых источников/контрактов. Нельзя подменять отсутствующие факты эвристикой только ради того, чтобы payload стал `prediction_ready`.
