# P9. Датированные pre-release факты

`FutureTemporalFactStore` хранит факты о будущем фильме, которые были реально известны к конкретному моменту времени. Слой нужен для `runtime`, `genres` и `synopsis`, чтобы future-release prediction не подменял историческое состояние сегодняшним IMDb snapshot.

## Контракт

Каждое наблюдение содержит:

- `project_id` — проект из P9 registry;
- `fact_type` — `runtime_minutes`, `genres` или `synopsis`;
- `value` — нормализованное значение;
- `known_at` — когда факт уже был известен;
- `source_id` — immutable snapshot источника из `future_release_sources`;
- `source_stream_id` — необязательный стабильный идентификатор логического потока повторных snapshot одного источника;
- `confidence` — уверенность `0..1`;
- `observation_id` — уникальное наблюдение.

Если `source_stream_id` не задан, он логически совпадает с `source_id`, поэтому старые bundles и ручные факты сохраняют прежнюю семантику.

Inference не обращается к сети. Все факты заранее сохраняются в той же DuckDB, где находится `future_releases`.

## As-of семантика

На заданный cutoff система:

1. берёт только наблюдения с `known_at <= cutoff`;
2. внутри каждого `source_stream_id` оставляет последнее наблюдение; если stream не задан — используется `source_id`;
3. группирует одинаковые значения разных логических источников;
4. если осталось одно значение — факт считается разрешённым;
5. если осталось несколько разных значений — фиксируется conflict.

Так immutable provenance не перезаписывается: ежедневные snapshot получают разные `source_id`, но могут принадлежать одному стабильному stream.

Конфликт `runtime` или `genres` блокирует автоматическую сборку `/predict` payload. Система не выбирает источник по confidence и не маскирует расхождение эвристикой.

`genres` нормализуются независимо от порядка элементов, поэтому `Drama, Sci-Fi` и `Sci-Fi, Drama` не создают ложный конфликт.

## Приоритеты при сборке prediction payload

Порядок источников остаётся явным:

### Runtime

1. explicit `runtime_override`;
2. датированный `Source Context planned_runtime`, если он уже был известен на cutoff;
3. P9 temporal fact `runtime_minutes`;
4. текущий IMDb snapshot — только при явном `allow_current_imdb_snapshot=True`.

### Genres

1. explicit `genres_override`;
2. P9 temporal fact `genres`;
3. текущий IMDb snapshot — только при явном opt-in.

### Synopsis

1. explicit `synopsis` запроса;
2. P9 temporal fact `synopsis`.

Synopsis пока не является обязательным blocker для rating prediction: он добавляется в payload, когда доступен, и используется downstream pre-release analysis.

Если датированный P9 факт заменяет значение, которое базовый builder взял из текущего IMDb snapshot, `historical_backtest_safe` пересчитывается по фактически использованным источникам.

## Ручной CLI

Добавить датированный runtime:

```bash
python scripts/future_temporal_facts.py add wikidata:Q123 \
  runtime_minutes \
  --runtime 132 \
  --known-at 2026-10-04T09:00:00Z \
  --source-id studio-page-20261004 \
  --provider "Official studio" \
  --url https://example.test/movie \
  --confidence 0.95
```

Добавить genres:

```bash
python scripts/future_temporal_facts.py add wikidata:Q123 \
  genres \
  --genre Drama \
  --genre Sci-Fi \
  --known-at 2026-10-04T09:00:00Z \
  --source-id studio-page-20261004 \
  --provider "Official studio"
```

Посмотреть состояние на исторический cutoff:

```bash
python scripts/future_temporal_facts.py show wikidata:Q123 \
  --cutoff 2026-10-05T00:00:00Z
```

После этого обычный:

```bash
python scripts/future_prediction_payload.py wikidata:Q123 \
  --cutoff 2026-10-05T00:00:00Z
```

сможет использовать датированные факты без `--allow-current-imdb-snapshot`.

## Batch import для collectors

Внешнему adapter/collector не нужно вызывать ручной CLI по одному факту. Массив `temporal_facts` является частью общего P9 bundle и импортируется через `FutureReleaseBatchImporter`.

Минимальный фрагмент bundle:

