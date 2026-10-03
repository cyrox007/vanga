# Production Context — Wikidata enrichment и materialization

## Назначение

Этот pipeline наполняет factual `production_context.duckdb` воспроизводимыми production identity из уже существующего Wikimedia enrichment Vanga.

Он **не** включает признаки в CatBoost и **не** присваивает студиям, продюсерам, франшизам или shared universe оценку качества.

## Архитектура

Pipeline разделён на три независимых шага:

1. `enrich.py` — существующий IMDb → Wikidata/Wikipedia enrichment. Он создаёт `film_enrichment` в `enrichment.duckdb`.
2. `production_enrich.py` — production-specific Wikidata cache. Он читает известные film QID и получает production companies, producers, series и RU/EN labels.
3. `materialize_production_context.py` — полностью офлайн переносит cached production metadata в `production_context.duckdb`.

Сетевой сбор и запись factual registry намеренно разделены. Это упрощает повторяемость, диагностику и работу на малом VPS.

## Wikidata properties

Production enrichment использует:

- `P272` — production company;
- `P162` — producer;
- `P179` — part of the series.

Для связанных QID дополнительно запрашиваются английский и русский labels.

`P179` автоматически материализуется **только как `franchise` candidate**. Из `P179` нельзя автоматически делать вывод `shared_universe`: это более сильное семантическое утверждение и требует отдельного provenance.

## Production Wikidata cache

В `enrichment.duckdb` создаётся таблица:

`production_wikidata(imdb_id, wikidata_id, metadata_json, status, error, fetched_at)`.

Первичный проход:

```bash
python production_enrich.py --max-items 100 --batch-size 10
```

Повтор ошибок:

```bash
python production_enrich.py --retry-errors --max-items 100
```

Переопрос уже известных фильмов:

```bash
python production_enrich.py --refresh-known --max-items 100 --batch-size 10
```

`--refresh-known` использует отдельный циклический cursor. Он нужен, потому что Wikidata production relations могут дополняться позже. Когда refresh доходит до конца набора, cursor сбрасывается; следующий плановый запуск начинает новый полный цикл.

Новый relation получает время нового fetch. Уже известный relation при materialization сохраняет своё первое `known_at` и первое provenance-наблюдение.

`--retry-errors` и `--refresh-known` одновременно запрещены.

## Temporal contract

Самое важное правило: текущий Wikidata snapshot нельзя использовать как доказательство того, что relation был известен много лет назад.

Поэтому:

`known_at = production_wikidata.fetched_at`

Например, если фильм вышел в 2020 году, а связь со студией впервые получили из Wikidata 3 октября 2026 года, для temporal dataset эта связь становится известной только 3 октября 2026 года. Она не backdate-ится к 2020 году.

Это консервативно, но исключает скрытую post-release leakage.

Каждый materialized fetch получает отдельный source snapshot:

`wikidata:<film_qid>:<UTC-fetch-timestamp>`

Если exact link/alias уже существует, materializer его не перепривязывает к новому snapshot. Поэтому раннее `known_at` и исходный `source_id` сохраняются. Если при refresh появляется новая relation, она получает source текущего fetch и новое `known_at`.

## Offline materialization

После production enrichment:

```bash
python materialize_production_context.py --max-items 500 --batch-size 100
```

После `production_enrich.py --refresh-known` требуется соответствующий refresh materializer:

```bash
python materialize_production_context.py --refresh-known --max-items 500 --batch-size 100
```

У materializer отдельный циклический refresh cursor. Он нужен потому, что обычный IMDb cursor уже не вернётся к ранее обработанному фильму, metadata которого обновилась. После полного refresh-pass cursor сбрасывается для следующего цикла.

Materializer создаёт или обновляет:

- production project по IMDb ID;
- отдельный provenance source snapshot каждого fetch;
- canonical production company entities;
- canonical producer entities;
- canonical franchise groups из `P179`;
- RU/EN aliases;
- project/entity links;
- project/group links.

Canonical IDs имеют вид:

- entity: `wikidata:Q...`;
- group: `wikidata:Q...`;
- source snapshot: `wikidata:<film-qid>:<UTC-fetch-timestamp>`.

Link IDs детерминированы, поэтому повторная materialization идемпотентна для уже наблюдавшихся exact relations.

## Release time

Materializer использует release dates из уже cached `film_enrichment.wikidata_json`, если `production_context` ещё не содержит release date проекта.

Historical `as-of` aggregates используют проект только если одновременно выполняются условия:

- production relation уже была известна к cutoff;
- фильм уже был выпущен к cutoff;
- фильм выпущен раньше target release limit.

Будущий фильм той же студии или франшизы не становится «историческим опытом».

## Что не автоматизируется

Этот pipeline намеренно не пытается автоматически вывести:

- shared universe из `P179`;
- creative lead;
- переписывания;
- пересъёмки;
- recut;
- consultancy scope;
- культурное/политическое влияние;
- положительное или отрицательное влияние компании/продюсера.

Такие факты требуют отдельного датированного source/provenance или отдельного исследовательского inference слоя.

## Corrections

Если Wikidata позже удаляет ошибочную relation, существующая factual запись не должна молча исчезать из исторической timeline: мы действительно наблюдали такой источник в конкретный момент.

Для исправлений нужен отдельный provenance-aware correction/supersession механизм. До его появления автоматический materializer добавляет новые наблюдаемые canonical relations и не стирает уже материализованные исторические факты.

## Связь с ML

Наличие production metadata в registry ещё не означает включение её в модель.

Перед CatBoost обязательны:

1. достаточное покрытие;
2. temporal dataset без backdate;
3. отдельный ablation каждого proxy-блока;
4. quality gate;
5. проверка нагрузки на VPS.

Rating-based «репутация студии/продюсера» здесь не рассчитывается: для неё нужна историческая кривая outcome/rating, которая относится к P7.