```json
{
  "sources": [
    {
      "source_id": "studio-page-20261004",
      "provider": "Official studio",
      "usage_basis": "public_record",
      "retrieved_at": "2026-10-04T09:00:00Z"
    }
  ],
  "projects": [
    {
      "project_id": "wikidata:Q123",
      "canonical_title": "Example Film"
    }
  ],
  "temporal_facts": [
    {
      "observation_id": "runtime-20261004",
      "project_id": "wikidata:Q123",
      "fact_type": "runtime_minutes",
      "value": 132,
      "known_at": "2026-10-04T09:00:00Z",
      "source_id": "studio-page-20261004",
      "confidence": 0.95
    },
    {
      "observation_id": "genres-20261004",
      "project_id": "wikidata:Q123",
      "fact_type": "genres",
      "value": ["Drama", "Sci-Fi"],
      "known_at": "2026-10-04T09:00:00Z",
      "source_id": "studio-page-20261004",
      "confidence": 0.95
    }
  ]
}
```

Если collector регулярно снимает один и тот же источник, каждый snapshot должен иметь новый immutable `source_id`, а temporal facts могут дополнительно содержать общий `source_stream_id`.

Полный collector batch импортируется обычной командой:

```bash
python scripts/future_release_import.py import collector-batch.json
```

`temporal_facts` остаётся необязательным массивом: старые v1 bundles без него продолжают приниматься.

Импорт атомарный. `sources`, `projects`, release windows, people/entities и `temporal_facts` используют одну DuckDB-транзакцию. Если хотя бы один temporal fact невалиден или ссылается на отсутствующий `project_id`/`source_id`, весь batch откатывается, включая запись в import history.

Повтор одного и того же source snapshot остаётся идемпотентным по существующему source/bundle fingerprint contract.

## Wikidata runtime/genres collector

Для проектов, у которых в P9 registry уже есть `wikidata_id`, реализован collector `WikidataFutureFactsCollector`.

Он получает:

- `runtime` из Wikidata property `P2047`;
- `genres` из `P136`;
- английский label жанра, с fallback на русский.

Collector принципиально **не** использует Wikidata description как `synopsis`: краткое описание сущности не является пересказом сюжета и не должно подменять StoryMap/text-analysis input.

Запуск и атомарный импорт:

```bash
python scripts/wikidata_future_facts.py \
  --project-id wikidata:Q123 \
  --import
```

Для нескольких проектов `--project-id` можно повторять. Без него берутся проекты из registry в пределах `--limit`.

Raw WDQS JSON кешируется локально и fingerprint входит в collector batch. Inference к сети не обращается.

### Temporal provenance

Каждый Wikidata sync создаёт собственный immutable source snapshot, например:

```text
wikidata:Q123:facts:20261004T090000Z:7e5c1a2b9d11
```

При этом temporal facts получают стабильный logical stream:

```text
wikidata:Q123:facts
```

Следующий sync создаёт другой `source_id` и новый `known_at`, но сохраняет тот же `source_stream_id`. Поэтому исходные snapshot никогда не переписываются, а as-of resolver выбирает последнее состояние Wikidata на конкретный cutoff вместо ложного конфликта между ежедневными снимками.

Если сам Wikidata snapshot одновременно содержит несколько разных значений runtime, collector не выбирает одно эвристически: runtime не импортируется, а возвращается warning `runtime_conflict_within_wikidata`.

### WDQS v1 → v2

Facts collector query написан на SPARQL 1.1 и не использует `SERVICE wikibase:label`/`bd:serviceParam`. Endpoint настраивается через `--endpoint`.

По умолчанию пока используется:

```text
https://query.wikidata.org/sparql
```

Существующий team enrichment пока использует текущий WDQS label service; детали и единый atomic refresh описаны в `docs/P9_WIKIDATA_REFRESH.md`.

Release date намеренно не импортируется этим collector-ом. `P577` может содержать несколько дат и территориальные/событийные различия; объявлять такую дату `worldwide exact` без корректного разбора qualifiers небезопасно.

## Следующие P9 инкременты

Temporal storage, batch import, Wikidata runtime/genres collector и единый atomic Wikidata refresh готовы. Дальше остаются:

- реализовать безопасный release-date adapter с qualifier/territory semantics и вторым независимым источником;
- получить датированный synopsis из источника, где действительно публикуется synopsis, а не entity description;
- публичный future-release каталог в `jsint-site`.

Production CatBoost model этими изменениями не переобучается и его feature schema не меняется.
